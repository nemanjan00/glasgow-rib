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
    from a RAM stub (`tools/led_cmd.S` + `tools/led_demo.py`) and watching the panel over a webcam
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
  - ***Active* LCD driving — CONFIRMED (with the FULL init).** The earlier failures were because the
    stub only sent the `00 00 3f` address command + data; the controller actually needs the full init
    **config** first. Replaying the exact captured phase sequence byte-for-byte drives the control head
    (the display visibly changed `322` → `888`, and a `byte0=0xFF` data write blanked it). The init
    phases (captured at steady boot, `tools/lcd_replay.py`): `CS=0x20` ×2 (26 B config), `CS=0x01`
    (21 B + 9 B), `CS=0x61` (10 B cmd), `CS=0x60` (`de`,`df`), `CS=0x01` (9 B), then per update
    `CS=0x41` (`00 00 3f` addr cmd) + `CS=0x01` (21 B data). Tool: `tools/lcd_replay.S` (a generic
    phase-replay interpreter: host sends `[cs][spcr][nlen][data…]`) + `tools/lcd_replay.py`.
  - **Re-checked rigorously (don't trust the earlier "888"):** that `888` was the **boot self-test**
    (all-segments), caught mid-animation — not driven data. A controlled test (full boot, stub launched,
    then idle/blank/full captures) shows:
    - **The stub HAS sole control** — with it running and no data sent, the display is frozen (`322`,
      backlight off); the firmware is halted. (So no SPI contention — my earlier contention guess was
      wrong.)
    - **My init/config DOES reach the control head** — it turns the backlight on.
    - **But the 21-byte digit-data write (`CS=0x01` after `00 00 3f`) does NOT overwrite the held
      digits** — `all-00` and `all-FF` both leave `322`. Extra settling (post-boot, post-provision)
      did not change this, so it's an **addressing/data-format** issue, not timing.
  - **Still OPEN: writing the digit RAM.** The held digits aren't refreshed (static), yet my `CS=0x01`
    data doesn't replace them — likely `00 00 3f` isn't the digit-RAM address, or the 21-byte buffer is
    structured (not raw consecutive segments; `tools/gm600_remote.py` *decodes* it, so it has fields).
    This is slow to crack over a webcam; **a logic analyzer on the control-head SPI** (SCK/MOSI + the
    `0x107E` CS nibble) would show the firmware's exact address/command/data to replicate. Passive
    decode via `0x37AC` remains the reliable route to the segment map.

  - ***Active* LCD driving SOLVED (pure data, no flash / no 12 V).** The firmware renders the 21-byte
    `0x37AC` shadow then pushes it to the LCD each cycle. **Tight-loop SBEP `0x17` writes to `0x37AC`
    race that render**, so the firmware's own (fully-set-up) push displays *your* bytes. Verified on the
    real radio (webcam): `all-00` blanks the display, `all-FF` shows `888` — i.e. full control from
    empty to all-segments, overriding the firmware's "322". No checksum on `0x37AC`. Tool:
    `tools/lcd_drive.py` (a writer thread races `0x37AC`; images/lcd_driven_blank_888_dig.jpg).
    The display is multiplexed (digit segments in bytes 12-20 by COM line); showing an arbitrary
    *specific* number additionally needs the multiplex bit-map decoded (the race blends a sparse buffer
    with the firmware's concurrent 322 render — extremes win cleanly, sparse numbers need the co-render
    suppressed via the self-healing OC-ISR hook, 0xA2/0xA4/0xA6).
  - **The LCD is read *passively* instead** — `0x37AC` (21 bytes) is a live mirror decoded by
    `tools/gm600_remote.py` (channel "1" = `0530 00c1 008f d085 e5af ea00 a661 0000 6008 020010`,
    "322" = `8530 18c1 008f d080 e5af fe00 a261 8000 6018 020010`). Segment decoding is best done this
    way: capture `0x37AC` for known displays and correlate.
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

## LCD content: what is and is NOT data-drivable (bench, 5 V applet + webcam, no logic analyzer)

Full investigation of "can we make the display show a chosen value by writing data?" — using the
cycle-accurate emulator as an oracle (`tools/emu6811.py`, renders 0x37AC identically to the glass)
and the real radio + webcam as the numeral ground truth. The radio serves SBEP on the applet socket
(`127.0.0.1:2324`); `0x17` writes the internal EEPROM codeplug (0x0D80–0x0FFF) **and** the RO
factory region, and `0x10` resets/reboots the CPU (re-render). RW-block checksum at 0x0E12 =
`(Σ(0x0D94..0x0E11) − 0x55) & 0xFF` (verified: stored 0xEE == computed).

### DRIVABLE — identity/annunciator field 0x0E07–0x0E0D (VERIFIED on cam)
Writing this 7-byte field + `0x10` reset changes the LCD annunciator segments and the backlight,
persistently, rendered by the firmware. Bit-level map (emulator-derived, confirmed on hardware):
- 0x0E07 → buf idx12, bits 4–7 (idx12 low nibble fixed 0x2)
- 0x0E0B → buf idx16 = `src & 0x7B`
- 0x0E0C → buf idx17 = `0x10 | (src & 0x0C)`
- 0x0E0D → buf idx18 = `0x02 | (src & 0x1C)`
- 0x0E08 → buf idx13 (lookup-style, not a clean mask)
- 0x0E09/0x0E0A → no display effect
all-FF or all-00 here turns the **backlight on** and lights/clears an annunciator icon; the main
3-digit number is UNAFFECTED. So this field = status icons + backlight, NOT the main number. (Earlier
`DIGIT_BYTES=(3,5,6,12,13,14,16,17,18,20)` in gm600_remote was an icon-heavy guess; the real main
digits are buffer idx **[0,1,2,4,6,8,14]**.)

### NOT cleanly drivable — the main 3-digit "322" (trunking idle)
- **NOT** `0x0F0B` (documented MPT1327_OWN_IDENT = 0x0142 = 322): changing it, checksum-fixed,
  fresh-boot, changes **zero** of the 21 buffer bytes. The displayed 322 is not this field.
- Provenance sweep (every codeplug byte flipped, checksum-fixed, fresh-boot, full-buffer diff): the
  main-digit bytes [0,1,2,4,6,8,14] are moved only by `0x0D92/0x0DDA/0x0DF7/0x0EAF/0x0EB1`
  (freq-plan) and `0x0DD5/0x0DD6`. All of these drive the display to one of two states, never a clean
  alternate number: a **fallback** `00 31 04 80 d1 e4 00` (also what a wiped RW region yields) or
  **blank** `00…00` (0x0DD5/6). Clean small changes to the control-channel frequency bytes leave the
  display on 322. So 322 is firmware-determined (trunking idle, no live MPT1327 network on the bench)
  and derived from a valid multi-byte structure, not a single pokeable field.
- Renderer: `0x61B1/0x61B7/0x617B` (bank 0) draw the main digits **procedurally via bset/bclr**
  (opcodes 0x1C/0x1D) — there is no digit→segment font table to read, and no single RAM "number"
  byte (live-patch sweeps of the renderer's scratch struct 0x2FB4–0x2FC4 and input region
  0x374x–0x377x changed nothing; the struct is a transient comms-decode buffer).

### Injection paths and why they don't give an arbitrary number
- **RAM-stub LCD takeover** (`lcd_cmd.S`/`lcd_replay.S`, upload via 0x17 + launch via 0x26): the stub
  runs and pushes 0x37AC via the SPI dispatcher (entry 8, `jsr 0x8F1F`), but its controller
  addressing differs from the firmware's steady-state refresh, so a pushed buffer does not map to the
  expected glyph (322-buffer shows as 888; arbitrary buffers stay latched at 322/888). Driving a
  chosen numeral would need the per-COM multiplex font, which cannot be learned on the bench because
  the firmware only ever renders one number (322) in trunking and conventional-mode numbers need the
  keypad.
- **Race** (tight-loop `0x17` to RAM 0x37AC while firmware refreshes): RAM writes to 0x37AC do **not**
  return a write-ACK (only EEPROM-range 0x17 writes ACK with 0x84), so the race is unreliable here.
- **Recovery:** `0x10` reset reliably reboots the radio to normal trunking (322) — including out of a
  hung 0x26 stub, as long as the SBEP ISR is still alive. The applet `-V 5` is the logic reference,
  not the radio's main supply, so restarting the applet does not power-cycle the radio.

### Remaining routes to a specific displayed NUMBER (each needs a decision/resources)
1. **Keypad flash-patch** (`tools/gm600_keypad_patch.py` + fixed `tools/flashtool.py`): override the
   A/D keypad source in flash → inject Button-A long-hold (→ conventional mode = data-driven channel
   number) and channel up/down. Risk: AT29C020 boot-block sector write (0x1000/0x1E00) is untested on
   hardware and the boot block may be program-locked → re-brick risk.
2. **DPS reprogram** of the identity/network (the vendor tool in the QEMU VM) — the legitimate "write
   changed identity"; needs GUI interaction (VNC).
3. **Band-plan/entry-checksum RE**: find the exact checksum for the freq-plan entry so a valid
   alternate control-channel frequency renders a different channel number instead of the fallback.

### RESOLVED — the main number IS data-drivable: Own Unit No. @ 0x0F0B–0x0F0C (VERIFIED on cam)
The "322" idle display is the **MPT1327 Own Unit No.** (DPS → Edit → Trunking → MPT Personality
Data → "Own Unit No."), stored in the codeplug as a **big-endian 16-bit** value at **0x0F0B–0x0F0C**
(322=0x0142, 456=0x01C8, 712=0x02C8), protected by the block checksum at **0x0FF4**
(`(Σ(0x0F08..0x0FF3) − 0x55) & 0xFF`). This is the documented MPT1327_OWN_IDENT location — the docs
were right all along.

Two methods proven on the real radio (cam-verified 322 → 456 → 712 → 322):
1. **Vendor DPS** (`C:\MRSS\MPT\DPSWDP\WDPS_DP.EXE`, R00.05.04, English menus, in the Win3.1 QEMU VM,
   serial bridged to the applet): File→Read Radio, Edit→Trunking→MPT Personality Data, set Own Unit
   No., OK, File→Write Radio. Driven headless via `tools/mouse_ctl.py` (closed-loop mouse) +
   `/tmp/typestr.py` (QWERTZ typing); menu nav via `sendkey`.
2. **Direct SBEP data write** (no DPS): `0x17` write 0x0F0B/0x0F0C (BE16) + recompute checksum 0x0FF4
   + `0x10` reset. Tool: `~/captures/setunit.py <n>`. Diff of a DPS 322→456 write is exactly two
   bytes (0x0F0C and the 0x0FF4 checksum), which is what cracked it.

**Why the earlier hunt failed — emulator gap:** `emu6811.py` does NOT model the Own-Unit-No.→display
path. Changing 0x0F0B/0x0F0C in the emulator (checksum-fixed, fresh boot) produced **no** buffer
change, which wrongly led to "the number is firmware-determined / not a clean codeplug field." On
**real hardware** 0x0F0B–0x0F0C is the displayed number. Lesson: the emulator is unreliable for the
trunking-idle identity render; verify display-source claims on hardware. (The procedural bset/bclr
renderer and the icon-field/backlight findings above remain correct.)

### LCD push protocol — exact wire sequence (emulator-traced) + .S status
Goal: a RAM stub (`.S`) that writes arbitrary content to the glass. The firmware refresh of the
21-byte 0x37AC buffer to the control-head LCD controller (SPI) is:
- **SPI send primitive 0x9102**(X=buf, B=count): for each byte — wait SPSR(0x1029) SPIF (`tst
  $1029; bpl`), then `staa $102A` (SPDR); after the last, read SPDR to clear. **No COP kick inside.**
- **Command phase:** SPCR(0x1028)=0x57, then **BSET 0x107E #0x41** (CS; read-modify-write, NOT a
  full-byte store), send 3 cmd bytes `02 50 3F` via 0x9102, then **BCLR 0x107E #0x61**.
- **Data phase:** SPCR=0x50, **BSET 0x107E #0x01**, send 21 bytes of 0x37AC via 0x9102, then clear.
- The CS lines live in port **0x107E** and are driven with **BSET/BCLR** to preserve the other
  device-select bits (LED, etc.) — a plain `staa` of 0x41/0x01/0x00 (what `lcd_cmd.S`/`lcd_arb.S`
  did) is wrong and is the leading suspect for why stub pushes don't land.

**.S status: NOT working yet.** `lcd_arb.S`/`lcd_fftest.S` (upload 0x17 + launch 0x26, replicate
SPCR/CS/0x9102) run (SBEP goes dead = stub active) but the glass stays frozen at the firmware's last
render — the raw full-byte CS writes and/or a missing acquire/device-select step. Next: decompile
0x8F1F dispatcher + the display task's paint routine in **IDA** and either (a) fix CS to BSET/BCLR
and match SPCR exactly, or (b) have the stub call the firmware's own complete refresh routine with
0x37AC pre-filled. Recovery from a hung stub = **PSU power-cycle** (`~/captures/psu_reboot.py`), not
0x10. See the "Lab rig" section in CLAUDE.md.
