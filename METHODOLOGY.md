# Reverse-engineering a radio: methodology

How the GM600 was worked out, written generically so it transfers to the next radio. The applet
([README.md](README.md)) is the PHY; this is the process around it. The GM600 is the worked
example throughout — concrete facts live in [`gm600/`](gm600/).

The whole thing is one loop: **state a hypothesis, run the one cheapest experiment that can
disprove it, let the result decide, update, repeat.** Most steps below are just that loop with a
different instrument.

## 1. Protocol and file format — static, no radio

Get the vendor programming software. If it needs a dead OS, run it in a VM (the GM600 DPS is
16-bit Windows 3.1 under QEMU). Disassemble it (IDA) and recover two things:

- **The serial framing** — opcodes, length/escape rules, checksum. (GM600: an SBEP variant,
  `F0|len` short frames / `FF` long frames, checksum `0xFF − sum`. See [gm600/PROTOCOL.md](gm600/PROTOCOL.md).)
- **The codeplug file format** — any obfuscation/crypto and the field layout. (GM600 `.net`/`.rad`
  use a fixed 7-byte XOR key.)

Output: enough to speak the wire and read/write the vendor's own files offline.

## 2. A RIB-less PHY — Glasgow

Implement the wire protocol as Glasgow gateware, not a bit-banged USB-serial dongle. The timing is
then deterministic (see the open-drain / echo / atomic-burst notes in [README.md](README.md)):
messages go out back to back and nothing depends on host or USB timing. (Collisions aren't handled
in the PHY — it sends the whole frame; the host detects one by the echo not matching.) Prove it
live by reading the
codeplug and writing it back **unchanged** — a clean round-trip is the first real milestone.

## 3. Decode fields by write-contrast — the workhorse

The technique that did most of the work:

1. Change **exactly one** field in the vendor tool.
2. Write Radio.
3. Re-dump and `diff` against a baseline dump.

The bytes that moved are that field. Keep the experiment clean:

- **Isolate one variable.** Two edits at once are unreadable.
- **Filter the noise.** Every write also perturbs save/header metadata and a checksum byte or two;
  those deltas are not your field. (GM600: DPS blanks a trailing factory field on every write,
  so it changes on the first one, and the personality checksum follows every data change.)
- **Checksums:** a net-zero edit (one byte +1, another −1) passes any additive checksum, which both
  confirms "additive" and lets you write modified data before you've pinned the checksum.

Capture results as a **runnable spec** (Kaitai `.ksy`) plus prose, so "the format" is something a
machine parses, not just English. (GM600: [gm600/gm600_codeplug.ksy](gm600/gm600_codeplug.ksy).)

## 4. Cross-check three ways

Confirm each fact by more than one path where you can:

- **Static** — file format, a field/registry table in the binary.
- **Dynamic** — the write-contrast above.
- **Firmware** — disassemble the radio's own code.

When they disagree, firmware and physics win over documentation and guesses. (GM600 example: a
table that looked like a field→offset map turned out to be UI layout; the disassembly of its reader
settled it.)

## 5. Hardware signals — the schematic

For anything analog the codeplug can't give you (repeater COR/PL, flat audio, programming/boot
pins), read the service-manual schematic and find the pins on the audio/signalling IC and the MCU.
(GM600: squelch/PL/flat-audio tap points in [gm600/HARDWARE.md](gm600/HARDWARE.md).) Confirm by
probing for the signal, not by trusting a pin number off a scan.

## Toolbox

| Tool | Use |
|---|---|
| Glasgow | the RIB-less PHY (gateware, deterministic timing) |
| IDA | disassemble the vendor software and the radio firmware |
| Kaitai Struct | the codeplug as a runnable, browser-viewable spec |
| QEMU | run dead vendor software (Win3.1 / DOS) |
| Logic analyzer | passively trace an external bus (ROM, SPI, the serial line) |
| Chip programmer (e.g. XGecu T48/T76) | read an external flash/EPROM directly |
| USBDM | debug access for BDM-era CPUs (HC12/HCS08/…), **not** the HC11 |

## Untested approaches — ideas, not done here

None of these were needed for the GM600 conventional surface; they are leads for going deeper or
for other radios.

- **Dump the firmware** (we only mapped the codeplug). On a 68HC11: bootstrap mode (MOD pins at
  reset → a loader over the SCI), or read the external flash with a programmer, or a logic-analyzer
  snoop of ROM fetches (no cache/DMA on the '11, so the bus is a faithful execution trace), or the
  radio's own "jump to address" SBEP opcode to drive a bank dump. See [gm600/FIRMWARE.md](gm600/FIRMWARE.md).
- **Reverse the file→radio transform** to pin the trunking/network-block byte offsets (left out of
  scope here; the field set is enumerated but not byte-bound).
- **Differential firmware RE** — diff two model variants (e.g. a 4-channel vs an 8-channel image) to
  find and possibly lift a feature limit.
- **Newer radios** — if the CPU has BDM (HC12/HCS08 and later), USBDM gives direct memory/debug
  access and most of steps 1–4 collapse.
- **Automate the write-contrast** — script the vendor GUI and the dump+diff to map many fields in
  bulk instead of one at a time.

## Pitfalls hit along the way

- **Single-client PHY vs an auto-reconnecting client.** The vendor tool (or its VM serial chardev)
  can re-grab a single-client socket between your reads. Symptom: the first read is fine, the next
  fails. Give yourself exclusive access while you read.
- **Kaitai enum names that are YAML booleans.** `yes`/`no`/`on`/`off` parse as `true`/`false` under
  YAML 1.1; quote them.
- **Coincidental terminators.** A `0xFFFF` after a table was padding from the next structure, not an
  end marker — it breaks as soon as that structure grows. Prefer a known count over a sentinel.
- **Checksum ranges hidden by padding.** A run of `0xFF` means many candidate range-ends give the
  same sum; the checksum alone can't pin the boundary. Settle it with a contrast write just inside
  vs just outside.
- **Pin numbers off a scanned schematic.** Grid designators (a BGA's `A4`, `H1`) are easy to
  misread by one; verify, and tap the accessible end of a net (a via or a QFP leg), not a BGA ball.
- **One variable per experiment.** Treat header, save-metadata and checksum deltas as noise to be
  subtracted, not as findings.
