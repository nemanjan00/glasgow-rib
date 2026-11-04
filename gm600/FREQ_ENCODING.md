# GM600 frequency encoding — SOLVED (live DPS capture, 2026-11-03)

> **This supersedes the earlier "NOT derivable / 0x0F67 is alignment data" conclusion.** A live
> DPS "Read Radio" of a real GM600 (serial `807IYA0903`, model M08RHF4CK3) proved that `0x0F67`
> **is** the channel table and gave the exact formula, confirmed on all four channels against
> DPS's own decode and the on-screen MHz values.

## The formula

```
f_Hz = 375_000_000 + (v & 0x7FFF) * step
step = 6.25 kHz  if (v & 0x8000)   else  5 kHz
```

`v` is the big-endian 16-bit stored word. Bit 15 selects the step size per word; bits 14..0 are an
offset above a **375.000 MHz reference**.

## Confirmed on radio 807IYA0903 (channel table at EEPROM `0x0F67`, 7-byte records)

| Ch | Rx word | step | → Rx MHz | Tx word | → Tx MHz | DPS GUI shows |
|----|---------|------|----------|---------|----------|---------------|
| 1 | `AC6B` | 6.25k (bit15) | 446.06875 | `AC6B` | 446.06875 | 446.068750 / 446.068750 |
| 2 | `AC75` | 6.25k | 446.13125 | `AC75` | 446.13125 | 446.131250 / 446.131250 |
| 3 | `AC6F` | 6.25k | 446.09375 | `AC6F` | 446.09375 | 446.093750 / 446.093750 |
| 4 | `31B5` | 5k (bit15 clear) | 438.62500 | `2BC5` | 431.02500 | 438.625000 / 431.025000 |

e.g. ch1: `0xAC6B & 0x7FFF = 0x2C6B = 11371`, bit15 set → `375.000 + 11371 × 6.25 kHz =
446.06875 MHz`. ch4 is a duplex channel (−7.6 MHz) using the 5 kHz step on both words.

The same formula decodes the **band-edge / tuning grid** words at `0x0DDA`–`0x0DE7`
(403.625 … 469.625 MHz), so it is consistent across the tuning block and the channel table.

## Why the 64 KB-dump search missed it (recorded so nobody repeats the dead end)

The earlier pass searched the dump for `value = freq / step` — a **pure scaling from 0 Hz** — in
every step and width, and found nothing systematic. It couldn't: the encoding is **375 MHz +
offset**, with a per-word step-select bit, so a channel word never equals `freq/step`. `0xAC6B`
only looked like "441 MHz" under a band-incompatible ×10 kHz reading; under the real
`375 + offset` formula it is 446.06875 MHz, squarely in the radio's band. **`0x0F67` is the channel
table, not alignment/softpot data** — the opposite of the earlier conclusion.

The synth/PLL reference constant still isn't located in the mapped firmware (it's likely in an
unmapped flash bank), but that no longer matters: the stored↔frequency formula is proven from the
radio's own data, which is what a host needs.

## Full codeplug decode

Channel record layout, PL-tone encoding, identity, block layout and checksums are in
[CODEPLUG.md](CODEPLUG.md).
