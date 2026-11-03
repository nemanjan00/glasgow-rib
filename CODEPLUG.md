# GM600 codeplug decode

The whole GM600 codeplug is the **640-byte 68HC11 internal EEPROM, radio `0x0D80`–`0x0FFF`**
(read with 20 × 32-byte `11` reads; see [PROTOCOL.md](PROTOCOL.md)). DPS expands it into a
`0x7F1`-byte `.per`/`.rad` file, but the radio stores only these 640 packed bytes. Offsets below
are radio addresses. Ground truth: a live DPS "Read Radio" of serial `807IYA0903`, model
`M08RHF4CK3`, plus write-probes driven by the tools in [tools/](tools/) (2026-11-03).

## Block layout

| Range | Block | R/W | Contents |
|---|---|---|---|
| `0x0D80`–`0x0E12` | Radio / alignment | **read-only** (never rewritten) | serial `807IYA0903CG` (ASCII @ `0x0D88`), band-cal / tuning-grid words (@ `0x0DDA`), softpot |
| `0x0E13`–`~0x0F32` | Network (MPT1327) | read-write | trunking/network parameters — **mostly not decoded**; MPT own-number lives here |
| `0x0F33`–`0x0FFB` | Personality (conventional) | read-write | per-channel option table, channel freq/PL table, timers, block checksum |

DPS's write touches only `0x0E13`–`0x0FFB`; `0x0D80`–`0x0E12` (serial + alignment) and
`0x0FFC`–`0x0FFF` are left alone.

## Channel table — `0x0F67`, 7-byte records

Terminated by the first record whose **Tx word = `0xFFFF`**.

| +off | field | encoding | status |
|---|---|---|---|
| +0 | Rx freq | BE16, `375.000 MHz + (v&0x7FFF)×(bit15?6.25:5 kHz)` | confirmed |
| +2 | Tx freq | BE16, same formula; `0xFFFF` = end-of-table | confirmed |
| +4 | Tx PL | tone-list index, 0 = none (ch4 = `0x13` = 19 = 123.0 Hz) | confirmed |
| +5 | Rx PL | tone-list index, 0 = none | confirmed |
| +6 | options | `0x08` default; **bit4 (`0x10`) = Monitor** (confirmed); bit3 always set | partly bound |

Frequency formula detail and per-channel proof: [FREQ_ENCODING.md](FREQ_ENCODING.md). PL/TPL tone
index table: motorola-cps `docs/.../FIELD_HELP_REFERENCE.md`.

This radio: 4 channels at `0x0F67/0x6E/0x75/0x7C`; record at `0x0F83` has Tx `0xFFFF` (end).

## Other decoded fields

- **MPT own-number (identity):** BE16 at `0x0F0B` = `0x0142` = **322**.
- **Serial:** ASCII `807IYA0903CG` at `0x0D88` (also returned by `23 01`).
- **Transmit timeout:** enabled + duration 10 s (GUI), stored in the personality block.

## Per-block checksum — additive, but exact scheme NOT established

DPS validates the read with per-block checksums and rejects the whole read on a mismatch, with a
block-specific message: `Radio / Network / Personality Data Checksum Failure`. Write-probes
established only this much:

- The checksum is **additive**: a **net-zero** change inside a block (e.g. `+1` on one byte, `−1`
  on another) **passes**; any **non-zero** net delta **fails** with that block's error. This also
  localized the checksum domains — byte `0x0F06` is in the **Network** block; the channel region
  (`0x0F6D`–`0x0F74`) is in the **Personality** block.
- Additivity is **all it proves.** It does **not** establish the target constant, the exact block
  boundaries, or where the checksum byte lives — a net-zero change passes for *any* target and *any*
  boundaries. **Correction (caught by an independent pre-write dump):** the earlier "each block sums
  to `0xFF`" claim here was wrong. Summing the ranges in the layout table gives Radio `0xD1`,
  Network `0xDD`, Personality `0x06` — none is `0xFF`, no two-way split of `0x0E13`–`0x0FFB` makes
  each half sum to `0xFF`, and the Personality sum isn't even invariant across the DPS write
  (`0x06` → `0xF2`, see the trailing-field note below), so these boundaries aren't right either.
- The radio **writes whatever bytes it is told** — every `17` returns `84` (OK) regardless of
  codeplug validity; DPS validates on *read*, not the radio on write.

**So `tools/sbep_write.py`'s balancing trick works only because a net-zero change never alters a
block's sum.** For the PERSONALITY block the stored checksum byte is now found — radio `0x0FF4`,
additive (see "Personality checksum byte" below), so a genuinely modified personality is writable
by recomputing it. The NETWORK and RADIO blocks' checksum bytes/ranges are still unlocated. (The raw
640 bytes summing to `0xB0` is just because the checksum is per-block.)

## DPS clears a 7-byte trailing field on write

DPS's captured write changed exactly **7 bytes** beyond re-writing the data: `0x0FF5`–`0x0FFB`,
from `20 00 00 00 00 11 DC` in the factory image to `FF FF FF FF FF FF FF`. Likely a
programming-history / flag / counter field (`11 DC` looks like a value, not padding). Consequences:

- `tools/sbep_write.py --restore` replays DPS's captured write, so it restores the **post-DPS**
  state (`FF` here) — **not** the factory original.
- The factory bytes survive only in the **pre-write dumps** (`native-eeprom.bin`, and the lab's
  `docs/gm600-807IYA0903-2026-11-03-alignment-0D80.bin`) — keep them.
- What those 7 bytes encode is not yet known; worth decoding.

## Per-channel options — in the `+6` byte (contrast-confirmed)

The GUI (Edit → Conventional Personality) exposes per-channel **Tx Power, Tx Inhibit, Rx PL, Tx PL,
Monitor, Data**. Rx/Tx PL are the `+4/+5` bytes. The booleans are bit-packed in the **`+6` byte**
(default `0x08`), proven by a clean DPS write-contrast:

- Set **ch1 Monitor = Yes** in DPS, Write Radio, read back, diff vs baseline → exactly two bytes
  changed: **`0x0F6D` (ch1 `+6`) `08 → 18`** and the checksum byte `0x0FF4` (below). So
  **Monitor = `+6` bit4 (`0x10`)**, Yes=1.
- bit3 (`0x08`) is always set (clearing it → "Personality data is invalid"). Tx Power / Tx Inhibit /
  Data occupy other `+6` bits — not yet individually bound (same contrast method: toggle one in DPS,
  diff). Some raw bit patterns are rejected by DPS validation (**Tx Inhibit is incompatible with Tx
  PL**; and frequencies must stay on the step grid).

This supersedes an earlier note here that guessed `+6` was "not options / a separate `0x0F33`
table" — that was wrong; `+6` is the options byte.

## Personality checksum byte @ `0x0FF4` (additive)

The same Monitor contrast moved **`0x0FF4`: `E6 → F6`** — i.e. the data went up `+0x10` and this
byte went up `+0x10`. So the personality checksum is **additive, stored (not `0xFF−sum`), low byte
of a running sum, at radio `0x0FF4`**. The summed range includes the channel region and ends before
`0x0FF4`; candidate extent `~0x0F1D–0x0FE0` (sums to `0xE6` on baseline) — not yet pinned exactly.
A host writing a modified personality must recompute this byte (`+= Σ of its data-byte deltas`).
(The Network block has its own checksum — changing `0x0F06` broke `Network Data Checksum Failure`.)

## How much is reversed

- Read/write wire protocol: **~90–95 %**, proven live. Container (block map, checksums): partial —
  additive per-block checksum shown, but target/boundaries/checksum-byte not yet pinned.
- Conventional channel programming (freq, PL, channel table, identity, timeout): **~90–95 %**.
- Whole-codeplug field content: **~50–60 %** — the undecoded **MPT1327 trunking network block** is
  the biggest remaining chunk, followed by the per-channel option bits and the factory
  alignment/softpot region.

## Cross-reference

Full RE write-ups (protocol provenance, `.per`/`.net`/`.rad` file formats, field catalogs,
model/version matrices, Kaitai spec) are in the Motorola CPS RE project,
`docs/DPS_EMEA_600SERIES_R00.03.02/` (`LIVE_CAPTURE.md`, `CODEPLUG_BITMAP.md`,
`gm600_codeplug.ksy`, `FIELD_HELP_REFERENCE.md`, `FILE_FORMAT.md`, `FIELD_MAP.md`).
