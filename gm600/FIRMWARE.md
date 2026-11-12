# GM600 firmware notes

Static analysis of a GM600 firmware image read out over BUS+ with the `11` command. This is
background for the protocol: it explains *why* the radio behaves the way [PROTOCOL.md](PROTOCOL.md)
records, and it settles some of that file's open questions. Nothing here needs a radio to verify;
it all comes from the dump.

## The dumps (`docs/`, not in git)

| File | What |
|---|---|
| `gm600-807IYA0903-2026-11-03-cpu64k.bin` | The full 64 KB CPU logical address space, read with `11`. |
| `gm600-807IYA0903-2026-11-03.bin` | The first 4 KB (low RAM + internal EEPROM). Matches the 64 KB dump except for ~19 bytes of live RAM (`0x000C`–`0x028A`) that changed between reads. |

`807IYA0903` is the radio's serial number (from `23 01`). Firmware version `R03.03.21`, copyright
string "Copyright 1992, Motorola, Inc." at `0x803C`.

These are a snapshot of one radio's RAM/ROM as the CPU sees it, **not** a dump of the flash chip.
The flash is banked (below), so this view shows only the one bank that happened to be mapped in,
plus RAM, registers and the internal EEPROM.

## CPU and memory

The controller CPU is a **Motorola 68HC11 K-series** part. On this board revision (**PCB
8402626Y08**) the parts list gives U0101 = `5195136A02` ("MICROPROCESSOR"), schematic-labelled
"PROC350 PLAT S/W R010000 A3"; the `5195136A01` "S/W MSKD 68HC11K4" is the earlier Y07 list. Either way it has the
K-series memory-management unit (bank register at `0x1058`, MMU control registers around offset
`0x55`–`0x5F`) and 640 bytes of internal EEPROM. The 64 KB logical map seen through `11`:

> **No internal mask ROM is active — the firmware is 100 % external flash.** Even if the die is the
> masked K4, its on-chip ROM is disabled here: `0x8000`–`0xFFFF` read back **byte-for-byte identical
> to the off-board flash chip** (`motofix.BIN` pages 14/15), and the reset vector at `0xFFFE` points
> into that external code — if on-chip ROM were mapped there, the CPU's view would differ. `CONFIG`
> (`0x103F`) reads `0xD9` (EEON set, internal EEPROM enabled). So the radio runs as a K1-equivalent:
> internal RAM + EEPROM, all code external. The only on-chip non-volatile store is the 640-byte
> EEPROM codeplug (`dump_codeplug.py`, or `ramdump.py --with-eeprom`).

| Range | Contents |
|---|---|
| `0000`–`02FF` | Internal RAM (768 bytes) |
| `0D80`–`0FFF` | **Internal EEPROM, 640 bytes** — the whole codeplug: alignment, network and personality blocks ([CODEPLUG.md](CODEPLUG.md)) |
| `1000`–`107F` | **I/O register block** — bank select `0x1058`, SCI data `0x1077`, free-running timer `0x100E`. Values here change between reads because they are live registers, not RAM. |
| `1080`–`3FFF` | RAM / working data structures (external SRAM U0103 = **MB84256A**, 32K×8, confirmed) |
| `4000`–`7FFF` | **Banked flash window** (see below) |
| `8000`–`DAFF` | Fixed firmware bank (dispatcher, SCI driver, trampoline, vectors) |
| `DB00`–`FFBF` | `FF` (unmapped in this bank) |
| `FFC0`–`FFFF` | Interrupt vectors |

The register block sits at `0x1000`, not `0x0000` (the 68HC11 register block is relocatable; this
one is placed at `0x1000`). That is why `0x1058` and `0x1077` are registers and why the `0x1000`
page changed between reads.

### Flash banking

The radio has a 256 KB flash (U0102) behind a 64 KB CPU. It is paged into the
`4000`–`7FFF` window:

> **The flash is an Atmel AT29C020, not a 28F020.** Seen on this unit: a TSOP-32 marked
> `AT29C020 12TI`, on board `8402626Y08` (© 1997). The parts list / other board revisions may show a
> 28F020. **Reading is identical** — both are 256 KB over a standard JEDEC parallel interface, so a
> programmer dump works unchanged. **Writing differs:** the 28F020 is Intel-style
> and needs 12 V on VPP; the AT29C020 is **5 V-only**, writes in 256-byte pages behind a
> software-data-protection unlock — **no VPP to supply**, so flashing is a simpler problem on this unit
> (still untried). A TSOP-32 adapter on a T48/TL866 reads it; in-circuit reading is awkward (it fights
> the HC11 on the bus, and a Glasgow lacks pins for 18 address + 8 data + 3 control).

- **Bank select register = `0x1058`.** Writing it changes which 16 KB flash page appears at
  `4000`.
- The flash is **16 × 16 KB pages**. Pages 0–11 are populated program banks (bank-register value =
  `page << 2`, i.e. `00 04 08 0C 10 14 18 1C 20 24 28 2C`), pages **12–13 are erased** (free space),
  and pages **14–15 are the fixed `8000`–`FFFF` region**. (An earlier single-bank view wrongly
  listed `0x18`/page 6 as skipped — the full off-board dump shows it is populated; see the full-chip
  section below.) `0x1058 = 0` also selects the register/RAM page for some accesses; the reset init
  writes it last.
- Far calls into a banked routine go through a **trampoline at `0x87B5`**: it saves the current
  `0x1058`, sets bank `2`, reads a 3-byte `[bank, addr_hi, addr_lo]` descriptor, switches to the
  target bank, `jsr`s, then restores the old bank. A table of 504 fixed call stubs starts at
  `0x87D9` (each `jsr $87B5`). The trampoline adds `0x4CAC` to its **return address** (the stub +
  3), so each descriptor is at `stub + 3 + 0x4CAC`. (At `stub + 0x4CAC` the first descriptor would
  decode as bank `0x7E`, which is impossible; with `+3` every bank is a multiple of 4.)

Consequence for the protocol: none for the codeplug. It is the internal EEPROM, which is always
in the map, so plain `11` reads reach it (confirmed by a DPS capture). Banking only matters for
dumping the firmware itself.

## SCI (the BUS+ UART)

- SCI data register `0x1077`, status/control around `0x1074`. The byte-level driver is in bank
  `04` (`~0x44DF`–`0x49A4`).
- Reset initialises the SCI and related registers from a table at `0xD437` (`reg, value` pairs,
  terminated by the first value ≥ `0x80`): baud/format regs plus the K-series
  expanded MMU/SCI set.

## SBEP command dispatch — the authoritative command list

The command handler is a **jump table at `0x4019` in bank `04`**, indexed by the SBEP opcode
(entry = `0x4019 + 2*opcode`, big-endian, bank-04 address). Any opcode whose entry is `0x8236`
falls through to a shared "unsupported" stub. The populated entries:

| Opcode | Handler | Meaning |
|---|---|---|
| `10` | `4CD5`→`9BCA` | Enter programming mode (deliberate hang → watchdog reset) |
| `11` | `4D85` | Read block |
| `13` | `4C8E` | Query max block size |
| `17` | `4CE8` | Write block |
| `18` | `4E49` | **Self-test / verify** (read-only) — not in PROTOCOL.md |
| `19` | `4F30` | Query (shared message-post path) |
| `20` | `4F3F` | Parameter read / softpot write |
| `21` | `4F30` | Query (shared message-post path) |
| `22` | `4F30` | Query (shared message-post path) |
| `23` | `4F30` | Read ID string (shared message-post path) |
| `26` | `4CDC` | **Jump to an address taken from the request — factory backdoor** — not in PROTOCOL.md |
| `2D` | `8B8A` | unbanked handler |
| `2E` | `8B87` | unbanked handler |

Any opcode whose table entry is `0x8236` (e.g. `12`, `14`–`16`, `1A`–`1F`, `24`, `25`, `27`+)
falls through to a shared "unsupported" stub and gets no useful reply.

### Handler details

- **`10` enter programming (`4CD5` → `9BCA`).** The routine is just `sei; bra self` — a tight
  infinite loop with interrupts off. The COP watchdog then times out and resets the CPU, which
  comes back up in programming mode. This is exactly the "ACK, then the radio resets" behaviour in
  PROTOCOL.md: the reset is intentional, not a crash.

- **`11` read (`4D85`).** Reply opcode `0x80`. Copies the request's 3 address bytes straight into
  the reply header, then appends the data (see open-question answers below).

- **`13` max block size (`4C8E`).** Returns a **hardcoded constant `0x20`**, formatted as a 3-byte
  `82` reply — this is the observed `00 20 00`. It is not computed from anything (RAM, model,
  etc.), so treat the `82` payload as a fixed advertised value, not a hard limit; 64-byte reads
  work in practice.

- **`17` write (`4CE8`).** Reply `0x84` (ok) / `0x85` (fail). Same `0D80`–`0FFF` → internal-EEPROM
  special-case as the read path.

- **`18` self-test (`4E49`).** Sub-command in the first data byte: `0` reads and status-checks a
  small fixed region; `1` runs a checksum/compare over a region. Returns a status byte. Read-only,
  but non-essential — skip it unless you want the self-test.

- **`19`/`21`/`22`/`23` query (`4F30`).** All four post a message (via `8377`, the inter-task
  message primitive that also drives the SCI) to a task that formats the reply. `23`'s string
  reply (`8B`) is built in that task, which lives in a **flash bank not present in this dump** —
  that's why no `ldab #$8B` appears in bank 04. The sub-selection between these four opcodes
  happens in the task, not in this handler.

- **`20` parameter (`4F3F`).** If the second data byte is `0` it is a **read**: reply `8A` echoing
  the parameter index plus its value. If it is `1` or `2` it is a **write** (two flavours, via
  `8B72`), matching the RAM-vs-permanent guess in PROTOCOL.md.

- **`26` backdoor (`4CDC`).** `sei`, load a 16-bit pointer from the request data, `jmp` through it.
  This executes arbitrary code at an address the host supplies. **Never send `26` blind** — a
  wrong pointer runs garbage with interrupts disabled. It is, however, a clean way to dump the
  other flash banks if you ever want to (point it at a small stub, or use it to drive the bank
  register), but only with a known-good routine.

Findings worth carrying into PROTOCOL.md:

1. **Opcodes `18` (self-test) and `26` (arbitrary jump) exist** beyond what DPS sends. `18` is
   safe to probe read-only; `26` is a backdoor — document it, don't fire it.
2. **`19`, `21`, `22`, `23` share the query entry point `4F30`** (proven from the dispatch table
   and the handler, which posts a message rather than taking the write path). This suggests
   `21`/`22`/`19` are query sub-variants rather than destructive writes — but the reply is
   formatted by a task in a flash bank not in this dump, so their *final* behaviour is not proven
   here. Treat "21 is not a destructive write" as likely, not confirmed, until a DPS capture or a
   dump of the other banks settles it.
3. **`13`'s advertised size is a constant**, so don't treat `00 20 00` as the real transfer
   ceiling.

## Answers to PROTOCOL.md open questions

From the `11` read handler at `4D85` and the `17` write handler at `4CE8`:

- **The `80` read-reply 3-byte header is the address, echoed as sent** (`addr_hi, addr_mid,
  addr_lo`). The handler copies request bytes 20/21/22 (the 3 address bytes) straight into the
  reply header, then appends the data. It is *not* `[len, addr_hi, addr_mid]`.
- **Only the low 16 bits of the address are used.** `addr_hi` is copied into the reply header but
  ignored for addressing, which is why reads wrap every 64 KB.
- **`0D80`–`0FFF` is special-cased.** Both the read and write handlers do a range check
  (`addr − 0x0D80`, then `addr − 0x1000`); addresses in that window are served from / written to
  the CPU's internal EEPROM, everything else from the normal memory read/write path. That window
  is the radio's non-volatile store: the whole codeplug ([CODEPLUG.md](CODEPLUG.md)).

Still open: what `21` does (DPS never sends it to a GM600), and the exact behaviour of `18`,
`20` and `22`.

## Where the codeplug is: the internal EEPROM

The whole codeplug is the 640-byte internal EEPROM at `0D80`–`0FFF`, and it is in this dump. A
live DPS read (20 × 32-byte `11` reads of exactly that range) matched it byte for byte. The `.per`
file DPS saves (`0x7F1` bytes) is DPS's own expanded format, built from those 640 bytes; no such
region exists in the radio. The decode is in [CODEPLUG.md](CODEPLUG.md).

`1080`–`3FFF` is RAM with pointer tables and working structures, not the stored codeplug.

## Limitation: only one flash bank is in this dump

The `11` dump is the CPU's 64 KB *logical* view, so it contains **only the flash bank that was
mapped at `4000`–`7FFF` when it was read** (bank `04`), plus the fixed `8000`+ region, RAM and the
EEPROM. The other banked pages, including the query-reply formatter and most of the application
code, are not here.

To get the rest you either:

- **Read the flash chip** (U0102, the AT29C020) with a programmer — the complete, reliable route
  (this is what the full-chip dump below used); or
- **Run a RAM stub launched via `0x26`** that selects each bank, copies `4000`–`7FFF` out over the
  SCI, and (since you can just power-cycle afterwards) need not restore anything or return. A naïve
  bank-walk over BUS+ — writing `0x1058` with a `17` and reading `4000`–`7FFF` — **does not work:**
  the `11`/`17` handlers themselves run from the banked window (bank `04`), so changing the bank
  pulls the running handler out from under the CPU and it crashes. See
  [DUMP_SEQUENCE.md](DUMP_SEQUENCE.md). **Implemented in [tools/ramdump.py](tools/ramdump.py)
  (`ramdump.S` + `asm6811.py`) and verified in-circuit on `807IYA0903`: its 256 KB output is
  byte-for-byte identical (same SHA-256) to the off-board `motofix.BIN`, all 16 pages.**

## Full-chip dump — all 16 banks (2026-11-06)

The whole 256 KB flash (U0102, AT29C020) has since been read **off-board** (XGECU T48) — route 1
above — lifting the single-bank limitation. Full byte-level analysis lives in the motorola-cps repo
(`docs/GM600.md`); the reversing results that change or extend this doc:

- **16 × 16 KB pages.** Banked page `p` at CPU `0x4000`–`0x7FFF` is `file = cpu − 0x4000 + p·0x4000`
  (bank reg `p<<2`); the fixed region `0x8000`–`0xFFFF` is pages 14–15 (`file = cpu + 0x30000`),
  vectors at the very top (`file 0x3FFC0`+). Reset code at `0xD45A`, register-init table at `0xD437`,
  version `R03.03.21` at `0xCDC5`.
- **Page 6 (bank reg `0x18`) is populated** — correcting the "0x18 is unused" note above; the
  single-bank list simply never mapped it in.
- **`0x2D`/`0x2E` are banked after all.** Their dispatch entries (`0x8B8A`/`0x8B87`) point into the
  far-call stub table → **bank `0x10` (page 4), CPU `0x6E14`/`0x6D24`** — real code (purpose TBD),
  not the "unbanked handler" guessed from one bank.
- **No firmware flash-write path.** The dispatch table ends at `0x2E`; the AT29C020
  software-data-protection unlock (`AA→0x5555`, `55→0x2AAA`) appears **nowhere** in the 256 KB; the
  `0x17` handler writes internal EEPROM for `0x0D80`–`0x0FFF` and is a plain memcpy (`0xCE79`) for
  everything else — so SBEP `0x17` cannot reprogram flash (the chip's SDP eats it). A field reflash
  must run from RAM: upload a stub via `0x17`, launch it via `0x26`, and have it do the SDP unlock +
  256-byte page program itself. Erased pages 12/13 (`file 0x30000`–`0x37FFF`) are free patch space.
- **The control head is on the CPU's SPI bus, not BUS+.** SPI transfer routine `0x9102`; device
  dispatcher `0x8F1F` jumps through a word table at `0x8F44`. **Entry 10 → `0x8F64`** pushes one
  byte from RAM shadow **`0x34AE`** to the front-panel **LED** shift register (so
  `ldab #10; jsr $8F1F` = "update LEDs"); **entry 8** sends the 21-byte **LCD** buffer from `0x37AC`.
  - **LED shift-register bit map** (byte at `0x34AE`), verified on the real radio by driving each bit
    from a RAM stub (`gm600py/stubs/led_cmd.S` + `tools/lab/led_demo.py`) and watching the panel over a webcam
    (per-bit photos: [images/led_bitmap.jpg](images/led_bitmap.jpg); all LEDs on:
    [images/front_panel_all_leds.jpg](images/front_panel_all_leds.jpg)):

    | bit | mask | front-panel element |
    |-----|------|---------------------|
    | 0 | `0x01` | **amber** status LED (middle of the left-column stack) |
    | 1 | `0x02` | **red** status LED (top) |
    | 2 | `0x04` | **green** status LED (bottom) |
    | 3–6 | | no visible LED (unused outputs / control bits; bit 6 is set in the idle baseline `40`) |
    | 7 | `0x80` | **LCD + keypad EL backlight** (green) |

    The left indicator is a tri-color stack (red/amber/green). Direct `0x17` writes to `0x34AE` do
    **not** show — the firmware only pushes the shadow on its own events and overwrites it — so driving
    the LEDs needs a stub that calls the push itself (or the self-healing timer-ISR hook below).
  - **The LCD push chain** (reversed from the display task in page 4 at `0x5378`): the firmware brackets
    each update with a SPI-bus **acquire/release** — `0x5013` (pre) and `0x5032` (post), which call the
    device-manager `0x9FFB`/`0xA01A` with `D = 0x0245` (the control-head SPI device struct) — and in
    between does `LDAB #8; JSR 0x8F1F` (entry 8) to stream the 21-byte `0x37AC` buffer. Boot inits the
    controller with entries **5,4,3** once; steady state alternates **8/10** with periodic **5**.
  - **Command vs data on the control head** (from SPI capture + the entry routines): the LCD segment
    driver shares the SPI bus, selected on **port `0x107E`**. A write asserts a CS/mode nibble with
    `BSET $107E`, streams bytes via `0x9102`, then `BCLR $107E #$61`. Observed selects: `0x01` = LCD
    **data** (entry 8, 21 bytes from `0x37AC`); `0x41` = LCD **command** (entry 3, 3 bytes from
    `0x01EE`, **bit6 = command**); `0x21`/`0x20`/`0x60`/`0x61` = LED / other-device / mixed selects.
    The firmware's first real display push is preceded by the command **`00 00 3f`** at `CS=0x41`
    (sets the controller's address pointer); then it streams 21 data bytes at `CS=0x01` and, in steady
    state, repeats entry 8 (the controller auto-wraps after 21 bytes).
  - **Driving the LCD — see the consolidated "## LCD" section below for the settled facts.** Short
    version: change the displayed idle number via the **Own Unit No.** codeplug field (`tools/setunit.py`
    / DPS, cam-verified); show **arbitrary text** on the 3 digits with the `gm600py/stubs/lcd_text.S` RAM stub
    (entry 5, the separate 7-seg controller — cam-proven). `0x37AC` holds icons/framing only, not the
    digits; the SBEP-`0x17` "race" on it is **unreliable** (RAM writes don't ACK).
- **In-circuit patching over BUS+, self-healing.** The OC3/4/5 timer ISRs (`0x92A1`/`0x92AA`/
  `0x9298`) clear their flag and `jmp` through **RAM pointers `$A4`/`$A6`/`$A2`**. Write a stub's
  address into one with a `0x17` RAM write → periodic execution with no flash change (reset re-inits
  the pointers, so RAM experiments are self-healing). With the LED push above, that's enough to make
  a front-panel LED follow a firmware flag without touching the flash.

The **>4-channel limit** (below) can now be settled against the full image — it lives in these banks.

## How these addresses were found (so they can be re-derived)

- Disassembled with capstone (`CS_ARCH_M680X` / `CS_MODE_M680X_6811`) against the 64 KB dump.
- Dispatch table located by searching bank `04` for the `[00 01 …]` header preceding a run of
  `82 36` (the unsupported-stub address) with real handler addresses at the known opcode indices.
- Bank register `0x1058` found from the reset init and the trampoline at `0x87B5`.
- Reply opcodes confirmed by the immediate loads in each handler (`ldab #$80` at the `11` handler,
  etc.).

## Getting the full flash — two more routes, and the channel-limit question

Beyond reading U0102 (the AT29C020) with a programmer (the route the full-chip dump used) and the
`0x26` RAM-stub route described above, two more:

- **68HC11 bootstrap mode.** The K-series has no BDM (BDM arrived with the HC12/HCS08 generation), so
  bootstrap is the native debug door: strap the mode pins at reset (`MODA_LIR`/`MODB_VSTBY`, both
  exposed on U0101) and the mask-ROM loader slurps a 256-byte helper into RAM over the SCI and runs
  it. That helper can drive `0x1058` and stream every bank out the same BUS+ wire — tool-free.
- **Passive bus-snoop.** A logic analyzer on the demuxed external bus (`A0..17` + `PC0..7_DATA` + E +
  R/W, ~28 lines) reconstructs ROM fetches *as executed* — the '11 has no DMA and no instruction
  cache, so the bus is a faithful execution trace (great for *watching* the codeplug/PL logic run).
  Complete coverage needs exercising all code paths; for a guaranteed whole image the programmer dump
  wins.

**>4 conventional channels** (GM600 caps at 4; GP600 does 16; 6/8-channel `M08…CK` models exist) is
**firmware-gated** — the channel-count limit lives in these flash banks, not the codeplug — so
settling or lifting it needs the full flash, not a codeplug edit.

For the analog/board side (ASFIC `SQDET`/`PLLIM`/`CHACT`, audio PA enable, flat-audio & repeater
taps), see [HARDWARE.md](HARDWARE.md).

## Remote keypress — a firmware patch over the keypad A/D

The front panel is a 6-key analog resistor ladder read through the A/D; there is **no clean software
way to synthesise a key event**. The firmware re-samples the pin every poll (`read_adc` at CPU
`0x50DF` writes ADCTL to force a *fresh* conversion each call) and dispatches keys as RTOS messages
through a mode-dependent state machine: key code lands in `byte_3255`, the keypad task switches on an
event type at `0x4720` into a jump table at `0x4740` (entry `0x473E` = key), the "give" at `0x8255`
is only a lock, and the chain wakes the display task (TCB `0x1141`). Injecting at any one stage
desyncs from that state machine (verified: give alone, `byte_3255` write, and calling the dispatch
all fail to reproduce a press).

So override the **source** instead. `read_adc` reads the keypad channel at CPU `0x50FE`
(`ldab ADR1` = `F6 10 31`, flash page 0 = file `0x10DF`/`0x10FE`). Replace those 3 bytes with
`jsr 0x5E25` and put a 13-byte stub in free flash at CPU `0x5E25` (file `0x1E25`, an 8.6 KB `0xFF`
run in page 0; a direct jsr reaches it because `read_adc` runs in page 0):

```
0x5E25  ldab 0x028B (FORCE_EN)   ; F6 02 8B
        beq  real                ; 27 04
        ldab 0x028C (FORCE_VAL)  ; F6 02 8C   armed: use our value, ignore the real button
        rts                      ; 39
real:   ldab ADR1                ; F6 10 31   disarmed: real keypad, patch is transparent
        rts                      ; 39
```

`FORCE_EN`/`FORCE_VAL` are two RAM bytes the firmware never touches (`0x028B`/`0x028C`). With the
patch in place the **whole real keypad path** runs for a host-supplied A/D level, so a press is
identical to a finger — **validated in the emulator byte-for-byte against a physical press on the
patched firmware**. While armed the stub ignores the real line (a real finger and the remote can't
fight); press/release are RAM writes (SBEP `0x17`); reverting the flash bytes restores the buttons.

The host tool is [`tools/gm600_keypad_patch.py`](tools/gm600_keypad_patch.py) (`patch_image`,
`patch_diff`, and `arm`/`press`/`release`). Applying and reverting the patch on a real radio is an
AT29C020 in-system **sector** write (256-byte sectors at `0x1000` and `0x1E00`) — the one remaining
hardware step; the module emits the exact byte diff for a flash writer.

## LCD — settled facts

The control head is a segment LCD on the CPU's SPI bus (not BUS+), driven by **two controllers**:
the **three main 7-seg digits** go through SPI-dispatcher **entry 5** (see "7-seg digits" below), and the
icons/annunciators/framing come from a 21-byte shadow at **`0x37AC`** (entry 8). `0x37AC` does **not**
contain the digits — a RAM diff of the running radio at Own Unit No. 322 vs 686 changes only
`0x3497–0x3499` (+ ASCII at `0x34BD`), never `0x37AC` or `0x01F1`. Everything below is
verified; the chronological dead-ends are in **Lessons** at the end.

### SPI push protocol (what a refresh actually sends)
Dispatcher `0x8F1F` jumps through a word table at `0x8F44`; **entry 8** = push the 21-byte LCD buffer,
**entry 10** = push the 1-byte LED shadow `0x34AE`. SPI bytes go out via primitive **`0x9102`**(X=buf,
B=count): per byte it waits SPSR(`0x1029`) SPIF (`tst $1029; bpl`), writes SPDR(`0x102A`), and reads
SPDR after the last to clear it (no COP kick inside). CS lines live in port **`0x107E`** and are driven
with **BSET/BCLR** (read-modify-write, to preserve the other device selects) — *not* full-byte stores.
A refresh (emulator-traced) is:
- **Command phase:** SPCR(`0x1028`)=`0x57`, `BSET 0x107E #0x41`, send `02 50 3F` via `0x9102`, `BCLR 0x107E #0x61`.
- **Data phase:** SPCR=`0x50`, `BSET 0x107E #0x01`, send the 21 bytes of `0x37AC`, then clear.
The firmware brackets updates with an SPI-bus acquire/release (`0x5013`/`0x5032` → device-manager
`0x9FFB`/`0xA01A`, device struct `D=0x0245`); boot inits the controller with entries 5,4,3 once.

### LED shadow `0x34AE` bit map (verified on the radio via `gm600py/stubs/led_cmd.S` + webcam)
| bit | mask | element | | bit | mask | element |
|--|--|--|--|--|--|--|
| 0 | `0x01` | amber status LED | | 2 | `0x04` | green status LED |
| 1 | `0x02` | red status LED | | 7 | `0x80` | LCD/keypad EL backlight |

bits 3–6: no visible LED (bit 6 set in the idle baseline `0x40`). The left indicator is a tri-color
stack. Direct `0x17` writes to `0x34AE` don't show — the firmware overwrites the shadow on its own
events — so driving LEDs needs a stub that calls the push (or the self-healing OC-ISR hook above).

### Icon/annunciator + backlight field — codeplug `0x0E07–0x0E0D` (cam-verified)
Writing this 7-byte field + `0x10` reset changes annunciator segments and the backlight (NOT the main
number). Bit map (emulator-derived, confirmed on hardware): `0x0E07`→buf idx12 bits4–7; `0x0E0B`→idx16
=`src&0x7B`; `0x0E0C`→idx17=`0x10|(src&0x0C)`; `0x0E0D`→idx18=`0x02|(src&0x1C)`; `0x0E08`→idx13
(lookup, not a clean mask); `0x0E09`/`0x0E0A` = no effect. all-FF/all-00 here turns the backlight on
and lights/clears an icon.

### The displayed idle NUMBER = MPT1327 Own Unit No. — codeplug `0x0F0B–0x0F0C` (cam-verified)
Big-endian 16-bit (322=`0x0142`, 456=`0x01C8`, 712=`0x02C8`), protected by the block checksum at
**`0x0FF4`** = `(Σ(0x0F08..0x0FF3) − 0x55) & 0xFF`. This is the documented MPT1327_OWN_IDENT location.
Change it two ways, both cam-verified (322→456→712→322):
- **Direct SBEP, no DPS:** `tools/setunit.py <n>` — `0x17`-writes the two bytes + the checksum, then
  `0x10` reset. (A DPS 322→456 write diffs to exactly these two bytes, which is what cracked it.)
- **Vendor DPS:** File→Read Radio, Edit→Trunking→MPT Personality Data→"Own Unit No.", File→Write
  Radio; driven headless with `tools/mouse_ctl.py` + `tools/typestr.py`.
### 7-seg digits — entry 5, and a working RAM stub (cam-proven)
SPI-dispatcher **entry 5 = `0x9018`**: takes a pointer (stack arg, read at `$C,y`) to a 5-byte struct
`[b0 b1 d0 d1 d2]`, bit-packs it into a 5-byte frame, sets SPCR=`0x52`, `BSET 0x107E #0x61`, sends 5
zero bytes (from `0x90F0`) then the frame via `0x9102`, `BCLR 0x107E #0x61`, and copies the struct
to **`0x3495`**. So `0x3497–0x3499` is the firmware's cache of the three digit bytes (idle `b0 b1` =
`00 40`). Firmware callers (page 1, `0x56A7`): `ldd #$0245; jsr $9FFB` (RTOS device lock — a pure
semaphore, bit 7 of `0x0245`), push struct ptr (`word_34A5+8`), push a word, `ldd #5; jsr $8F1F`, pull
both, `ldd #$0245; jsr $A01A` (unlock).

**Font** (one byte per digit; swept bit by bit on the radio, [images/lcd_7seg_bitmap.png](images/lcd_7seg_bitmap.png)):

| bit | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|--|--|--|--|--|--|--|--|--|
| segment | f (top-left) | a (top) | b (top-right) | — | e (bottom-left) | g (middle) | d (bottom) | c (bottom-right) |

e.g. `2`=`76`, `3`=`E6`, `6`=`F3`, `8`=`F7` (matches `0x3497` on the radio).

**`gm600py/stubs/lcd_text.S` + `lcd_text.py`** — RAM stub (upload `0x17`, launch `0x26`): `sei`, then loop:
wait for sync `A5`, read 5 bytes from the SCI, call entry 5 with them (no lock). Proven on the radio:
"322" pushed over a 686 codeplug while the firmware was halted, then HI / HEL / LO / AbC / PSU / 147 /
-9- ([images/lcd_text_stub.png](images/lcd_text_stub.png)). `lcd_text.py HEL LO`, `--scroll`, `--raw
e67676`, `--no-launch`. The firmware is halted while it runs: recover with `psu_reboot.py`.

**`gm600py/stubs/lcd_raw.S` — the same, with NO firmware code.** Our own SPI driver: `SPCR &= 0xF3; SPCR =
0x52`, `0x107E |= 0x61`, the 10 wire bytes out through SPDR polling SPIF, `0x107E &= 0x9E`, `SPCR |=
0x0C`. The host sends all 10 wire bytes (normally 5 × `00` + `lcd_text.pack(struct)`, a Python port of
`0x9018` cross-checked against the firmware in `emu6811`). The SPCR/select/SPDR write sequence is
identical to the firmware path in the emulator (test `LcdTextTest`), and it ran on the radio: HI / OUr /
SPI / COd / 586 ([images/lcd_raw_stub.png](images/lcd_raw_stub.png)). It also drives the LED shift
register as entry 10 does (`SPCR = 0x51`, shift 1 byte, strobe `0x107E |= 0x21`, clear `0x61`), so
`--backlight` turns the backlight on. `lcd_text.py --standalone [--backlight] TEXT`.
Commands carry a checksum (`A5 <10 wire bytes> <cks>`, `5A <LED byte> <cks>`, whole command sums to
`0xFF`) and the stub answers one status byte: `06` done, `15` bad checksum (nothing sent), `54` SPI
timeout (SPIF polled 256× without success; selects released). It lives at **`0x3E40`**: the earlier
`0x2000` load address is the firmware's live message-buffer pool, and once the stub grew to 255 bytes
the upload itself crashed the radio. `0x3D80–0x3FFF` reads zero in every RAM dump and is unreferenced.

**The glass is one controller: 3 digits + 9 icons, start-bit framed.** `b0`/`b1` in the struct are the
icons (`b1` bit 6 is the upper-right icon the idle screen shows). The controller ignores zeros until a
`1` (start bit), latches the next **33 data bits**, then waits for the next start bit; a partial frame
survives chip-select, which is what the firmware's 5 leading zero bytes flush. (Consistent with an
MC145453-class 33-segment driver; part not identified.) The early "every segment answers in both wire
halves" observation was this: a `1` in bytes 0–4 becomes an earlier start bit and shifts the real one
into a data slot. Settled by an A/B framing experiment; the model reproduces all 79 single-bit photos
and all 12 framing photos. Data-bit map, per-segment photos, glyph shots and the experiment:
[images/lcd_segments/](images/lcd_segments/README.md).

The clean way to change the number the *firmware* shows is still the Own Unit No. codeplug field
above; the stubs are for showing anything else.

### Lessons (why earlier conclusions were wrong)
- **The `0x37AC` stub pushes "never worked" because the digits aren't there.** Every attempt to put a
  number on the glass by pushing `0x37AC` (entry 8) was aimed at the icon controller; the "888" seen
  was the boot self-test after a crash. The digits are a separate controller (entry 5). A RAM diff
  between two displayed numbers found it in one step — do that before decoding a buffer by hand.
- **Emulator gap (closed):** the VM used to render the same screen for any Own Unit No. because it
  never reached the idle own-ident state: with all input pins reading 0 and every A/D channel 0xFF it
  stayed in a channel scan. With the bench radio's pin and A/D levels modelled in `emu6811`, the VM
  shows the codeplug's Own Unit No. (322, 456) like the hardware. Earlier "the VM renders 322" claims
  read `0x37AC`, which never holds the digits.
- **Why the "race" looked like it worked:** all-00 (blank) and all-FF (all segments) are extremes that
  look right regardless, and an early "888" was actually the boot self-test caught mid-animation. The
  race never gave *clean* control of a chosen value — a sparse buffer blended with the firmware's
  concurrent `322` render, and RAM `0x17` writes don't even ACK. Treat the race as unproven.

## Keypad, key events and the receive flags (2026-11-12)

**Keypad decode.** Three analog ladders on A/D channels AN0–AN2, read by the keypad task (TCB
`0x11D0`) through `read_adc` (`0x50DF`, a fresh conversion each call). Decoders (page 0) `0x4FBB` /
`0x4F79` / `0x4FFD` turn a reading into a key code by window (≤`0x29`, `0x39`–`0x48`, `0x79`–`0x88`,
`0xB9`–`0xC8`); a code must read the same twice in a row (last codes at `0x33FC`–`0x33FE`).

**Key events — `0x4D8B(event, state)`** (page 0; `D` = event, 16-bit stack arg = state). The keypad
task calls it for every key of every line on every scan. It keeps the state in an **input-state array at
`0x3460 + event`**, and on a change posts the key message to the UI task. Events: 16–19 = vol down / vol
up / clear / base call, **20–23 = A / B / C / D**, 6–8 = scroll down / scroll up / on-off; the other
entries are fed by other inputs.

**Receive flags in the same array** (found by RAM-diffing a conventional-channel radio with no signal,
a carrier, and a carrier + the channel's PL from a HackRF; then confirmed live, repeatably):

| RAM | meaning | no signal | carrier, no/wrong PL | carrier + correct PL |
|--|--|--|--|--|
| **`0x347F`** (event 31) | carrier detected | 0 | 1 | 1 |
| **`0x347E`** (event 30) | **PL decoded = RX active / squelch open** | 0 | 0 | 1 |

Hardware side: PORTH (`0x107C`) bit 3 goes high on a carrier without our PL, and the red LED (`0x34AE`
bit 1) flashes "channel busy". `0x347E` sets within ~0.1 s of a matching PL.

**Pausing a task.** Setting bit `0x10` (or `0x20`/`0x04`/`0x01`) in a TCB's flag byte (TCB+8) makes the
scheduler skip it until cleared; `0x88`/`0xC0` are cleared back. Keypad task: `0x11D8`.

**Remote key presses without a flash patch — `tools/keyhold.py` + `gm600py/stubs/keyevt.S`.** Pause the
keypad task (so no real scan reports the key released), then post the event with a *non-halting* RAM
stub launched through the firmware's own `0x26` handler: it switches to page 0, calls `0x4D8B`,
restores the bank, re-enables interrupts and exits through the handler's epilogue (`0x4CE6`:
`pulx; rts`), so the radio keeps running. Release + resume afterwards. Validated in the emulator
(`KeyHoldTest`) and on the radio: a remote long-hold of A put the bench radio into conventional
channel `1`. **The conventional latch is RAM state** — no EEPROM write happens (the EEPROM-write
routine `0x5259`–`0x5293` never runs; a codeplug dump is identical in both modes). On the bench radio
it **survives a PSU power-off** (booted back into conventional), so that RAM is retained across main
power — the HC11 standby supply (MODB/VSTBY) keeps the K4's **internal RAM `0x0000`–`0x02FF`** alive
(external RAM is cleared by the boot RAM test, which is why stubs at `0x3D80` vanish on a reset).
Switching mode re-runs the self-test.

**The mode bytes** (internal-RAM diff of both modes on the radio, then bisected in `emu6811` with a
warm reset): **`0x0085`** = `FF` conventional / `01` trunked, and **`0x0089`** bit 1 = conventional /
bit 2 = trunked (other bits untouched). Both are needed. The firmware reads them at boot, so two `0x17`
writes + a `0x10` reset switch the mode (`tools/gm600_mode.py`, ~10 s, both ways on the radio).
Consequence for anything parked in internal RAM: it survives power-off — e.g. the keypad patch's
arm flags were moved to external RAM (`0x3FF0`–`0x3FF2`) so an override can't outlive a reset.

**Live web panel for the real radio — `tools/gm600_live.py`.** The VM UI's page, backed by SBEP: screen
from the display struct cached at `0x3495`, LEDs `0x34AE`, RX flags; keys through `keyevt.S` (tap /
mouse-hold). Survives mode-switch resets (re-uploads the stub, retries the release).

## Free RAM 0x3D80–0x3FFF — who owns which bytes

The only RAM the tools use. It reads zero in every RAM dump, nothing in the firmware references it,
and the boot RAM test clears it (so nothing here survives a reset). `0x2000` is the firmware's live
message-buffer pool — never load there. Internal RAM `0x0000`–`0x02FF` is retained across power-off —
don't park flags there either. Halting stubs end with a power-cycle, so their overlap with each other
is harmless; anything that runs **alongside the firmware** (marked *live*) must not overlap anything
else that can be present at the same time.

| Range | Owner | Notes |
|--|--|--|
| `0x3D80`–`0x3DB8` | `keyevt.S` code (*live*) | non-halting; the live panel / `keyhold.py` keep it resident |
| `0x3D80`–`0x3F47` | halting stubs' code: `ramdump.S`, `flashtool.S`, `lcd_text.S`, `led_cmd.S` | one at a time; overlap `keyevt.S` — fine, a halting stub ends with a power-cycle |
| `0x3E40`–`0x3F59` | `lcd_raw.S` code (halting) | |
| `0x3F00`–`0x3F01` | `keyevt.S` parameters EVT, STATE (*live*) | written by the host before each `0x26` |
| `0x3F80`–`0x3F8C` | `lcd_raw.S` / `lcd_text.S` buffers (halting) | |
| `0x3FF0`–`0x3FF2` | keypad patch FORCE_EN / FORCE_VAL / FORCE_M2 (*live*, only if that patch is flashed) | cleared at boot by design |
| ↓ from `0x3FFF` | stack of halting stubs that `lds #$3FFF` | grows down over `0x3FF0`–; harmless (firmware is halted, power-cycle follows) |

`flashtool.S` additionally uses `0x2280`–`0x2286` and a 256-byte buffer at `0x2300` (inside the
buffer pool, on purpose: only touched after launch, when the firmware is halted for good).
