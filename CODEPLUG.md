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
| +6 | marker | **constant `0x08`** on every channel — structural, *not* options | confirmed |

Frequency formula detail and per-channel proof: [FREQ_ENCODING.md](FREQ_ENCODING.md). PL/TPL tone
index table: motorola-cps `docs/.../FIELD_HELP_REFERENCE.md`.

This radio: 4 channels at `0x0F67/0x6E/0x75/0x7C`; record at `0x0F83` has Tx `0xFFFF` (end).

## Other decoded fields

- **MPT own-number (identity):** BE16 at `0x0F0B` = `0x0142` = **322**.
- **Serial:** ASCII `807IYA0903CG` at `0x0D88` (also returned by `23 01`).
- **Transmit timeout:** enabled + duration 10 s (GUI), stored in the personality block.

## Per-block checksums — `sum ≡ 0xFF` (0xFF−sum)

Each block carries its own checksum so that **the block's bytes sum to `0xFF` (mod 256)** — the
classic `cks = 0xFF − sum(rest)` scheme. DPS validates each on read and rejects the whole read on
mismatch, with a block-specific message: `Radio / Network / Personality Data Checksum Failure`.

Proven by write-probe:
- A **net-zero** change inside the personality block (e.g. `+1` on one byte, `−1` on another)
  **passes** the personality checksum → confirms the sum model and that both bytes are in-block.
- A **non-zero** delta fails with the matching block's error. This localized the blocks: byte
  `0x0F06` is in the **Network** block; the channel region (`0x0F6D`–`0x0F74` at least) is in the
  **Personality** block.
- The radio **writes whatever bytes it is told** — every `17` returns `84` (OK) regardless of
  codeplug validity. Validation happens in DPS on *read*, not in the radio on write. So a host must
  compute the per-block `0xFF−sum` checksums itself for the radio to be programmable by DPS
  afterwards (the radio itself will run a bad-checksum codeplug, but DPS won't re-read it).

(The raw 640 bytes summing to `0xB0`, noted in PROTOCOL.md, is just because the checksum is
*per-block*, not over the whole image.)

## Per-channel options — located, not yet bit-bound

The GUI (Edit → Conventional Personality) exposes per-channel **Tx Power, Tx Inhibit, Rx PL, Tx PL,
Monitor, Data**. Rx/Tx PL are the `+4/+5` bytes above. The other four booleans are **not in the
7-byte channel record**:

- `+6` is a constant `0x08` on every channel despite differing freq/PL, so it is neither a
  per-channel checksum (would vary) nor an options byte at default.
- Writing `+6 = 0x08|bit` → DPS "**invalid values: Conventional Personality**"; clearing bit3
  (`0x07`) → "**Personality data is invalid**". So `0x08` is a required value, not a free bitfield.

They live in a **separate per-channel structure in the personality block** — most likely the run of
**13 × `00 00 00 80` 4-byte entries at `0x0F33`–`0x0F66`** (one per possible channel, default
option word, flag `0x80`), which precedes the freq table. Binding the exact Power/Inhibit/Monitor/
Data bits there needs DPS-generated contrast (a modified codeplug DPS writes, which needs a real
display — the headless VM's mouse is dead and its grid combos ignore injected keys) **or** the
conventional-personality field-descriptor table in `WDPS_NP.EXE` (the analogue of the decoded
network-block table at NP `0x9B9E0` / IDA `dseg54`). **Not yet done.**

Validation constraints seen while probing: **Tx Inhibit is incompatible with Tx PL**, and
frequencies must sit on the channel step grid (off-step → "Personality data is invalid").

## How much is reversed

- Read/write wire protocol + container (block map, `0xFF−sum` checksums): **~90–95 %**, proven live.
- Conventional channel programming (freq, PL, channel table, identity, timeout): **~90–95 %**.
- Whole-codeplug field content: **~50–60 %** — the undecoded **MPT1327 trunking network block** is
  the biggest remaining chunk, followed by the per-channel option bits and the factory
  alignment/softpot region.

## Cross-reference

Full RE write-ups (protocol provenance, `.per`/`.net`/`.rad` file formats, field catalogs,
model/version matrices, Kaitai spec) are in the Motorola CPS RE project,
`docs/DPS_EMEA_600SERIES_R00.03.02/` (`LIVE_CAPTURE.md`, `CODEPLUG_BITMAP.md`,
`gm600_codeplug.ksy`, `FIELD_HELP_REFERENCE.md`, `FILE_FORMAT.md`, `FIELD_MAP.md`).
