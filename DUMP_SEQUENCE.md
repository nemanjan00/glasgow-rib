# GM600 codeplug dump sequence (for radio bring-up)

How to pull a codeplug (and other memory) off a GM600 over BUS+ with the applet. Companion to
[PROTOCOL.md](PROTOCOL.md) (wire framing) and [FIRMWARE.md](FIRMWARE.md) (radio internals).

> **RESOLVED by a live DPS capture (2026-11-03).** We ran real DPS against a real GM600 through
> the applet and captured a full Read and Write. Findings:
> - **The GM600 codeplug is the resident 640-byte internal EEPROM, `0x0D80`–`0x0FFF`.** DPS reads
>   it directly with 20× `0x11` reads of 32 bytes — **no `0x21`, no page-select, no separate
>   `0x7F1` personality region** on this model. The "runtime base address" below never mattered
>   for the GM600; the codeplug was in the readable space all along.
> - **Write path:** `0x10` (enter) → `0x19` (mode) → `0x17` write-blocks → `0x84` OK.
> - **Native frequency encoding:** `f = 375.000 MHz + (BE16 & 0x7FFF) × (bit15 ? 6.25 : 5 kHz)`.
> - A reference radio decoded fully (channels + identity). Full write-up and the Kaitai spec are
>   in the motorola-cps repo (`LIVE_CAPTURE.md`, `CODEPLUG_BITMAP.md`, `gm600_codeplug.ksy`).
>
> The read-only probe plan below is kept for other radios/variants, but for the GM600 just read
> `0x0D80`–`0x0FFF` directly.

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

## Offline analysis of the 64 KB dump (2026-11-03, no radio)

Searched `docs/gm600-807IYA0903-2026-11-03-cpu64k.bin` with the DPS `.per`/`.rad` field map
(frequencies are plain LE32 Hz; serial is ASCII; tuning grid references 375.000 MHz and band edges
403.625–469.625 MHz). Three independent negatives:

- The band-edge / reference calibration frequencies are **not present** as plain LE32 Hz anywhere.
- There is **no regular flat-Hz channel grid** — only code/data coincidences in the 400–475 MHz
  byte window, no paired Rx/Tx table with a sane step.
- The serial `807IYA0903` appears **only** at `0x0D88` (the EEPROM). A flat `.per` image would
  repeat it (serial is ASCII at `.per` 0x4C0); it does not.

**Conclusion: the radio does not hold a flat `.per`-format codeplug anywhere in the readable
64 KB.** It stores the codeplug in the **packed on-air format** (the field map's NP seg54
bit-position layout: WDPS_NP.EXE file `0x9B9E0`, 59×14-byte records, unpacker at file `0x795B4`),
which is a different encoding from the `.per` file. So scanning the dump with the `.per` field map
(probe-1 follow-up) cannot locate it — wrong encoding.

What this means for the plan:
- **No new radio read is needed to get the bytes.** The candidate regions — external SRAM
  (`1080`–`3FFF`, inferred) and EEPROM (`0D80`–`0FFF`) — are already fully in the 64 KB dump. A
  re-read would only show which bytes are live. The remaining work is **decoding**.
- **"Packed somewhere in `1080`–`3FFF`" is a hypothesis, not shown.** What is *proven* is only "no
  flat `.per` layout anywhere". `1080`–`3FFF` is clearly live working RAM (e.g. the self-referential
  record table at `0x2000`). If the reversed packed layout (NP seg54) matches nothing in the dump,
  **paging via some unseen command is back on the table** and a wire test is needed after all.
- To turn a raw dump into channels we need the **packed-layout map (NP seg54)** reversed and then
  tested against the dump. That is a no-radio static-RE task and is now the critical path.

On the agent's second question — where DPS gets the region addresses: **not** from the `0x13`/`82`
reply. `82 00 20 00` is a hardcoded constant block size (proven in FIRMWARE.md), not addresses. The
region addresses are codeplug-relative offsets held in DPS static (relocated) data, e.g. the
`0x4C5/0x4E1/0x4FD` offsets the DPS RE already found — offsets into the codeplug image, combined
with a base, not values readable from any radio reply.

## Do NOT send (not read-only — can detune or brick)

- `0x17` write, `0x20`/`0x22` tuning/softpot writes, `0x19`.
- **`0x26`** — jumps the CPU to an address taken from the request (factory backdoor); a wrong
  value runs garbage with interrupts off. See FIRMWARE.md.

Keep everything read-only until a DPS capture confirms the write path.
