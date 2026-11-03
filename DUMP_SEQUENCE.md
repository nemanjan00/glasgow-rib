# GM600 codeplug dump sequence (for radio bring-up)

How to pull a codeplug (and other memory) off a GM600 over BUS+ with the applet. Companion to
[PROTOCOL.md](PROTOCOL.md) (wire framing) and [FIRMWARE.md](FIRMWARE.md) (radio internals).

The frame formats and the DPS read order below are **byte-confirmed by reversing the GM600 DPS
Dealer Programmer** (`WDPS_DP.EXE`). What is **not** known statically is where the codeplug
regions actually live — DPS keeps those addresses in runtime objects, not in code. So this doc
gives the proven pieces, a safe read-only probe plan to find the rest on the wire, and the
questions that plan should answer. (Full DPS static-RE provenance is in the separate motorola-cps
repo, `docs/DPS_EMEA_600SERIES_R00.03.02/DUMP_SEQUENCE.md` — not visible from this repo.)

## Prerequisite

**9 V on HOOK (J0400-14)** or the radio never transmits on BUS+ (see CONNECTION.md). No BUSY line.

## Frames (confirmed)

| Purpose | Send | Reply |
|---|---|---|
| Enter programming | `F1 10 FE` | ACK `50`, then **the radio resets** — wait for it to reboot before the next command |
| Read ID string | `F2 23 <idx> <cks>` | ACK, then `8B <idx> <ascii…>`; idx `0`=model code, `1`=serial, `3`=fw ver, `4`=cp ver |
| Max block size | `F1 13 FB` | ACK, then `82 00 20 00 ..` — a **constant**, not a real ceiling (FIRMWARE.md); 64-byte reads work |
| Read block | `F5 11 <len> <a_hi> <a_mid> <a_lo> <cks>` | ACK, then `80 <a_hi> <a_mid> <a_lo> <data…>` |
| Set address | `F4 21 00 <hi> <lo> <cks>` | ACK — **not seen on the codeplug read path in DPS** |

- `0x11` address is **24-bit big-endian** after the length byte. `len` ≤ `0x40` (64) works; `0x80`
  (128) is refused (a bare `80` instead of the ACK).
- `0x80` read reply echoes the 3 address bytes as sent, then the data (confirmed, FIRMWARE.md).

## Dumpable right now (read-only, safe)

1. `F1 10 FE`, wait for the reset/reboot.
2. `F2 23 00/01/03/04` — identity (model, serial, fw, cp version).
3. `F1 13 FB` — max block.
4. **Alignment block / internal EEPROM:** `0x11` reads over `0x000D80`–`0x000FFF` (640 bytes) in
   ≤64-byte chunks, advancing the address by the chunk size. No paging needed — this range is the
   68HC11 internal EEPROM and is always in the default map. Holds the serial (`807IYA0903CG`) and
   tuning. **Confirmed present and readable.**
5. **Full logical image:** `0x11` sweep of `0x000000`–`0x00FFFF` (a_hi = `00`) in ≤64-byte chunks.
   The radio reads its 64 KB CPU space; the top address byte is ignored and reads wrap every 64 KB.
   This captures RAM, I/O registers, the EEPROM, the fixed firmware bank and whichever flash bank
   is currently mapped. See the FIRMWARE.md memory map for what each range is.

## What to resolve on the wire (the point of probing)

FIRMWARE.md and the DPS static RE **disagree**, and only the radio can settle it:

- **FIRMWARE.md:** the personality codeplug is **not** in the default 64 KB map — it is paged, and
  `0x11` ignores the top address byte (wraps every 64 KB).
- **DPS static RE:** the codeplug read path uses **direct addressing with no `0x21`**, walking the
  address upward chunk by chunk.

Both can't be fully true. Probe to decide (all read-only):

1. **Does the top address byte matter? Answered on the radio (2026-11-03): no.** A 4 KB-step
   sweep with `a_hi` = `00`–`0F` returned identical data everywhere except the live RAM and
   register rows. 16-byte reads at `0x8000`, `0x4000` and `0x2000` with `a_hi` = `10`, `20`, …
   `F0` were byte-identical to `a_hi = 00`. Nothing above 64 KB is reachable with `0x11`. So
   either DPS's direct-addressed codeplug regions are **inside the 64 KB dump we already have**
   (`docs/gm600-807IYA0903-2026-11-03-cpu64k.bin`; check offline against the field map, e.g.
   SRAM at `1080`–`3FFF`), or something DPS sends pages them in first.
2. **Is `0x21` needed first?** Try a candidate codeplug read with and without a preceding
   `F4 21 00 hi lo`. If `0x21` changes what a later `0x11` returns, paging is via `0x21`.
3. **Find the personality.** It is `0x7F1` bytes (new) / `0x799` (old) = the `.per` body. Look for
   a 2 KB region that decodes as channel data (frequencies, per the DPS field map). It is not in
   the default low-64 KB image if FIRMWARE.md is right.

The authoritative answer is one **live DPS "Read Radio" capture** through the applet (`sniff` or
`pty` under 86Box): it shows the exact region addresses and whether `0x21` is used. The DPS master
read order (10 main regions, then record sets) is documented in the motorola-cps repo.

## Do NOT send (not read-only — can detune or brick)

- `0x17` write, `0x20`/`0x22` tuning/softpot writes, `0x19`.
- **`0x26`** — jumps the CPU to an address taken from the request (factory backdoor); a wrong
  value runs garbage with interrupts off. See FIRMWARE.md.

Keep everything read-only until a DPS capture confirms the write path.
