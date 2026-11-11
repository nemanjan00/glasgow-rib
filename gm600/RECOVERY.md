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
- **12 V on (MODA low → single-chip mode):** one beep, then silence.

Root-cause chain, reproduced byte-for-byte in `tools/emu6811.py`:

1. Page 3 holds **boot-path executable code**. The firmware banks it in (`BANK=0x0C`) and jumps into
   the window at `0x4000`.
2. The corrupted bytes `(i^0x3C)` decode to `0x3F` = **SWI** at window `0x4003`.
3. SWI vectors through `0xFFF6` → **`0x8EE4`**, which is **`20 FE` = `BRA *`** (an infinite spin;
   it's the shared default handler for most vectors).
4. The spin never kicks the COP → **COP watchdog resets the CPU** → reboot → same crash = the loop.

The 12 V-on single beep is **not** the watchdog being off: COP is governed by `NOCOP` in `CONFIG`,
not the mode pins, and only the *special* modes (bootstrap/test) set `DISR` to block resets.
Single-chip is a *normal* mode, so its COP runs normally. Since the 12 V-off loop *is* the COP
resetting the spin, **COP is enabled (NOCOP=0)** — which means the single-chip "beep then silence"
must be **CPU-internal code running** (external flash is off-bus, yet a beep needs ASFIC setup over
SPI). That is a possible **factory loader**, to be confirmed by a BUS+ sniff (see below).

Emulator proof (`tools/test_tools.py::BootFaultInjectionTest`, needs `GM600_FW=motofix.BIN`):
golden boots to the main loop (`PC=0xCE98`) and never reaches `0x8EE4`; the reconstructed broken
image reaches the `0x8EE4` spin; **restoring `0xC000` from the golden dump boots identically to
golden.** The recovery content is therefore known and verified: `motofix.BIN[0xC000:0xC100]`.

## Recovery routes, in order (cheapest first)

### 1. Factory-loader check — BUS+ sniff with 12 V on (free, do first)
The 12 V-on beep means internal code runs. **Listen before guessing:** power up with 12 V and capture
raw BUS+ for a few seconds. Anything it emits at power-up — even one byte — is informative. Then try
decoding at **9600** and **7812.5** baud. (A prior SBEP `0x23` ID at 9600 got no reply, which only
rules out *that* query at *that* speed, not a loader with its own prompt/protocol.) If a loader
exists and can program flash, the rest is unnecessary.

### 2. MODB meter check (for route 3)
`MODB` = **pin 76 (`MODB_VSTBY`)**. After reset this pin supplies RAM standby power, so **only pull
it low during reset, through ~1 kΩ, never a hard short.** With the radio off, measure resistance
pin 76 → +5 V and → GND: ~0 Ω to +5 V means tied directly (needs a cut); a few kΩ means an
overridable pull-up. With the radio on, measure the pin-76 voltage.

### 3. HC11 bootstrap mode (no desolder)
Force `MODB:MODA = 0:0` at reset → the MCU's internal bootstrap ROM loads a program over the SCI into
internal RAM and runs it, bypassing the broken flash. **MODA is already pulled low by 12 V on pin 12**
(which we also need to release the flash `/OE` for writing), so we only add MODB low. The RAM loader
must then reach the flash and reflash `0xC000` from golden. Writer requirements:

- Set **`HPRIO.MDA`** to switch from the special mode onto the **expanded bus**, then **clear
  `RBOOT`** so the boot ROM stops overlaying the top of the map (vectors + flash underneath).
- **Replay the firmware's reset register table** (below) to map the flash and bank window.
- **Never write `CONFIG`** (writable in special modes; a bad write there is a worse brick).
- Watchdog is blocked by `DISR` in special modes, but **keep the COP kicks** so the same code also
  works in normal mode.
- Internal RAM is small — once expanded, put the **256-byte sector buffer in external SRAM (CSGP2)**.
- The bootstrap loader **echoes every received byte**; on the single-wire BUS+ that collides with the
  next byte, so **send one byte at a time and wait for each echo**.

### 4. T48 off-board — guaranteed fallback
Desolder/clip the AT29C020, reflash page 3 (`0xC000`) from `motofix.BIN`, resolder.

## Firmware reset register-setup table (authoritative, K4 names)

The reset handler at `0xD45A` copies this table (`LDY #$D437`, write-loop, `0xFF`-terminated) into the
`0x10xx` register block. A bootstrap writer must replay it to reach the flash. **Flash `/CE` is
`CSPROG`, point-to-point (traced) — not CSGP1.**

| Reg      | Value | K4 register | Role |
|----------|-------|-------------|------|
| `0x1026` | `0x01` | PACTL | port A control |
| `0x1056` | `0x42` | **MMSIZ** | memory-window size (with MMWBR defines the bank window) |
| `0x1057` | `0x06` | **MMWBR** | memory-window base |
| `0x102D` | `0x1E` | SCCR2 | SCI TE+RE+ILIE, **RIE=0** (SBEP is polled, not RX-interrupt) |
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
| `0x1072` | `0x40` | — | |
| `0x1030` | `0x34` | ADCTL | |

Set *before* the table by the reset code: `OPTION(0x1039)=0xBB`, `0x1035=0x40`, and `INIT=0x01`
(moves registers to `0x1000`) via the early direct `STAA $3D`. `MM1CR(0x1058)` (the bank) stays 0 and
is driven dynamically during boot. `HPRIO` is **never** touched by the firmware (it is already
expanded) — so entering expanded mode from bootstrap is the one step the writer must add.
