# GM600: notes for agents

GM600-only facts. Generic applet notes are in the top-level `CLAUDE.md`. Start with `README.md`
in this folder; it indexes the docs and has the current status.

## Hardware facts (from the schematic; confirmed on a radio unless noted)

- J0400 (rear 16-pin accessory connector): **pin 6 = BUS+** (through R0422, 270 Ω), **pin 7 =
  GND**. Pin 8 SCI_DATA_OUT is dead (Q0421 and R0428 not fitted). Pin 13 ANALOG_GND/SW_B+ may carry
  battery voltage.
- **HOOK_RSS (J0400-14) needs about 9 V for programming** (lab PSU at 9 V, current limit set, draws
  about 1 mA). Above about 5.7 V, VR0102 (5.1 V zener) → Q0105 fires the CPU IRQ and, through
  Q0122, enables the radio's BUS+ transmitter. Without it the radio receives but never answers.
  Never wire HOOK to Glasgow. Keep it under 12 V (14 V zener VR0415).
- The control head is **not** on BUS+: it uses SPI (J0101 SPI_CLCK_BUF/SPI_DATA_BUF/LCD_CE_BUF/
  LED_CE) and analog key ladders. BUS+ is silent in normal use, so `sniff` on an idle radio shows
  nothing.
- BUS+ pull-up is R0130, 10 kΩ. **There is no BUSY line**: don't use `--busy`.
- Physical pin layout of J0400 isn't recorded; go by the moulded numbers.
- Only wires plus the 9 V HOOK supply are needed: no MAX232, LM311 or diode.

## Protocol and codeplug facts

- ACK is `50`, NAK `60`. A bare `80` in place of the ACK means the request was refused (e.g. a
  128-byte read; 64 bytes is the most that works).
- `10` (enter programming mode) ACKs, then **resets the radio**. After the reset it answers
  commands without another `10`.
- `11` reads the CPU's 64 KB logical space; the top address byte is ignored (checked for every
  value `00`–`FF`).
- **The codeplug is the 640-byte internal EEPROM, `0x0D80`–`0x0FFF`.** DPS reads it as 20 × 32
  bytes and writes only `0x0E13`–`0x0FFB` (15 × 32 bytes, then 2 and 7). It never sends `21` or
  `19` to a GM600.
- Checksums are additive per block. The personality checksum byte is `0x0FF4`; its range isn't
  pinned. The network block's checksum byte isn't located.
- DPS blanks the factory field `0x0FF5`–`0x0FFB` (`20 00 00 00 00 11 DC` on the reference radio) on
  every write. The pre-write dump `docs/gm600-807IYA0903-2026-11-03-alignment-0D80.bin` is the
  only copy of the original.
- **Never send** `17`, `20`, `22`, `19` or `26` without a reason and a backup. `20`/`22` touch
  tuning; `26` jumps to an address taken from the request.

## Working with the radio

- The owner may be using Glasgow for DPS sessions (QEMU → `socat` → applet `socket` on
  `127.0.0.1:2324`). Check `ps` for a running `glasgow run rib` before using it, and ask.
- The applet's `socket` serves one client at a time.
- Capture logs from DPS sessions are in `~/captures/` (not in git).

## Next steps

1. Pin the personality checksum range: a contrast write to a byte just inside and just outside
   `0x0FDF`. Then locate the network block's checksum byte.
2. Time the reset after `10`; capture what `20`/`22` do from DPS (don't guess).
3. Optional: bind the MPT1327 network block (`0x0E13`–`0x0F07`).

## Reference material (`docs/` is not in git; re-fetch on a new machine)

- GM600 UHF service manual (schematics): `motorola_gm600_uhf_sm.pdf`, supplied by the owner.
  Pages 5 and 7 are the controller and I/O schematics.
- GM Series (GM340 family) detailed service manual 6864115B62-C:
  https://manuals.repeater-builder.com/mo-files1/-GM%20Series/GM%20Series%20Detailed%20service%20manual%206864115B62-C.pdf
- GM600 user manual: `115e93.pdf`, supplied by the owner. Nothing about the bus.
- 16-pin connector diagram:
  https://www.repeater-builder.com/motorola/maxtrac/interfacing/maxtrac-16-pin-plug.gif
- Fuller reverse-engineering write-ups (DPS, file formats, field catalogs): the motorola-cps repo,
  `docs/DPS_EMEA_600SERIES_R00.03.02/`.
