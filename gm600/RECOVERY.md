# GM600 flash recovery — page-3 brick and how to un-brick it

## What happened

The in-circuit flash writer (`tools/flashtool.S`) had a **bank-encoding bug**: `do_read`/`do_write`
wrote the **raw page number** to `MM1CR` (`0x1058`) instead of **`page << 2`** (which `ramdump.S`
does correctly). A "safe test" write aimed at page 13 (device `0x34000`, erased) therefore set
`BANK = 0x0D` → `(0x0D >> 2) = page 3` → **device `0xC000` = live firmware**. The read path used the
same wrong encoding, so the "before" read and the "verified" read-back both landed at `0xC000`, and
the write silently corrupted a live boot-path sector. Fixed in commit `81c6165` (two `aslb` →
`page<<2`; regression test `test_nonzero_page_bank_encoding`; page 3 added to the write whitelist for
the recovery reflash).

## Symptom → root cause (verified in the emulator)

- **12 V off (normal expanded mode):** all LCD segments on + beep, repeating = **bootloop**.
- **12 V on (bootstrap mode — see Route 2):** one beep, then silence.

Root-cause chain, reproduced byte-for-byte in `tools/emu6811.py`:

1. Page 3 holds **boot-path executable code**. The firmware banks it in (`BANK=0x0C`) and jumps into
   the window at `0x4000`.
2. The corrupted bytes `(i^0x3C)` decode to `0x3F` = **SWI** at window `0x4003`.
3. SWI vectors through `0xFFF6` → **`0x8EE4`**, which is **`20 FE` = `BRA *`** (an infinite spin;
   it's the shared default handler for most vectors).
4. The spin never kicks the COP → **COP watchdog resets the CPU** → reboot → same crash = the loop.

The 12 V-on state is **bootstrap mode**, not single-chip: 12 V on pin 12 pulls **both MODA and MODB**
low via D0101/Q0102 (`MODB:MODA = 0:0`), confirmed by schematic trace — see Route 2. The HC11's
internal bootstrap ROM runs (DISR is set in special modes, so no watchdog loop), sits waiting for an
SCI download, and the single beep is just the audio side powering up; the external firmware does not
run. (COP is otherwise enabled — `CONFIG = 0xD9`, `NOCOP=0` — which is why the 12 V-*off* bootloop is
the COP resetting the crashed spin.)

Emulator proof (`tools/test_tools.py::BootFaultInjectionTest`, needs `GM600_FW=motofix.BIN`):
golden boots to the main loop (`PC=0xCE98`) and never reaches `0x8EE4`; the reconstructed broken
image reaches the `0x8EE4` spin; **restoring `0xC000` from the golden dump boots identically to
golden.** The recovery content is therefore known and verified: `motofix.BIN[0xC000:0xC100]`.

### Verified against the physical chip
The desoldered chip was read back (`broken.BIN`) and diffed against golden. **Exactly two 256-byte
sectors differ, both holding the `(i^0x3C)` test pattern** — both from the bank bug:
- **device `0x2800`** (page 0): the earlier write aimed at CPU `0x6800` that reported "link closed" —
  it *did* program (raw-bank `BANK=1` → page 0, window `0x6800` → device `0x2800`). Golden is all-FF
  here (unused); booting the VM with *only* `0x2800` corrupted **boots clean → harmless**.
- **device `0xC000`** (page 3): the boot-path code; booting with *only* `0xC000` corrupted **crashes**.

Booting `broken.BIN` itself in the VM reproduces the `0x8EE4` crash; booting golden is clean. So the
full golden reflash restores both sectors and fully recovers the radio.

## Outcome

**Recovered by T48** (route 3, below). In-circuit bootstrap (route 2) was attempted and came up
silent; chasing it further needed a clock measurement the bench couldn't spare, so we fell back to
the guaranteed off-board reflash.

## Recovery routes

### 1. In-circuit SBEP — NOT possible in this state
The firmware crashes in the corrupted page-3 code *before* it reaches a running state that services
SBEP, so the radio never answers SBEP while bricked. (SBEP is handled off the SCI receive interrupt —
the firmware clocks its transmit from the RX ISR — not by polling; `SCCR2` is `0x1073`, set during
boot, after the crash point.) The flashtool stub upload (`0x17`+`0x26`) needs live firmware, which we
don't have. Confirmed in the VM.

### 2. HC11 bootstrap mode (no desolder) — ATTEMPTED, inconclusive
**12 V on accessory pin 12 already selects bootstrap mode**: pin 12 drives *both* MODA (pin 77) and
MODB (pin 76, `MODB_VSTBY`) low through D0101/Q0102 — they share the diode node, so >~11 V pulls both
low = `MODB:MODA = 0:0` = bootstrap, while Q0103 turns off and releases the flash `/OE`. **No
soldering needed** — the earlier "single-chip" reading was wrong (MODB was never high). This is
Motorola's own factory-programming mechanism: one 12 V signal boots the internal loader *and* unlocks
flash writes. Security does **not** block it: `CONFIG = 0xD9` → **`NOSEC=1` (unsecured)**.

The boot ROM loads into `$0080–$037F` and jumps to `$0080` after a 4-char idle or the 768th byte
(K4 datasheet §2.3, and AN1060). Baud = `EXTAL ÷ (32 × BR)`; **but the CPU clock comes from the
ASFIC's UPCLK** (EXTAL ← R0147 ← TP0100 ← ASFIC via R0146), which the firmware raises to 14.7456 MHz
over SPI during boot (derived from the firmware's `SCBDL = 0x30` → BR=48 → 9600). In bootstrap no
firmware runs, so the ASFIC sits at an **unknown power-on default clock**, and the bootstrap baud is
whatever that gives.

What was tried: a baud sweep (115200, 57600, 38400, 28800, 19200, 14400, 9600, 7200, 4800, 2400,
1200), power-cycling into bootstrap at each and sending one `$FF`. The applet's receiver decodes the
**physical** BUS+ wire (open-drain wired-AND; no software echo), so a live boot ROM re-transmitting
its byte would show as a **second** byte. **Every baud returned exactly one byte (our own echo) — no
boot ROM response on BUS+.** Unresolved possibilities: the ASFIC default clock gives a baud outside
the swept set; the 448-byte K4 boot ROM may not echo at all; or the SCI TxD→BUS+ path needs setup the
boot ROM doesn't do. **Decisive next test if revisited: scope/counter on TP0100 with 12 V on** to get
the real bootstrap clock, then target that baud and confirm with a downloaded signature program
(don't rely on echo). A RAM loader would then: set `HPRIO.MDA` (expanded) + clear `RBOOT`, replay the
reset register table (below), drive PA6, program `0x0C000` from golden, kicking the COP; buffer in
external SRAM (CSGP2); one byte at a time, waiting for each echo.

### 3. T48 off-board — USED (guaranteed)
Desolder the AT29C020, **erase + program the full golden `motofix.BIN`** (SHA-256
`db74c7c88911e9df1f7328e09975ae3a2db5d212be3df74bdeee0dd00aa530b9`, 262144 B), resolder. The live
flash differs from golden only in the one corrupted sector, so a full reflash restores it exactly.
**The codeplug is in the CPU's internal EEPROM (`0x0D80–0x0FFF`), not this chip — T48 doesn't touch
it.** After `0xC000` the sector should read `34 c6 01 30 e7 00 f6 34 7a c4 01 27 06 bd b8 ba`.

## Post-recovery validation (on the real radio)

After reflashing, the radio boots and answers SBEP (serial `807IYA0903`). Three checks confirmed a
clean recovery and a correct fix:

1. **Firmware is byte-perfect.** An in-circuit `ramdump.py --with-eeprom` was diffed against golden:
   **0 bytes differ in the 256 KB flash**; both former corruption sites (`0x2800`, `0xC000`) match
   golden. (The only NV difference is 3 bytes in the 640 B EEPROM codeplug — the trunking→conventional
   mode switch — which is not in the flash; `motofix.BIN` is a flash-only *trunking* dump.)
2. **The `page<<2` bank fix works in-circuit.** `flash_write_test.py` (with 12 V on pin 12): probe
   `1F DA FE FE` → write a pattern to page 13 (`0x34000`) → read-back matches → **`0xC000` (page 3)
   unchanged** → restore `0x34000` to FF. A "page 13" write now lands at page 13, not page 3. The old
   stale stub, briefly left on the lab host, reproduced the bug one last time (its "read 0x34000"
   returned `0xC000`'s contents) — a reminder to deploy the fixed stub before any write.
3. **The safety guards fire.** Every write auto-backed-up the live sector to `flash_backups/`, and
   `require_erased=True` confirmed page 13 was erased before writing.

⚠️ Order for an in-circuit write: boot with **12 V OFF** (12 V at reset = bootstrap, no firmware),
*then* apply 12 V, run the tool, remove 12 V, power-cycle. If the probe reads `0F CE…` instead of
`1F DA…`, the 12 V isn't effective (sagging below ~11 V, or dropped out).

## Firmware reset register-setup table (authoritative, K4 names)

The reset handler at `0xD45A` copies this table (`LDY #$D437`, write-loop, `0xFF`-terminated) into the
`0x10xx` register block. A bootstrap writer must replay it to reach the flash. **Flash `/CE` is
`CSPROG`, point-to-point (traced) — not CSGP1.**

| Reg      | Value | K4 register | Role |
|----------|-------|-------------|------|
| `0x1026` | `0x01` | PACTL | port A control |
| `0x1056` | `0x42` | **MMSIZ** | memory-window size (with MMWBR defines the bank window) |
| `0x1057` | `0x06` | **MMWBR** | memory-window base |
| `0x102D` | `0x1E` | **PGAR** | Port G assignment (the K4 SCI is at `0x1070–0x1077`, *not* `0x102D`) |
| `0x105B` | `0x05` | **CSCTL** | configures **CSPROG** — the flash chip-select (the one that matters) |
| `0x105D` | `0x4A` | GPCS1C | CSGP1 control (not the flash) |
| `0x105F` | `0x04` | GPCS2C | CSGP2 control (external SRAM chip-select) |
| `0x105A` | `0x00` | CSCSTR | chip-select clock stretch |
| `0x1001` | `0x70` | DDRA | port A directions |
| `0x1008` | `0x18` | PORTD | |
| `0x1009` | `0x3A` | DDRD | |
| `0x107F` | `0x61` | — | K4 extended reg |
| `0x107C` | `0x10` | — | K4 extended reg |
| `0x107D` | `0x14` | — | K4 extended reg |
| `0x1028` | `0x50` | SPCR | SPI enable (ASFIC link) |
| `0x1072` | `0x40` | **SCCR1** | SCI: `WOMS=1` = wired-OR (open-drain) — matches the single-wire BUS+ |
| `0x1030` | `0x34` | ADCTL | |

Set *before* the table by the reset code: `OPTION(0x1039)=0xBB`, `0x1035=0x40`, and `INIT=0x01`
(moves registers to `0x1000`) via the early direct `STAA $3D`. `MM1CR(0x1058)` (the bank) stays 0 and
is driven dynamically during boot. `SCCR2(0x1073)` (SCI interrupt enables, incl. RIE) is **not** in
this table — it is set later during boot. `HPRIO` is **never** touched by the firmware (it is already
expanded) — so entering expanded mode from bootstrap is the one step the writer must add.

> Bootstrap over BUS+ was **set aside** (recovery was done on the T48). A likely reason the boot-ROM
> echo never reached BUS+: the firmware drives the SCI in **wired-OR mode (`SCCR1.WOMS`)** and enables
> the line via port pins (`DDRD=0x3A`, `PORTD=0x18`) that the bootstrap ROM never configures — so it
> could receive/load fine while its echo never drives the bus. If ever revisited, confirm with a
> downloaded signature program that toggles a scopeable pin (not the SCI), and measure TP0100.
