# GM600 codeplug dump sequence and reversing notes

How to pull the codeplug (and other memory) off a GM600 over BUS+, and the methods and DPS
binary references used to find it, for future reversing of this and other radios. Companion to
[PROTOCOL.md](PROTOCOL.md) (wire framing), [CODEPLUG.md](CODEPLUG.md) (decode) and
[FIRMWARE.md](FIRMWARE.md) (radio internals).

## Summary

- **The GM600 codeplug is the 640-byte internal EEPROM, `0x0D80`–`0x0FFF`.** DPS reads it with
  20 × 32-byte `0x11` reads: no `0x21`, no page select. There is no separate `0x7F1`-byte
  personality region in the radio; `0x7F1` is the size of DPS's `.per` file.
- **Write path** (live DPS capture): `23`/`23`/`13` (queries) → `0x17` write blocks (15 × 32 bytes,
  then 2 bytes at `0x0FF3`, a `13`, then 7 bytes at `0x0FF5`; each → `0x84` OK) → a final `0x10`.
  No `0x19`. Writes cover only `0x0E13`–`0x0FFB`; `0x0D80`–`0x0E12` (serial and alignment) and
  `0x0FFC`–`0x0FFF` are never written.
- **Frequency encoding:** `f = 375.000 MHz + (BE16 & 0x7FFF) × (bit15 ? 6.25 : 5 kHz)`
  ([FREQ_ENCODING.md](FREQ_ENCODING.md)).
- Decode: [CODEPLUG.md](CODEPLUG.md) and [gm600_codeplug.ksy](gm600_codeplug.ksy). The live-capture
  write-up is in the motorola-cps repo (`LIVE_CAPTURE.md`, `CODEPLUG_BITMAP.md`).

## Prerequisite

**9 V on HOOK (J0400-14)** or the radio never transmits on BUS+ (see
[CONNECTION.md](CONNECTION.md)). No BUSY line.

## Frames (confirmed)

| Purpose | Send | Reply |
|---|---|---|
| Enter programming | `F1 10 FE` | ACK `50`, then **the radio resets**; wait for it to reboot before the next command |
| Read ID string | `F2 23 <idx> <cks>` | ACK, then `8B <idx> <ascii…>`; idx `0` = model code, `1` = serial, `3` = firmware version, `4` = codeplug version |
| Max block size | `F1 13 FB` | ACK, then `82 00 20 00`: a **constant** in the firmware, not a real ceiling ([FIRMWARE.md](FIRMWARE.md)); 64-byte reads work |
| Read block | `F5 11 <len> <a_hi> <a_mid> <a_lo> <cks>` | ACK, then `80 <a_hi> <a_mid> <a_lo> <data…>` |
| Set address | `F4 21 00 <hi> <lo> <cks>` | ACK. **Not used by DPS with a GM600** |

- The `0x11` address is sent as 24-bit big-endian after the length byte, but the radio uses only
  the low 16 bits (see below). `len` ≤ `0x40` (64) works; `0x80` (128) is refused with a bare `80`
  in place of the ACK.
- The `0x80` read reply echoes the 3 address bytes as sent, then the data (confirmed in
  [FIRMWARE.md](FIRMWARE.md)).

## Dumping

1. `F1 10 FE`, then wait for the reset and reboot.
2. `F2 23 00/01/03/04`: identity (model code, serial, firmware, codeplug version).
3. `F1 13 FB`: max block size (constant).
4. **Codeplug:** `0x11` reads over `0x0D80`–`0x0FFF` (640 bytes) in chunks of up to 64 bytes. This
   is the 68HC11's internal EEPROM, always in the memory map. `tools/dump_codeplug.py` does this
   with 32-byte reads and checks every reply.
5. **Full logical image (optional):** a `0x11` sweep of `0x0000`–`0xFFFF` in chunks of up to 64 bytes.
   This captures RAM, the register block, the EEPROM, the fixed firmware and whichever flash bank
   is mapped at `0x4000` at the time ([FIRMWARE.md](FIRMWARE.md) has the map). It takes about
   2 minutes at 9600 baud.

   > **Caveat (2026-11-12):** observed while the flash had a bad joint (`0x23` was also dead then);
   > re-verify on the repaired radio before relying on it.
   >
   > **Read the full image in _normal_ mode — do not send the step-1 `0x10` first.** Verified
   > in-circuit on `807IYA0903` (2026-11-06): once the radio is in programming mode (`0x10`) it
   > answers only codeplug-region reads (`0x0D80`–`0x0FFF`); `0x11` reads of `0x8000`, `0x4000`,
   > etc. come back as **echo with no reply**. Without `0x10` the same reads return real data —
   > `0x8000` = fixed firmware, `0x4000` = bank 04 (its dispatch table and `82 36` unsupported-stub
   > fill, consistent with the off-chip `motofix.BIN`). So the codeplug dump (step 4, after `0x10`)
   > and the full-image sweep (step 5, no `0x10`) are two different sessions.
   >
   > **This only ever reaches the one flash bank currently mapped at `0x4000`.** You cannot dump the
   > other 15 banks by writing the bank-select register (`0x1058`) over `0x17`: the `0x11`/`0x17`
   > handlers themselves live in that banked window (bank 04), so changing the bank swaps the running
   > handler out from under the CPU and it crashes. A full 256 KB flash dump needs a small RAM stub
   > (upload via `0x17`, run via `0x26`) that walks the banks and streams each out the SCI — this is
   > **implemented in [tools/ramdump.py](tools/README.md), and its output is byte-for-byte identical
   > (same SHA-256) to the off-board T48 read (`motofix.BIN`), verified in-circuit on `807IYA0903`.**
   > (The chip can also be read off-board on a programmer; that is what `motofix.BIN` is.)

## Probe methods (read-only, reusable on other radios and variants)

These were used to locate the GM600 codeplug before a DPS capture was available. They're worth
repeating on any radio whose memory layout is unknown.

1. **Does the top address byte matter?** Read the same `a_mid/a_lo` with `a_hi = 00`, then `01`,
   `02`, … and compare. **GM600 result (2026-11-03): it doesn't.** A 4 KB-step sweep with `a_hi` =
   `00`–`0F` returned identical data everywhere except the live RAM and register rows, and 16-byte
   reads at `0x8000`, `0x4000` and `0x2000` with `a_hi` = `10`, `20`, … `F0` were byte-identical to
   `a_hi = 00`. Nothing above 64 KB is reachable with `0x11`.
2. **Is `0x21` needed first?** Do a read with and without a preceding `F4 21 00 hi lo` and compare.
   On the GM600 it isn't needed; the capture shows DPS never sends it. Only try this with values
   taken from a DPS capture, never guessed ones.
3. **Find structure, not values.** Look for a region with the size and shape the software expects
   (record tables, checksums, strings such as the serial). Reading twice and diffing separates
   live RAM and registers from stored data.

The authoritative answer is a live DPS "Read Radio" capture through the applet. For the GM600 it
was done under QEMU with COM1 bridged to the applet socket ([tools/README.md](tools/README.md)),
and showed the exact addresses.

## Facts from the 64 KB dump analysis (still valid)

From `docs/gm600-807IYA0903-2026-11-03-cpu64k.bin`, checked against the DPS `.per`/`.rad` field
map. In those files, frequencies are plain LE32 Hz, the serial is ASCII, and the tuning grid uses
a 375.000 MHz reference with band edges 403.625–469.625 MHz. The radio stores none of it that way:

- **Frequencies are not stored as plain Hz.** The band edges (403.625–469.625 MHz) and the
  375.000 MHz reference don't appear as LE32 Hz anywhere, and there's no flat-Hz channel grid. The
  real encoding is the 375 MHz offset word ([FREQ_ENCODING.md](FREQ_ENCODING.md)).
- **The full serial `807IYA0903` appears only at `0x0D88`** (the EEPROM). RAM at `0x02D2` holds a
  working copy of the start of the EEPROM block, with the serial cut to `807IYA`.
- **No flat `.per` image exists in the radio.** In a `.per` file the serial is ASCII at offset
  `0x4C0`; nothing in the 64 KB view matches that layout. The radio stores only the packed
  640 bytes, and DPS converts between the two.
- **`0x1080`–`0x3FFF` is live working RAM**, e.g. a self-referential table of 10-byte records at
  `0x2000` (`20 0C`, `20 16`, `20 20` …), not stored codeplug data.
- **A checksum scan finds nothing:** windows of `0x7F1` or `0x799` bytes summing to `0xFF` occur
  only at chance level in the dump.

## DPS references for reversing

- **Packed-layout table:** `WDPS_NP.EXE` holds a bit-position table for the packed network
  block at file offset `0x9B9E0` (IDA `dseg54`): 59 records of 14 bytes. Its unpacker is at file
  offset `0x795B4`. This is the place to look when binding the network block's fields.
- **Region addresses:** DPS keeps codeplug region addresses as codeplug-relative offsets in its
  static (relocated) data, e.g. `0x4C5`, `0x4E1`, `0x4FD`, combined with a base at run time. They
  don't come from any radio reply.
- **Read order:** DPS reads 10 main regions, then record sets. Details and full DPS static-RE
  provenance: motorola-cps repo, `docs/DPS_EMEA_600SERIES_R00.03.02/DUMP_SEQUENCE.md`.

## Do not send (not read-only: can detune or brick)

- `0x17` writes without a pre-write dump and correct checksums ([CODEPLUG.md](CODEPLUG.md)).
- `0x20`/`0x22` tuning and softpot writes, and `0x19`, until captured from DPS.
- **`0x26`**: jumps the CPU to an address taken from the request (factory backdoor); a wrong value
  runs garbage with interrupts off. See [FIRMWARE.md](FIRMWARE.md).
