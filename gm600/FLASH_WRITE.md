# GM600 flash write — AT29C020 in-system programming

The GM600 firmware lives in an external **Atmel AT29C020** (256K×8, `motofix.BIN`). We can already
*read* it in-circuit (`tools/ramdump.py`, via a RAM stub). Writing it in-circuit would let us apply
the keypad-remote firmware patch (see [FIRMWARE.md](FIRMWARE.md), "Remote keypress") and revert it.
This documents the AT29C020 programming interface and how it maps through the GM600's banking.

Datasheet: Atmel `0291N–FLASH–07/02` (saved under `datasheets/AT29C020.pdf`, gitignored;
e.g. <https://www.farnell.com/datasheets/94379.pdf>). Mfr code `1F`, device code `DA`.

## The write lock — and how to release it (hardware-confirmed)

In stock operation the flash is **read-only**: every write command is ignored and a probe reads back
ordinary array data (`0F CE …`). The lock is the flash **`/OE`** line. Board control (traced on the
Y08 schematic, confirmed on the radio):

- `/WE` = CPU `PG7_R_W` (U0101 pin 33) — a write strobe on every CPU write; shared with the SRAM.
- `/CE` = `PH7_CSPROG` (U0101 pin 29), point-to-point to U0102 pin 30.
- `/OE` = `AND(PA6, Q0103 collector)` via U0106 (74AC08). **PA6** is a CPU port pin (U0101 pin 5);
  Q0103 is on the accessory **pin-12** net. Normally Q0103 holds the AND low → `/OE` stuck low →
  flash always in read mode → writes ignored (reads work regardless). Motorola reused the old
  28F020 VPP-detect net as this write-enable; VPP itself does nothing on the AT29C020.

**Release = ~12 V on accessory pin-12** (current-limited — the net has a 4.7 kΩ pull-up, a
firmware-switchable pull-down Q0432, and a ~14 V clamp; 12 V through ~1 kΩ is right). Above ~10 V the
zener/Q0102/Q0103 network turns Q0103 off, so `/OE` then **follows PA6**: PA6 low = read, PA6 high =
write-enable. **Stock firmware keeps running normally with 12 V present** (no reset, no boot-mode
change — MODA is only sampled at reset; this is NOT bootstrap). So the write path is:

> boot normally → **apply 12 V to pin-12 and leave it on** → launch the RAM stub (`flashtool`), which
> frees PA6 from OC2 (TCTL1), makes it an output, holds it low for reads and raises it only around the
> `AA/55/A0` command + sector-load writes → program → read-back verify → remove 12 V.

Order matters: hot-plugging 12 V can glitch a *running* stub, so apply 12 V **first**, then launch.

**Confirmed probe (12 V live, PA6-gated):** `mfr 1F, device DA` — the AT29C020 answered its own
command interface, i.e. **writes reach the chip** — with **lower-lockout `FE` and upper-lockout `FE`:
both boot blocks UNLOCKED**, so the patch sectors are programmable. (Commands go through the CSPROG
`$8000–$FFFF` region, where `/CE` is guaranteed; the AT29C020 decodes only A0–A14, so chip `0x5555`
== CPU `0xD555`, `0x2AAA` == CPU `0xAAAA`.)

## ⚠️ Boot-block lockout (measured: both UNLOCKED)

The AT29C020 has a **boot-block programming lockout**: the first 8 KB (`0x00000–0x01FFF`) and the
last 8 KB can each be permanently* locked against erase/program. **Everything we need to patch is in
the lower 8 KB** — `read_adc` at `0x010FE` and the stub slot at `0x01E25`. If Motorola enabled the
lockout on the lower block (plausible — it's exactly what the feature is for), **the firmware patch
cannot be written at all**, and remote keypress falls back to the hardware keypad-line drive.

Detect it in software product-ID mode: read `0x00002` → `0xFE` = lower block programmable, `0xFF` =
locked (and `0x3FFF2` for the upper block). The probe below does this; run it before any write.

(*lockout persists across power cycles and disables chip-erase; there is no software un-lock.)

## Command set (exact, from the datasheet)

All command "loads" are byte writes to these **device** addresses (mapping below). Each is a normal
write cycle; a command is the exact 3-cycle pattern.

| Operation | Sequence (data → address) |
|---|---|
| **Product-ID entry** | `AA→5555`, `55→2AAA`, `90→5555` |
| &nbsp;&nbsp;read codes | `0x0000`=mfr `1F`, `0x0001`=device `DA`; `0x0002`=lower-lockout, `0x3FFF2`=upper-lockout |
| **Product-ID exit** | `AA→5555`, `55→2AAA`, `F0→5555`, then wait 10 ms |
| **Sector program** (SDP prefix) | `AA→5555`, `55→2AAA`, `A0→5555`, then load the 256 sector bytes |
| SDP enable | `AA→5555`, `55→2AAA`, `A0→5555` (+ a sector load) |
| SDP disable | `AA→5555`, `55→2AAA`, `80→5555`, `AA→5555`, `55→2AAA`, `20→5555` |

- **Sector = 256 bytes; 1024 sectors.** A8–A17 select the sector, A0–A7 the byte. You must load the
  **whole** sector (unloaded bytes become indeterminate) — so read the sector first, patch bytes,
  write all 256 back.
- **Byte-load timing `tBLC` = 150 µs:** consecutive bytes must be loaded within 150 µs or the load
  window closes and programming starts. A tight stub loop (interrupts off) is well inside this.
- **Program time `tWC` = 10 ms** after the last byte.
- **Completion:** DATA-polling — a read of the last-loaded byte returns the **complement** of its
  I/O7 until done, then true data; or the **toggle bit** DQ6 toggles until done. A conservative
  fixed delay (≥15 ms) plus a read-back verify is simplest and robust.
- **SDP state is unknown on this radio.** SDP ships *disabled*; Motorola may have enabled it. Using
  the `AA/55/A0` prefix is the correct program command **either way** (it's the recognised command,
  not three stray data writes), so always send it.

## Device address → GM600 CPU access (through the bank window)

The flash's A0–A13 come from the CPU window `0x4000–0x7FFF`; A14–A17 from the bank register
`0x1058` (`= page << 2`, page = A14–A17). So for device address `D`:

```
page   = D >> 14
window = 0x4000 | (D & 0x3FFF)     # CPU address to touch
bank:  store (page << 2) to 0x1058
```

| Device addr | page | CPU window addr | bank reg `0x1058` |
|---|---|---|---|
| `0x5555` (cmd) | 1 | `0x5555` | `0x04` |
| `0x2AAA` (cmd) | 0 | `0x6AAA` | `0x00` |
| `0x0000/1/2` (ID) | 0 | `0x4000/1/2` | `0x00` |
| sector `0x1000` (read_adc patch) | 0 | `0x5000–0x50FF` | `0x00` |
| sector `0x1E00` (stub slot) | 0 | `0x5E00–0x5EFF` | `0x00` |

The command sequence therefore switches the bank between `0x5555` (page 1) and `0x2AAA` (page 0)
writes. The two patch sectors are both page 0.

## Tooling

`tools/flashtool.py` + `tools/flashtool.S` extend the RAM-stub idea (`ramdump`) to a resident stub
driven over SCI: **probe** (ID + lockout — safe, no writes), **read** sector, **program** sector.
The host refuses to program unless the probe reports the lower boot block unlocked. Applying the
keypad patch = probe → for each of the 2 patch sectors: read it, splice in the patched bytes
(`gm600_keypad_patch.patch_diff()`), program, read-back verify. Reverting = program the original
bytes back.

**Status.** The write *path* is confirmed on the real radio — with 12 V on pin-12 the probe returns
`1F DA FE FE` (AT29C020, writes reach the chip, both boot blocks unlocked) — and the **full keypad
patch apply is validated end-to-end in the emulator** (`emu6811` now models the PA6+12 V `/OE` gate,
the AT29C020 command/program state machine, and the boot-block bits; see
`test_tools.py::VmFlashWriteModelTest`). The emulator run reproduces the exact patched image, and it
caught a real stub bug (the tWC delay clobbering its counter via the COP kick). The stub refuses any
page outside 0/12/13, and `flashtool.py` programs the stub sector first then the read_adc jsr sites
(so read_adc never points at a stub that isn't there yet). The one open item is the **256-byte
host→radio write burst dropping the applet link** — a transport issue, not the flash; a real
in-circuit sector program on the radio waits on that. A botched page-0 write is recoverable (boot/SBEP
live in the fixed region `0x8000+` and page 1, re-flashable), so proceed deliberately once the burst
transfer is fixed.
