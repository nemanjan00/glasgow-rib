"""A 68HC11 core emulator — foundation for running GM600 firmware in software.

Run firmware routines (and eventually boot) to trace the display render, find variables by
watching memory, and test codeplug writes without the bench. CPU is fairly complete (ALU x all
addressing modes, 16-bit ops, memory RMW, bit ops, Y/CPD prefixes, branches, stack, calls).
Peripherals modelled: SPI/SCI capture, timers + output-compare/RTI interrupts (the RTOS ticks),
the A/D (keypad), and the external AT29C020 flash write path (PA6+pin-12 /OE gate, SDP program,
product-ID) so the flashtool write sequence can be validated in software.

Memory: 0x0000-0x3FFF RAM; 0x1000-0x107F registers (0x1058=bank, SPI/SCI captured);
0x0D80-0x0FFF internal EEPROM; 0x4000-0x7FFF banked flash (page=reg0x58>>2); 0x8000-0xFFFF fixed.
"""
from collections import deque

S, X_, H, I, N, Z, V, C = 0x80, 0x40, 0x20, 0x10, 0x08, 0x04, 0x02, 0x01

# SCI registers — this is a 68HC11K4: SCI block is at 0x1070-0x1077, NOT the classic 0x102B map.
#   0x1072 SCCR1, 0x1073 SCCR2, 0x1074 SCSR1, 0x1075 SCSR2, 0x1077 SCDR.
SCCR2 = 0x73                                  # register offset (0x1073)
SCSR  = 0x74                                  # register offset (0x1074)
SCSR_TDRE, SCSR_RDRF, SCSR_IDLE = 0x80, 0x20, 0x10
SCCR2_TIE, SCCR2_RIE, SCCR2_ILIE = 0x80, 0x20, 0x10
# RX is interrupt-driven: RIE is set in idle, so every received byte raises the SCI interrupt
# (vector 0xFFD6 -> 0x9336), whose ISR reads exactly that one byte. Bytes must therefore be
# delivered one at a time, paced ~1 byte-time apart; re-reading SCDR returns the same byte.
RX_BYTE_INSNS = 250                           # ~instructions per byte at the emulated bitrate.
# The firmware arms an inter-byte OC5 timeout of ~3.5 byte-times (table @0x92E2) when it advances
# the reception state machine; bytes must arrive within it, so pacing must be < that timeout and
# > one state-handler's run (~80 insns, so re-reads of SCDR in a handler see the same byte).


class Mem:
    def __init__(self, flash=None, eeprom=None):
        self.ram = bytearray(0x4000)
        self.flash = bytearray(flash) if flash else bytearray(0x40000)
        self.eeprom = bytearray(eeprom or b"\xff" * 0x280)
        self.reg = bytearray(0x80)
        self.reg[0x3F] = 0xD9              # CONFIG (non-volatile on HC11) = the GM600's value;
        #                                   without it the firmware endlessly rewrites CONFIG and
        #                                   watchdog-resets to make it take effect (found via the debugger)
        self.spi_out = bytearray()
        self.sci_out = bytearray()
        self.sci_rx_byte = 0
        self.rx_queue = deque()              # pending SCI RX bytes (a BUS+ frame from the host)
        self.rx_timer = 0                    # instructions until the next queued byte is delivered
        self.watch = None
        self.tcnt = 0                      # free-running timer (TCNT @ 0x100E)
        self.tcnt_prev = 0
        self.cop_arm = False               # COP watchdog kick sequence (0x55 then 0xAA -> 0x103A)
        self.cop_kicks = 0
        self.adc = [0xFF, 0xFF, 0xFF, 0xFF]  # ADR1-4 (keypad idle = 5V = 0xFF, no key)
        self.spi_xfers = []                  # list of (cs_value, bytes) grouped by chip-select
        self._cur_xfer = None
        self.force_oc1 = False               # debug: force OC1 interrupt enabled (tick), see docs
        # External AT29C020 flash write model. In stock operation the flash /OE is held low (read
        # only): writes are ignored. ~12V on accessory pin-12 releases the gate so /OE follows PA6
        # (PORTA bit6); with PA6 high a CPU write pulses /WE and reaches the chip. Set pin12_12v to
        # model the 12V being applied. See docs/FLASH_WRITE.md.
        self.pin12_12v = False
        self._fl_seq = []                    # recent (A0..A14 cmd addr, data) writes, for 3-cycle commands
        self._fl_id = False                  # AT29C020 software product-ID mode
        self._fl_load = None                 # pending sector program: {byte_offset: value}
        self._fl_sector = None               # base chip address of the sector being loaded

    def tick(self, n=4):
        self.tcnt = (self.tcnt + n) & 0xFFFF

    def feed_rx(self, data):
        """Queue a BUS+ frame for the SCI receiver.

        Reception is interrupt-driven: RIE is set, so each byte that arrives raises the SCI
        interrupt (0xFFD6 -> ISR 0x9336), and the ISR reads exactly that one byte, advancing a
        reception state machine (RAM byte_30). Bytes are therefore delivered one at a time, paced
        ~1 byte-time apart by pump_rx(); the queue is NOT drained by SCDR reads (re-reading SCDR
        returns the same latched byte, as on real hardware)."""
        self.rx_queue.extend(data)
        if not (self.reg[SCSR] & SCSR_RDRF):
            self.rx_timer = 1                 # deliver the first byte promptly

    def pump_rx(self):
        """Per-instruction: deliver the next queued byte when its byte-time elapses."""
        if self.rx_timer > 0:
            self.rx_timer -= 1
            if self.rx_timer == 0 and self.rx_queue and not (self.reg[SCSR] & SCSR_RDRF):
                self.sci_rx_byte = self.rx_queue.popleft()
                self.reg[SCSR] |= SCSR_RDRF
                self.rx_timer = RX_BYTE_INSNS if self.rx_queue else 0

    def sci_rx(self, byte):
        """Feed a single RX byte (low-level helper; prefer feed_rx for whole frames)."""
        self.feed_rx(bytes([byte & 0xFF]))

    def rb(self, a):
        a &= 0xFFFF
        if a < 0x1000:
            return self.eeprom[a - 0x0D80] if 0x0D80 <= a < 0x1000 else self.ram[a]
        if a < 0x1080:
            o = a - 0x1000
            if o == 0x0E: return (self.tcnt >> 8) & 0xFF      # TCNT high
            if o == 0x0F: return self.tcnt & 0xFF             # TCNT low
            if o == SCSR:                                     # the emulated transmitter is always ready:
                return self.reg[SCSR] | SCSR_TDRE | 0x40      # report TDRE + TC set (plus live RX flags)
            if o == 0x77:                                     # reading SCDR returns the RX latch and
                self.reg[SCSR] &= ~(SCSR_RDRF | SCSR_IDLE)    # clears RDRF+IDLE; the byte stays latched
                return self.sci_rx_byte                       # (re-reads return the same byte)
            return self.reg[o]
        if a < 0x4000:
            return self.ram[a]
        return self._flash_read(self._chip_addr(a))

    def wb(self, a, v):
        a &= 0xFFFF; v &= 0xFF
        if self.watch:
            self.watch(a, v)
        if a < 0x1000:
            if 0x0D80 <= a < 0x1000:
                self.eeprom[a - 0x0D80] = v
            else:
                self.ram[a] = v
            return
        if a < 0x1080:
            o = a - 0x1000
            self.reg[o] = v
            if o == 0x2A:
                self.spi_out.append(v); self.reg[0x29] |= 0x80
                if self._cur_xfer is None:
                    self._cur_xfer = (self.reg[0x28], bytearray()); self.spi_xfers.append(self._cur_xfer)
                self._cur_xfer[1].append(v)
            elif o == 0x28:                    # port D / chip-selects: CS change ends a transfer
                self._cur_xfer = None
            elif o == 0x77:
                self.sci_out.append(v); self.reg[SCSR] |= SCSR_TDRE   # transmit + TDRE
                # Single-wire half-duplex bus: a transmitted byte is echoed back on RX. This echo is
                # how the firmware CLOCKS a multi-byte reply — each TX-byte state writes SCDR, the
                # echo raises RDRF -> SCI interrupt -> the ISR consumes the echo and runs the next
                # TX state. Loop every transmitted byte back, as long as nothing is inbound (the host
                # isn't mid-frame); a host frame in rx_queue takes precedence over our own echo.
                if not self.rx_queue:
                    self.sci_rx_byte = v
                    self.reg[SCSR] |= SCSR_RDRF
            elif o == 0x3A:                    # COPRST: 0x55 then 0xAA kicks the watchdog
                if v == 0x55: self.cop_arm = True
                elif v == 0xAA and self.cop_arm: self.cop_kicks += 1; self.cop_arm = False
                else: self.cop_arm = False
            elif o == 0x30:                    # ADCTL write starts a conversion -> instantly complete
                self.reg[0x30] = v | 0x80      # set CCF (conversions complete flag)
                self.reg[0x31:0x35] = bytes(self.adc)   # ADR1-4 results
            elif o in (0x23, 0x25):            # TFLG1/TFLG2: writing a 1 CLEARS that flag
                self.reg[o] = (self.reg[o] & ~v) & 0xFF
            return
        if a < 0x4000:
            self.ram[a] = v
            return
        # 0x4000-0xFFFF: external flash. Writes land only when the /OE write-lock is released
        # (pin-12 ~12V AND PA6 high); otherwise ignored, exactly as the real board behaves.
        if self.pin12_12v and (self.reg[0x00] & 0x40):
            self._flash_write(self._chip_addr(a), v)

    # ---- AT29C020 flash write model (see __init__) ----
    def _chip_addr(self, a):
        """CPU address -> 18-bit flash chip address (banked window or fixed region)."""
        if a < 0x8000:
            return ((self.reg[0x58] >> 2) * 0x4000) + (a - 0x4000)
        return 0x38000 + (a - 0x8000)

    def _flash_write(self, chip, v):
        # Models SDP ENABLED: a write only takes effect as part of a recognised command sequence
        # (the AA/55/A0 program prefix, or ID entry/exit); a bare write with no prefix is ignored.
        # The chip's factory SDP state is unknown, but the stub always sends the prefix, which works
        # either way, and leaves SDP on. (No sdp_enabled=False path is modelled.)
        ca = chip & 0x7FFF                               # AT29C020 commands decode only A0-A14
        self._fl_seq.append((ca, v)); self._fl_seq = self._fl_seq[-3:]
        s = self._fl_seq
        if len(s) == 3 and s[0] == (0x5555, 0xAA) and s[1] == (0x2AAA, 0x55) and s[2][0] == 0x5555:
            x = s[2][1]
            if x == 0x90:                                # product-ID entry
                self._fl_id = True
            elif x == 0xF0:                              # product-ID exit
                self._fl_id = False
            elif x == 0xA0:                              # SDP program prefix -> begin sector load
                self._fl_load = {}; self._fl_sector = None
            self._fl_seq = []
            return
        if self._fl_load is not None:                    # sector data load
            if self._fl_sector is None:
                self._fl_sector = chip & ~0xFF
            if (chip & ~0xFF) == self._fl_sector:
                self._fl_load[chip & 0xFF] = v

    def _flash_read(self, chip):
        if self._fl_load is not None and self._fl_sector is not None:
            sect = bytearray(b"\xFF" * 0x100)            # sector auto-erases, then loaded bytes program
            for off, val in self._fl_load.items():
                sect[off] = val
            self.flash[self._fl_sector:self._fl_sector + 0x100] = sect
            self._fl_load = None; self._fl_sector = None
        if self._fl_id:
            if chip in (0x0002, 0x3FFF2):
                return 0xFE                              # boot-block lockout: both unlocked
            return 0x1F if (chip & 1) == 0 else 0xDA     # manufacturer / device code
        return self.flash[chip]

    def rw(self, a):
        return (self.rb(a) << 8) | self.rb(a + 1)

    def ww(self, a, v):
        self.wb(a, (v >> 8) & 0xFF); self.wb(a + 1, v & 0xFF)


class CPU:
    def __init__(self, mem):
        self.m = mem
        self.A = self.B = 0
        self.X = self.Y = 0
        self.SP = 0x01FF
        self.PC = 0
        self.CC = S | X_ | I
        self.ireg = 'X'           # which index register the current (possibly prefixed) op uses
        self.insn_pc = 0          # start PC of the instruction currently executing (for the debugger)

    @property
    def D(self): return (self.A << 8) | self.B
    @D.setter
    def D(self, v): self.A = (v >> 8) & 0xFF; self.B = v & 0xFF

    def ir(self): return self.X if self.ireg == 'X' else self.Y
    def set_ir(self, v):
        if self.ireg == 'X': self.X = v & 0xFFFF
        else: self.Y = v & 0xFFFF

    def sf(self, b, on): self.CC = (self.CC | b) if on else (self.CC & ~b)
    def gf(self, b): return bool(self.CC & b)
    def nz8(self, v): self.sf(N, v & 0x80); self.sf(Z, (v & 0xFF) == 0)
    def nz16(self, v): self.sf(N, v & 0x8000); self.sf(Z, (v & 0xFFFF) == 0)

    def fb(self):
        v = self.m.rb(self.PC); self.PC = (self.PC + 1) & 0xFFFF; return v
    def fw(self):
        return (self.fb() << 8) | self.fb()

    def push(self, v): self.m.wb(self.SP, v & 0xFF); self.SP = (self.SP - 1) & 0xFFFF
    def pull(self): self.SP = (self.SP + 1) & 0xFFFF; return self.m.rb(self.SP)
    def pushw(self, v): self.push(v & 0xFF); self.push((v >> 8) & 0xFF)
    def pullw(self): hi = self.pull(); lo = self.pull(); return (hi << 8) | lo

    # addressing-mode effective address
    def ea(self, mode):
        if mode == 'd': return self.fb()
        if mode == 'e': return self.fw()
        if mode == 'i': return (self.ir() + self.fb()) & 0xFFFF
        raise ValueError(mode)

    def add8(self, a, b, cin=0):
        r = a + b + cin
        self.sf(H, ((a & 0xF) + (b & 0xF) + cin) > 0xF)
        self.sf(C, r > 0xFF); self.sf(V, ((a ^ r) & (b ^ r) & 0x80) != 0)
        r &= 0xFF; self.nz8(r); return r

    def sub8(self, a, b, bin=0):
        r = a - b - bin
        self.sf(C, r < 0); self.sf(V, ((a ^ b) & (a ^ (r & 0xFF)) & 0x80) != 0)
        r &= 0xFF; self.nz8(r); return r

    def reset(self):
        """Hardware/COP reset: re-vector, set S/X/I, clear bank; RAM is battery-backed (preserved)."""
        self.PC = self.m.rw(0xFFFE)
        self.CC = S | X_ | I
        self.m.reg[0x58] = 0

    def irq(self, vector):
        """Take an interrupt: stack PC,Y,X,A,B,CC and vector."""
        self.pushw(self.PC); self.pushw(self.Y); self.pushw(self.X)
        self.push(self.A); self.push(self.B); self.push(self.CC)
        self.sf(I, True)
        self.PC = self.m.rw(vector)

    def _update_timer_flags(self):
        """Set OC compare flags on TCNT crossings (and TOF on overflow)."""
        m = self.m; r = m.reg
        last, now = m.tcnt_prev, m.tcnt; m.tcnt_prev = now
        for bit, toco in ((0x80, 0x16), (0x40, 0x18), (0x20, 0x1A), (0x10, 0x1C), (0x08, 0x1E)):
            toc = (r[toco] << 8) | r[toco + 1]
            if (last < toc <= now) if last <= now else (toc > last or toc <= now):
                r[0x23] |= bit
        if now < last:
            r[0x25] |= 0x80

    def _pending_vector(self):
        """The vector of the highest-priority enabled+flagged timer interrupt, or None."""
        r = self.m.reg
        tmsk1 = r[0x22] | (0x80 if self.m.force_oc1 else 0)
        for bit, vec in ((0x80, 0xFFE8), (0x40, 0xFFE6), (0x20, 0xFFE4), (0x10, 0xFFE2), (0x08, 0xFFE0)):
            if tmsk1 & r[0x23] & bit:
                return vec
        if r[0x24] & r[0x25] & 0x80:
            return 0xFFDE
        # SCI: receive (RIE & RDRF) or idle-line (ILIE & IDLE) -> SCI vector 0xFFD6
        if (r[SCCR2] & SCCR2_RIE and r[SCSR] & SCSR_RDRF) or \
           (r[SCCR2] & SCCR2_ILIE and r[SCSR] & SCSR_IDLE):
            return 0xFFD6
        return None

    def _timer_irq(self):
        """Per-instruction: update timer flags and service a timer interrupt if one is pending.

        This is what makes the firmware's RTOS tick: the scheduler runs on an output-compare ISR."""
        self._update_timer_flags()
        if self.gf(I):
            return
        v = self._pending_vector()
        if v is not None:
            self.irq(v)

    def run(self, max_insns=5_000_000, stop_pc=None, trace=None, cop_timeout=0, max_resets=30):
        resets = 0; since_kick = 0; last_kicks = self.m.cop_kicks
        for _ in range(max_insns):
            if stop_pc is not None and self.PC == stop_pc:
                return 'stop'
            if cop_timeout:
                if self.m.cop_kicks != last_kicks:
                    last_kicks = self.m.cop_kicks; since_kick = 0
                else:
                    since_kick += 1
                    if since_kick > cop_timeout:
                        resets += 1; since_kick = 0
                        if resets > max_resets:
                            return 'cop_loop'
                        self.reset()
                        continue
            if trace: trace(self)
            self.insn_pc = self.PC
            self.ireg = 'X'
            op = self.fb()
            if op == 0x18:                      # Y prefix
                self.ireg = 'Y'; op = self.fb()
            elif op == 0x1A:                    # CPD / cross prefix (page 3)
                op = self.fb()
                if op == 0x83: self._cpd(self.fw()); continue
                if op == 0x93: self._cpd(self.m.rw(self.ea('d'))); continue
                if op == 0xA3: self._cpd(self.m.rw(self.ea('i'))); continue
                if op == 0xB3: self._cpd(self.m.rw(self.ea('e'))); continue
                if op == 0xEE: self.Y = self.m.rw((self.X + self.fb()) & 0xFFFF); self.nz16(self.Y); continue
                if op == 0xEF: self.m.ww((self.X + self.fb()) & 0xFFFF, self.Y); self.nz16(self.Y); continue
                if op == 0xAC:                               # CPY idx,X
                    v = self.m.rw((self.X + self.fb()) & 0xFFFF)
                    self.sf(C, self.Y < v); self.nz16((self.Y - v) & 0xFFFF)
                    self.sf(V, ((self.Y ^ v) & (self.Y ^ ((self.Y - v) & 0xFFFF)) & 0x8000) != 0)
                    continue
                raise NotImplementedError("0x1A%02X @0x%04X" % (op, self.PC))
            elif op == 0xCD:                    # page 4 (index cross)
                op = self.fb()
                if op == 0xEE: self.X = self.m.rw((self.Y + self.fb()) & 0xFFFF); self.nz16(self.X); continue
                if op == 0xEF: self.m.ww((self.Y + self.fb()) & 0xFFFF, self.X); self.nz16(self.X); continue
                if op == 0xA3: self._cpd(self.m.rw((self.Y + self.fb()) & 0xFFFF)); continue
                if op == 0xAC: v = self.m.rw((self.Y + self.fb()) & 0xFFFF); self.sf(C, self.X < v); self.nz16((self.X - v) & 0xFFFF); continue
                raise NotImplementedError("0xCD%02X @0x%04X" % (op, self.PC))
            h = OPC.get(op)
            if h is None:
                raise NotImplementedError("0x%02X @0x%04X (bank 0x%02X)" % (op, (self.PC - 1) & 0xFFFF, self.m.reg[0x58]))
            h(self)
            self.m.tick()
            self.m.pump_rx()
            self._timer_irq()
        return 'max'

    def _cpd(self, v):
        r = self.D - v
        self.sf(C, self.D < v); self.sf(V, ((self.D ^ v) & (self.D ^ (r & 0xFFFF)) & 0x8000) != 0)
        self.nz16(r & 0xFFFF)


OPC = {}
def op(code):
    def d(fn): OPC[code] = fn; return fn
    return d


# ---------- generated ALU ops across addressing modes ----------
def _rd(c, mode):
    return c.m.rb(c.ea(mode))

def _alu_reg(reg, base, fn16=None):
    # reg='A'/'B'; base immediate opcode; generates imm/dir/idx/ext (+0x00/0x10/0x20/0x30 from 0x8x row)
    pass

def _mk(reg):
    def get(c): return c.A if reg == 'A' else c.B
    def put(c, v):
        if reg == 'A': c.A = v & 0xFF
        else: c.B = v & 0xFF
    return get, put

for _reg, _hi in (('A', 0x80), ('B', 0xC0)):
    g, p = _mk(_reg)
    def mkops(g=g, p=p, hi=_hi):
        def load(mode):
            def h(c): v = (c.fb() if mode == 'm' else _rd(c, mode)); p(c, v); c.nz8(v); c.sf(V, False)
            return h
        def store(mode):
            def h(c): ea = c.ea(mode); v = g(c); c.m.wb(ea, v); c.nz8(v); c.sf(V, False)
            return h
        def alu(name):
            def mk(mode):
                def h(c):
                    v = (c.fb() if mode == 'm' else _rd(c, mode)); a = g(c)
                    if name == 'add': p(c, c.add8(a, v))
                    elif name == 'adc': p(c, c.add8(a, v, 1 if c.gf(C) else 0))
                    elif name == 'sub': p(c, c.sub8(a, v))
                    elif name == 'sbc': p(c, c.sub8(a, v, 1 if c.gf(C) else 0))
                    elif name == 'cmp': c.sub8(a, v)
                    elif name == 'and': r = a & v; p(c, r); c.nz8(r); c.sf(V, False)
                    elif name == 'ora': r = a | v; p(c, r); c.nz8(r); c.sf(V, False)
                    elif name == 'eor': r = a ^ v; p(c, r); c.nz8(r); c.sf(V, False)
                    elif name == 'bit': r = a & v; c.nz8(r); c.sf(V, False)
                return h
            return mk
        # opcode layout within a row: imm=hi+6, dir=hi+0x16-0x10.., use HC11 standard map:
        OPC[hi + 0x06] = load('m')          # LDAA/LDAB immediate (86/C6)
        OPC[hi + 0x16] = load('d')          # 96 / D6
        OPC[hi + 0x26] = load('i')          # A6 / E6
        OPC[hi + 0x36] = load('e')          # B6 / F6
        OPC[hi + 0x17] = store('d')         # 97 / D7
        OPC[hi + 0x27] = store('i')         # A7 / E7
        OPC[hi + 0x37] = store('e')         # B7 / F7
        for nm, lowimm in (('sub', 0x00), ('cmp', 0x01), ('sbc', 0x02), ('and', 0x04),
                           ('bit', 0x05), ('ora', 0x0A), ('eor', 0x08), ('adc', 0x09),
                           ('add', 0x0B)):
            mk = alu(nm)
            OPC[hi + lowimm] = mk('m')
            OPC[hi + 0x10 + lowimm] = mk('d')
            OPC[hi + 0x20 + lowimm] = mk('i')
            OPC[hi + 0x30 + lowimm] = mk('e')
    mkops()


# ---------- 16-bit loads/stores (D, X/Y via ireg, SP) ----------
def _ld16(which):
    def mk(mode):
        def h(c):
            v = (c.fw() if mode == 'm' else c.m.rw(c.ea(mode)))
            if which == 'D': c.D = v
            elif which == 'IR': c.set_ir(v)
            else: c.SP = v
            c.nz16(v); c.sf(V, False)
        return h
    return mk
def _st16(which):
    def mk(mode):
        def h(c):
            ea = c.ea(mode); v = (c.D if which == 'D' else (c.ir() if which == 'IR' else c.SP))
            c.m.ww(ea, v); c.nz16(v); c.sf(V, False)
        return h
    return mk
ld = _ld16('D'); st = _st16('D')
OPC[0xCC] = ld('m'); OPC[0xDC] = ld('d'); OPC[0xEC] = ld('i'); OPC[0xFC] = ld('e')
OPC[0xDD] = st('d'); OPC[0xED] = st('i'); OPC[0xFD] = st('e')
ld = _ld16('IR'); st = _st16('IR')
OPC[0xCE] = ld('m'); OPC[0xDE] = ld('d'); OPC[0xEE] = ld('i'); OPC[0xFE] = ld('e')
OPC[0xDF] = st('d'); OPC[0xEF] = st('i'); OPC[0xFF] = st('e')
ld = _ld16('SP'); st = _st16('SP')
OPC[0x8E] = ld('m'); OPC[0x9E] = ld('d'); OPC[0xAE] = ld('i'); OPC[0xBE] = ld('e')
OPC[0x9F] = st('d'); OPC[0xAF] = st('i'); OPC[0xBF] = st('e')


# ---------- 16-bit arithmetic ----------
def _subd(mode):
    def h(c):
        v = (c.fw() if mode == 'm' else c.m.rw(c.ea(mode))); r = c.D - v
        c.sf(C, r < 0); c.sf(V, ((c.D ^ v) & (c.D ^ (r & 0xFFFF)) & 0x8000) != 0)
        c.D = r & 0xFFFF; c.nz16(c.D)
    return h
def _addd(mode):
    def h(c):
        v = (c.fw() if mode == 'm' else c.m.rw(c.ea(mode))); r = c.D + v
        c.sf(C, r > 0xFFFF); c.D = r & 0xFFFF; c.nz16(c.D)
    return h
OPC[0x83] = _subd('m'); OPC[0x93] = _subd('d'); OPC[0xA3] = _subd('i'); OPC[0xB3] = _subd('e')
OPC[0xC3] = _addd('m'); OPC[0xD3] = _addd('d'); OPC[0xE3] = _addd('i'); OPC[0xF3] = _addd('e')
def _cpx(mode):
    def h(c):
        v = (c.fw() if mode == 'm' else c.m.rw(c.ea(mode)))
        c.sf(C, c.ir() < v); c.sf(V, ((c.ir() ^ v) & (c.ir() ^ ((c.ir() - v) & 0xFFFF)) & 0x8000) != 0)
        c.nz16((c.ir() - v) & 0xFFFF)
    return h
OPC[0x8C] = _cpx('m'); OPC[0x9C] = _cpx('d'); OPC[0xAC] = _cpx('i'); OPC[0xBC] = _cpx('e')


# ---------- memory read-modify-write (ext/idx) ----------
def _rmw(name):
    def mk(mode):
        def h(c):
            ea = c.ea(mode); v = c.m.rb(ea)
            if name == 'inc': r = (v + 1) & 0xFF; c.nz8(r); c.sf(V, v == 0x7F)
            elif name == 'dec': r = (v - 1) & 0xFF; c.nz8(r); c.sf(V, v == 0x80)
            elif name == 'clr': r = 0; c.nz8(0); c.sf(V, False); c.sf(C, False)
            elif name == 'com': r = (~v) & 0xFF; c.nz8(r); c.sf(V, False); c.sf(C, True)
            elif name == 'neg': r = (-v) & 0xFF; c.nz8(r); c.sf(V, v == 0x80); c.sf(C, v != 0)
            elif name == 'tst': c.nz8(v); c.sf(V, False); c.sf(C, False); return
            elif name == 'asl': c.sf(C, bool(v & 0x80)); r = (v << 1) & 0xFF; c.nz8(r); c.sf(V, c.gf(N) ^ c.gf(C))
            elif name == 'lsr': c.sf(C, bool(v & 1)); r = v >> 1; c.nz8(r); c.sf(V, c.gf(N) ^ c.gf(C))
            elif name == 'asr': c.sf(C, bool(v & 1)); r = ((v >> 1) | (v & 0x80)) & 0xFF; c.nz8(r); c.sf(V, c.gf(N) ^ c.gf(C))
            elif name == 'rol': nc = bool(v & 0x80); r = ((v << 1) | (1 if c.gf(C) else 0)) & 0xFF; c.sf(C, nc); c.nz8(r); c.sf(V, c.gf(N) ^ c.gf(C))
            elif name == 'ror': nc = bool(v & 1); r = ((v >> 1) | (0x80 if c.gf(C) else 0)) & 0xFF; c.sf(C, nc); c.nz8(r); c.sf(V, c.gf(N) ^ c.gf(C))
            c.m.wb(ea, r)
        return h
    return mk
for nm, lo in (('neg', 0x00), ('com', 0x03), ('lsr', 0x04), ('ror', 0x06), ('asr', 0x07),
               ('asl', 0x08), ('rol', 0x09), ('dec', 0x0A), ('inc', 0x0C), ('tst', 0x0D), ('clr', 0x0F)):
    OPC[0x60 + lo] = _rmw(nm)('i')
    OPC[0x70 + lo] = _rmw(nm)('e')
# accumulator RMW (A: 0x4x, B: 0x5x)
def _rmw_acc(name, reg):
    def h(c):
        v = c.A if reg == 'A' else c.B
        fn = _rmw(name)('d')  # reuse logic via a temp cell is messy; inline:
        # inline compute:
        if name == 'inc': r = (v + 1) & 0xFF; c.nz8(r); c.sf(V, v == 0x7F)
        elif name == 'dec': r = (v - 1) & 0xFF; c.nz8(r); c.sf(V, v == 0x80)
        elif name == 'clr': r = 0; c.nz8(0); c.sf(V, False); c.sf(C, False)
        elif name == 'com': r = (~v) & 0xFF; c.nz8(r); c.sf(V, False); c.sf(C, True)
        elif name == 'neg': r = (-v) & 0xFF; c.nz8(r); c.sf(V, v == 0x80); c.sf(C, v != 0)
        elif name == 'tst': c.nz8(v); c.sf(V, False); c.sf(C, False); return
        elif name == 'asl': c.sf(C, bool(v & 0x80)); r = (v << 1) & 0xFF; c.nz8(r); c.sf(V, c.gf(N) ^ c.gf(C))
        elif name == 'lsr': c.sf(C, bool(v & 1)); r = v >> 1; c.nz8(r); c.sf(V, c.gf(N) ^ c.gf(C))
        elif name == 'asr': c.sf(C, bool(v & 1)); r = ((v >> 1) | (v & 0x80)) & 0xFF; c.nz8(r); c.sf(V, c.gf(N) ^ c.gf(C))
        elif name == 'rol': nc = bool(v & 0x80); r = ((v << 1) | (1 if c.gf(C) else 0)) & 0xFF; c.sf(C, nc); c.nz8(r); c.sf(V, c.gf(N) ^ c.gf(C))
        elif name == 'ror': nc = bool(v & 1); r = ((v >> 1) | (0x80 if c.gf(C) else 0)) & 0xFF; c.sf(C, nc); c.nz8(r); c.sf(V, c.gf(N) ^ c.gf(C))
        if reg == 'A': c.A = r
        else: c.B = r
    return h
for nm, lo in (('neg', 0x00), ('com', 0x03), ('lsr', 0x04), ('ror', 0x06), ('asr', 0x07),
               ('asl', 0x08), ('rol', 0x09), ('dec', 0x0A), ('inc', 0x0C), ('tst', 0x0D), ('clr', 0x0F)):
    OPC[0x40 + lo] = _rmw_acc(nm, 'A')
    OPC[0x50 + lo] = _rmw_acc(nm, 'B')


# ---------- bit ops bset/bclr/brset/brclr (dir + idx) ----------
def _bset(mode, clear):
    def h(c):
        ea = c.ea(mode); mask = c.fb(); v = c.m.rb(ea)
        v = (v & ~mask) if clear else (v | mask)
        c.m.wb(ea, v & 0xFF); c.nz8(v & 0xFF); c.sf(V, False)
    return h
OPC[0x14] = _bset('d', False); OPC[0x15] = _bset('d', True)
OPC[0x1C] = _bset('i', False); OPC[0x1D] = _bset('i', True)
def _brbit(mode, setcond):
    def h(c):
        ea = c.ea(mode); mask = c.fb(); rel = c.fb()
        if rel & 0x80: rel -= 0x100
        v = c.m.rb(ea); masked = v & mask
        take = (masked == mask) if setcond else (masked == 0)
        if take: c.PC = (c.PC + rel) & 0xFFFF
    return h
OPC[0x12] = _brbit('d', True); OPC[0x13] = _brbit('d', False)
OPC[0x1E] = _brbit('i', True); OPC[0x1F] = _brbit('i', False)


# ---------- inherent / register ----------
@op(0x01)
def _(c): pass
@op(0x0F)
def _(c): c.sf(I, True)
@op(0x0E)
def _(c): c.sf(I, False)
@op(0x0D)
def _(c): c.sf(C, True)
@op(0x0C)
def _(c): c.sf(C, False)
@op(0x0A)
def _(c): c.sf(V, True)
@op(0x0B)
def _(c): c.sf(V, False)
@op(0x08)
def _(c): c.set_ir((c.ir() + 1) & 0xFFFF); c.sf(Z, c.ir() == 0)
@op(0x09)
def _(c): c.set_ir((c.ir() - 1) & 0xFFFF); c.sf(Z, c.ir() == 0)
@op(0x31)
def _(c): c.SP = (c.SP + 1) & 0xFFFF
@op(0x34)
def _(c): c.SP = (c.SP - 1) & 0xFFFF
@op(0x30)
def _tsx(c): c.set_ir((c.SP + 1) & 0xFFFF)
@op(0x35)
def _txs(c): c.SP = (c.ir() - 1) & 0xFFFF
@op(0x3A)
def _abx(c): c.set_ir((c.ir() + c.B) & 0xFFFF)
@op(0x8F)
def _xgdx(c): d = c.D; c.D = c.ir(); c.set_ir(d)
@op(0x16)
def _(c): c.B = c.A; c.nz8(c.B); c.sf(V, False)
@op(0x17)
def _(c): c.A = c.B; c.nz8(c.A); c.sf(V, False)
@op(0x19)
def _daa(c):
    a = c.A; corr = 0; cf = c.gf(C)
    if (a & 0xF) > 9 or c.gf(H): corr |= 0x06
    if a > 0x99 or cf: corr |= 0x60
    r = (a + corr) & 0xFF; c.sf(C, cf or (a + corr) > 0xFF); c.A = r; c.nz8(r)
@op(0x3D)
def _mul(c): r = c.A * c.B; c.D = r & 0xFFFF; c.sf(C, bool(r & 0x80))
@op(0x04)
def _lsrd(c): c.sf(C, bool(c.D & 1)); c.D = (c.D >> 1) & 0xFFFF; c.nz16(c.D); c.sf(V, c.gf(N) ^ c.gf(C))
@op(0x05)
def _lsld(c): c.sf(C, bool(c.D & 0x8000)); c.D = (c.D << 1) & 0xFFFF; c.nz16(c.D); c.sf(V, c.gf(N) ^ c.gf(C))
@op(0x02)
def _idiv(c):
    if c.X == 0: c.sf(C, True); return
    q = c.D // c.X; r = c.D % c.X; c.X = q & 0xFFFF; c.D = r & 0xFFFF
    c.sf(Z, c.X == 0); c.sf(C, False); c.sf(V, False)
@op(0x03)
def _fdiv(c):
    if c.X == 0 or c.D >= c.X: c.sf(C, True); c.sf(V, c.D >= c.X); return
    q = (c.D << 16) // c.X; r = (c.D << 16) % c.X; c.X = q & 0xFFFF; c.D = r & 0xFFFF
    c.sf(Z, c.X == 0); c.sf(C, False)


# accumulator inherent shifts/clears already covered by 0x4x/0x5x table


# ---------- stack / transfers ----------
@op(0x36)
def _(c): c.push(c.A)
@op(0x32)
def _(c): c.A = c.pull()
@op(0x37)
def _(c): c.push(c.B)
@op(0x33)
def _(c): c.B = c.pull()
@op(0x3C)
def _(c): c.pushw(c.ir())
@op(0x38)
def _(c): c.set_ir(c.pullw())
@op(0x06)
def _tap(c): c.CC = c.A | S | X_   # S,X stay set-ish; keep simple
@op(0x07)
def _tpa(c): c.A = c.CC


# ---------- branches ----------
def _br(cond):
    def h(c):
        rel = c.fb()
        if rel & 0x80: rel -= 0x100
        if cond(c): c.PC = (c.PC + rel) & 0xFFFF
    return h
OPC[0x20] = _br(lambda c: True)
OPC[0x21] = _br(lambda c: False)
OPC[0x22] = _br(lambda c: not (c.gf(C) or c.gf(Z)))
OPC[0x23] = _br(lambda c: c.gf(C) or c.gf(Z))
OPC[0x24] = _br(lambda c: not c.gf(C))
OPC[0x25] = _br(lambda c: c.gf(C))
OPC[0x26] = _br(lambda c: not c.gf(Z))
OPC[0x27] = _br(lambda c: c.gf(Z))
OPC[0x28] = _br(lambda c: not c.gf(V))
OPC[0x29] = _br(lambda c: c.gf(V))
OPC[0x2A] = _br(lambda c: not c.gf(N))
OPC[0x2B] = _br(lambda c: c.gf(N))
OPC[0x2C] = _br(lambda c: not (c.gf(N) ^ c.gf(V)))
OPC[0x2D] = _br(lambda c: c.gf(N) ^ c.gf(V))
OPC[0x2E] = _br(lambda c: not (c.gf(Z) or (c.gf(N) ^ c.gf(V))))
OPC[0x2F] = _br(lambda c: c.gf(Z) or (c.gf(N) ^ c.gf(V)))


# ---------- jumps / calls / returns ----------
@op(0x7E)
def _(c): c.PC = c.ea('e')
@op(0x6E)
def _(c): c.PC = c.ea('i')
@op(0xBD)
def _(c): ea = c.ea('e'); c.pushw(c.PC); c.PC = ea
@op(0xAD)
def _(c): ea = c.ea('i'); c.pushw(c.PC); c.PC = ea
@op(0x9D)
def _(c): ea = c.ea('d'); c.pushw(c.PC); c.PC = ea
@op(0x8D)
def _(c):
    rel = c.fb()
    if rel & 0x80: rel -= 0x100
    c.pushw(c.PC); c.PC = (c.PC + rel) & 0xFFFF
@op(0x39)
def _(c): c.PC = c.pullw()
@op(0x3B)
def _rti(c):
    c.CC = c.pull(); c.B = c.pull(); c.A = c.pull()
    c.X = c.pullw(); c.Y = c.pullw(); c.PC = c.pullw()
@op(0x3F)
def _swi(c): raise NotImplementedError("SWI @0x%04X" % c.PC)
@op(0x3E)
def _wai(c):
    # WAI stacks the registers once, then halts until an unmasked interrupt vectors in.
    c.pushw(c.PC); c.pushw(c.Y); c.pushw(c.X); c.push(c.A); c.push(c.B); c.push(c.CC)
    for _ in range(2_000_000):
        c.m.tick(); c._update_timer_flags()
        if not c.gf(I):
            v = c._pending_vector()
            if v is not None:
                c.sf(I, True); c.PC = c.m.rw(v); return
    raise RuntimeError("WAI with no interrupt (deadlock) @0x%04X" % c.PC)
@op(0x00)
def _test(c): raise NotImplementedError("TEST/illegal 0x00 @0x%04X" % c.PC)


class _Stop(Exception):
    pass


class Debugger:
    """A debugger interface for the emulator: breakpoints, memory watchpoints, stepping, and
    the handy `find_writer` (what instruction writes an address — e.g. the RTOS tick counter).

        dbg = Debugger(cpu)
        dbg.find_writer(0x000C)          # -> [(pc, addr, value), ...] that wrote $0C
        dbg.bp(0x8540); dbg.cont()       # run to a breakpoint
        print(dbg.state())               # registers + flags + disassembly at PC
    """

    def __init__(self, cpu):
        self.cpu = cpu
        self.m = cpu.m
        self.breakpoints = set()
        self.watch_w = {}                 # addr -> list of (insn_pc, addr, value)
        self.stopped = None
        self.m.watch = self._on_write

    def _on_write(self, addr, v):
        if addr in self.watch_w:
            self.watch_w[addr].append((self.cpu.insn_pc, addr, v))
            self.stopped = ('watch', addr)

    def bp(self, addr):
        self.breakpoints.add(addr & 0xFFFF)

    def watchpoint(self, addr):
        self.watch_w.setdefault(addr & 0xFFFF, [])

    def cont(self, limit=5_000_000, cop_timeout=150000):
        """Run until a breakpoint or watchpoint fires (or `limit`). Returns the stop reason."""
        self.stopped = None
        bps = self.breakpoints

        def tr(c):
            if self.stopped:
                raise _Stop()
            if c.PC in bps:
                self.stopped = ('bp', c.PC)
                raise _Stop()
        try:
            self.cpu.run(max_insns=limit, cop_timeout=cop_timeout, trace=tr)
        except _Stop:
            pass
        return self.stopped

    def find_writer(self, addr, limit=5_000_000, cop_timeout=150000, hits=1):
        """Run until `addr` is written `hits` times; return the (insn_pc, addr, value) list."""
        self.watch_w[addr & 0xFFFF] = []
        self.stopped = None

        def tr(c):
            if len(self.watch_w[addr & 0xFFFF]) >= hits:
                raise _Stop()
        try:
            self.cpu.run(max_insns=limit, cop_timeout=cop_timeout, trace=tr)
        except _Stop:
            pass
        rec = self.watch_w.pop(addr & 0xFFFF)
        return rec

    def step(self, n=1):
        for _ in range(n):
            self.cpu.run(max_insns=1, cop_timeout=0)

    def state(self):
        c = self.cpu
        flags = "".join(k for k, b in (("S", S), ("X", X_), ("H", H), ("I", I),
                                       ("N", N), ("Z", Z), ("V", V), ("C", C)) if c.CC & b)
        s = ("PC=%04X A=%02X B=%02X X=%04X Y=%04X SP=%04X CC=%s bank=%02X" %
             (c.PC, c.A, c.B, c.X, c.Y, c.SP, flags or "-", self.m.reg[0x58]))
        try:
            from capstone import Cs, CS_ARCH_M680X, CS_MODE_M680X_6811
            md = Cs(CS_ARCH_M680X, CS_MODE_M680X_6811)
            code = bytes(self.m.rb(c.PC + i) for i in range(16))
            dis = next(md.disasm(code, c.PC), None)
            if dis:
                s += "\n  %04X: %s %s" % (dis.address, dis.mnemonic, dis.op_str)
        except ImportError:
            pass
        return s


def run_selftest():
    m = Mem()
    prog = bytes([0x86, 0x05, 0xC6, 0x07, 0x3D, 0xFD, 0x20, 0x00, 0xCE, 0x00, 0x0A, 0x20, 0xFE])
    m.ram[0x100:0x100 + len(prog)] = prog
    c = CPU(m); c.PC = 0x0100
    c.run(stop_pc=0x0100 + len(prog) - 2)
    ok = m.rw(0x2000) == 35 and c.X == 10
    print("selftest: 5*7=%d X=%d -> %s" % (m.rw(0x2000), c.X, "OK" if ok else "FAIL"))
    return ok


def boot(flash_path, eeprom_path=None, insns=8_000_000, cop_timeout=150000):
    """Load the GM600 firmware (256 KB flash + optional 640 B EEPROM) and boot it.

    Returns the CPU after it reaches its main loop. The firmware's init uses deliberate
    COP watchdog resets (RAM is battery-backed), which `cop_timeout` emulates. SPI/SCI output
    is captured in mem.spi_xfers / mem.sci_out."""
    flash = bytearray(open(flash_path, "rb").read())
    ee = open(eeprom_path, "rb").read() if eeprom_path else None
    m = Mem(flash=flash, eeprom=ee)
    c = CPU(m)
    c.PC = m.rw(0xFFFE)
    c.run(max_insns=insns, cop_timeout=cop_timeout)
    return c


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        run_selftest()
        print("usage: emu6811.py <flash.bin> [eeprom.bin]   # boot the firmware, dump display SPI")
        sys.exit(0)
    c = boot(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
    m = c.m
    print("booted: PC=0x%04X  COP kicks=%d  SPI bytes=%d  SCI bytes=%d" %
          (c.PC, m.cop_kicks, len(m.spi_out), len(m.sci_out)))
    import collections
    seen = collections.Counter((cs, bytes(b)) for cs, b in m.spi_xfers)
    print("distinct LCD/SPI transfers (chip-select, length, bytes):")
    for (cs, b), n in sorted(seen.items(), key=lambda kv: -kv[1]):
        print("  cs=0x%02X len=%2d x%-4d  %s" % (cs, len(b), n, b.hex()))
