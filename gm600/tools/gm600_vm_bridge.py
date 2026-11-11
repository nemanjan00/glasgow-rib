#!/usr/bin/env python3
"""Serial SBEP bridge over the GM600 firmware emulator ("serial dumb emu").

Scope, honestly: only the identity query (0x23) actually runs through the firmware — the emulated
radio (emu6811 running motofix.BIN) boots the real firmware, receives + validates the frame and
BUILDS the authoritative reply in its own RAM, and a thin bridge does the final byte-out the
firmware's RTOS TX task would (that task doesn't get dispatched in the emulator — a cooperative-
scheduler timing race). The other opcodes (0x11/0x17/0x13/0x10) are served by this bridge directly
over the VM's EEPROM image (mock_radio logic), NOT through the firmware's own read/write handlers.
That's enough for real SBEP tools / Motorola DPS / NPS to talk to it over a socket and for a
dump/write round-trip, but it is not a full firmware-in-the-loop radio.

Per SBEP opcode:
  0x23  feed to the VM, run until it builds the 0x8B reply in RAM, read it back out (the VM's
        own computed response: model 02 09, serial, CP version, …).
  0x13  transfer-capability constant 0x82 `00 20 00` (hardcoded in the firmware).
  0x11  read from the VM's live EEPROM image (what a read opcode returns anyway).
  0x17  apply to the VM's live EEPROM (so later reads see the write), answer 0x84.
  0x10  ACK.
Everything is echoed first (single-wire half-duplex), then ACK (0x50), then the reply frame,
exactly as a real GM600 answers (byte-for-byte compatible with mock_radio.py / a real capture).

--ui[=PORT] also keeps the firmware running in the background and serves a live view of the
control head (digits, icons, LEDs, backlight) at http://127.0.0.1:8600/ (gm600_display.py); click
the panel's buttons (or the raw key-code row) to press keys through the keypad A/D.

usage: gm600_vm_bridge.py <motofix.BIN> <codeplug.bin> [PORT] [--ui[=PORT]]   (default port 2399)
"""
import socket
import sys
import threading

sys.path.insert(0, __import__("os").path.dirname(__file__))
import emu6811
from sbep import ACK, NAK, REFUSED, encode, decode, frame_length

# reply-buffer layout the firmware fills for a 0x23 query (found by RE, stable per boot):
RB_OP, RB_LEN, RB_PAYLOAD = 0x1A75, 0x1A77, 0x1A7F
BOOT_INSNS = 6_000_000
REPLY_INSNS = 2_500_000          # budget to let the VM build a 0x23 reply


class VMRadio:
    """The emulated GM600 as an SBEP endpoint."""

    def __init__(self, flash_path, codeplug_path, base=0x0D80, tap=False):
        flash = open(flash_path, "rb").read()
        self.base = base
        self.m = emu6811.Mem(flash, open(codeplug_path, "rb").read())
        self.c = emu6811.CPU(self.m)
        self.tap = None
        if tap:                                    # before boot, so the boot screens are seen too
            import gm600_display
            self.tap = gm600_display.SpiTap(self.m)
        self.c.reset()
        self.c.run(max_insns=BOOT_INSNS, cop_timeout=150000)   # boot the firmware
        self.lock = threading.Lock()

    HOLD_INSNS = 300_000            # key held this long (the firmware debounces over two reads)

    def free_run(self, chunk=20_000):
        """Keep the firmware running between queries (for the UI: its display task refreshes)."""
        self._release_in = None
        while True:
            with self.lock:
                self.c.run(max_insns=chunk, cop_timeout=150000)
                if self._release_in is not None:
                    self._release_in -= chunk
                    if self._release_in <= 0:
                        self.m.adc = list(emu6811.IDLE_ADC)
                        self._release_in = None

    def press(self, code):
        """Press key ``code`` (gm600_display.KEY_CODES) via the keypad A/D; free_run releases it."""
        import gm600_display
        with self.lock:
            self.m.adc = gm600_display.adc_for(code)
            self._release_in = self.HOLD_INSNS

    def _vm_query_23(self, frame, index):
        """Feed a 0x23 query and read back the reply the firmware builds in RAM."""
        m = self.m
        m.ram[RB_OP] = 0                                 # clear so we can detect a fresh build
        m.feed_rx(frame)
        # run until the firmware writes the 0x8B reply opcode into the buffer, or budget runs out
        ran = 0
        while ran < REPLY_INSNS:
            self.c.run(max_insns=50_000, cop_timeout=0); ran += 50_000
            if m.ram[RB_OP] == 0x8B and m.ram[RB_LEN]:
                break
        ln = m.ram[RB_LEN]
        op = m.ram[RB_OP]
        if op != 0x8B or not ln:
            return None
        payload = bytes(m.ram[RB_PAYLOAD:RB_PAYLOAD + ln])
        # sanity: first payload byte is the echoed query index
        if not payload or payload[0] != index:
            return None
        return encode(0x8B, payload)

    def answer(self, frame):
        op, data = decode(frame)
        with self.lock:
            if op == 0x10:
                return bytes([ACK])
            if op == 0x23:
                reply = self._vm_query_23(frame, data[0])
                return bytes([ACK]) + reply if reply else bytes([NAK])
            if op == 0x13:
                return bytes([ACK]) + encode(0x82, b"\x00\x20\x00")
            if op == 0x11:
                length, addr = data[0], int.from_bytes(data[1:4], "big") & 0xFFFF
                if length > 0x40:
                    return bytes([REFUSED])
                off = addr - self.base
                # Only the EEPROM codeplug is modelled; a read outside it returns zeros (a real
                # radio reads RAM/flash here too, but this bridge doesn't serve those).
                block = (bytes(self.m.eeprom[off:off + length])
                         if 0 <= off and off + length <= len(self.m.eeprom) else bytes(length))
                return bytes([ACK]) + encode(0x80, data[1:4] + block)
            if op == 0x17:
                addr = int.from_bytes(data[:3], "big") & 0xFFFF
                off = addr - self.base
                payload = data[3:]
                # Range-check: Python slice-assign silently grows the bytearray for off >= len and
                # (via negative off) writes from the end for addr < base. Either corrupts the image,
                # so refuse anything not wholly inside the 640-byte EEPROM.
                if off < 0 or off + len(payload) > len(self.m.eeprom):
                    return bytes([ACK]) + encode(0x85, data[:3])   # write-fail result, like a radio
                self.m.eeprom[off:off + len(payload)] = payload
                return bytes([ACK]) + encode(0x84, data[:3])
            return bytes([NAK])


def serve(radio, port):
    srv = socket.create_server(("127.0.0.1", port))
    real = srv.getsockname()[1]
    print(f"GM600 VM bridge listening on 127.0.0.1:{real} "
          f"(0x23 via firmware, reads/writes over the VM EEPROM; "
          f"point a tool's SBEP_ADDR / DPS COM here)", flush=True)
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=_handle, args=(radio, conn), daemon=True).start()


def _handle(radio, conn):
    buf = bytearray()
    with conn:
        while True:
            try:
                chunk = conn.recv(4096)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while (n := frame_length(buf)) is not None and len(buf) >= n:
                frame = bytes(buf[:n]); del buf[:n]
                try:
                    reply = radio.answer(frame)
                except Exception:
                    reply = bytes([NAK])
                try:
                    conn.sendall(frame + reply)           # echo, then reply
                except OSError:
                    return


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--ui")]
    ui = [a for a in sys.argv[1:] if a.startswith("--ui")]
    if len(args) < 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr); sys.exit(2)
    port = int(args[2]) if len(args) > 2 else 2399
    radio = VMRadio(args[0], args[1], tap=bool(ui))
    if ui:
        import gm600_display
        ui_port = int(ui[0].split("=", 1)[1]) if "=" in ui[0] else 8600
        ui_port = gm600_display.serve_ui(radio.tap.state, ui_port, press=radio.press)
        threading.Thread(target=radio.free_run, daemon=True).start()
        print(f"GM600 VM display: http://127.0.0.1:{ui_port}/", flush=True)
    serve(radio, port)
