# Motorola GM600

Everything specific to the GM600 (600 Series, MPT1327 trunked mobile): wiring, the programming
protocol, the codeplug format, firmware notes and host tools. The applet itself is generic; see
the [top-level README](../README.md).

With a Glasgow and a lab supply you can read and write a GM600 codeplug, with no RIB and no DPS.
DPS (`WDPS_DP.EXE`) can also run under QEMU with its COM port bridged to the applet.

## Start here

1. [CONNECTION.md](CONNECTION.md): wiring. BUS+ is J0400 pin 6, GND is pin 7, and **about 9 V on
   pin 14 (HOOK) from a separate supply**. Without HOOK the radio receives but never answers.
2. [PROTOCOL.md](PROTOCOL.md): SBEP framing, commands, and the DPS read/write sequence captured
   from a real radio.
3. [tools/](tools/): dump the codeplug (`dump_codeplug.py`), read (`sbep_client.py`), write
   (`sbep_write.py`), and the QEMU + DPS capture rig.

## Reference

| File | Contents |
|---|---|
| [CODEPLUG.md](CODEPLUG.md) | The 640-byte codeplug: block map, channel table, PL tones, identity, option bits, checksums |
| [gm600_codeplug.ksy](gm600_codeplug.ksy) | Kaitai Struct spec for the codeplug (open it with a dump in ide.kaitai.io) |
| [FREQ_ENCODING.md](FREQ_ENCODING.md) | Stored word ↔ frequency: `375 MHz + (v & 0x7FFF) × (bit 15 ? 6.25 : 5 kHz)` |
| [DUMP_SEQUENCE.md](DUMP_SEQUENCE.md) | How the codeplug was located, and the read-only probe plan for other variants |
| [FIRMWARE.md](FIRMWARE.md) | 68HC11 memory map, flash banking, SBEP dispatch table, from a 64 KB dump |
| [HARDWARE.md](HARDWARE.md) | Accessory connector reference and repeater tap points (COR, PL, flat audio) |
| [assets/](assets/) | Wiring diagrams used by CONNECTION.md |

## Status (2026-11-04)

- **Working:** read and write over BUS+, verified on a real radio (serial `807IYA0903`).
  Conventional channels, PL tones, MPT identity, per-channel option bits and timers are decoded.
- **The codeplug is the CPU's 640-byte internal EEPROM, `0x0D80`–`0x0FFF`.** There is no separate
  personality region. DPS writes only `0x0E13`–`0x0FFB`.
- **Not yet safe to write freely:** the checksums are additive, but only the personality checksum
  byte (`0x0FF4`) is located, and its exact range isn't pinned. Edits that keep each block's sum
  unchanged are safe; anything else may make DPS reject the radio on the next read.
- **Not decoded:** the MPT1327 network block (`0x0E13`–`0x0F07`, fields enumerated but not
  byte-bound) and the factory alignment area.
- **Firmware flashing is a separate, untried problem** (68HC11 bootstrap via MODA/MODB, VPP).
- **Keep a pre-write dump.** DPS blanks the factory field at `0x0FF5`–`0x0FFB` on every write, and
  it can't be recovered from the radio afterwards.
