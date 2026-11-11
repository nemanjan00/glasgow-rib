# GM600 display: one photo per segment

Taken 2026-11-11 on the bench radio (serial 807IYA0903) with the webcam, backlight on, by
`tools/lcd_photos.py`. The radio runs `tools/lcd_raw.S`, which drives the display controller with its own
SPI code (no firmware calls), so nothing else is drawn on the glass.

The glass is one controller (SPI chip-select `0x61` on `0x107E`, SPCR `0x52`) carrying 3 seven-segment
digits and 9 icons; `all_on.jpg` shows everything, `blank.jpg` nothing.

## `struct/` — one bit of the firmware's 5-byte digit struct `[b0 b1 d0 d1 d2]`

This is what firmware entry 5 (`0x9018`) takes; `lcd_text.pack()` turns it into the wire bytes.
Sheets: `sheet_digit_bits.png`, `sheet_icon_bits.png`.

| bit | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|--|--|--|--|--|--|--|--|--|
| `d0`–`d2` (digit, left→right) | f top-left | a top | b top-right | — | e bottom-left | g middle | d bottom | c bottom-right |
| `b0` | ▲ lower left | small icon, left middle | ▶ upper left | — | — | — | — | — |
| `b1` | ▼ lower right | ▼ bottom centre | small mark, lower left | — | dash, right | box, right | icon, upper right (firmware idle: on) | — |

## How the controller reads the wire: start bit + 33 data bits

The controller is a **start-bit-framed serial driver** (consistent with an MC145453-class 33-segment LCD
driver; the part is not identified). It ignores `0` bits until it sees a `1` (the **start bit**), takes
the next **33 bits** as segment data (MSB-first on the wire), latches them, and goes back to waiting for a
start bit. **A partial frame survives chip-select**: if a push ends inside a frame, the next push's first
bits complete it. That is why every push (the firmware's included) begins with **5 zero bytes**: 40 zero
clocks finish any pending frame, so the real start bit (wire byte 5 bit 7, always set) begins an aligned
frame. The firmware frame is therefore `00×5 | 1 + data bits 0–32 | 6 trailing zeros`.

Data bit → segment (33 bits: 21 digit segments + 9 icons + 3 unused):

| data bit | wire byte.bit | segment | | data bit | wire byte.bit | segment |
|--|--|--|--|--|--|--|
| 0 | 5.6 | d0.f | | 17 | 7.5 | b1.0 |
| 1 | 5.5 | d0.g | | 18 | 7.4 | — |
| 2 | 5.4 | b0.2 | | 19 | 7.3 | b1.4 |
| 3 | 5.3 | b0.1 | | 20 | 7.2 | b1.5 |
| 4 | 5.2 | b0.0 | | 21 | 7.1 | b1.6 |
| 5 | 5.1 | — | | 22 | 7.0 | — |
| 6 | 5.0 | b1.2 | | 23 | 8.7 | d2.b |
| 7 | 6.7 | d0.e | | 24 | 8.6 | d2.a |
| 8 | 6.6 | d0.d | | 25 | 8.5 | d2.f |
| 9 | 6.5 | d0.c | | 26 | 8.4 | d2.g |
| 10 | 6.4 | d1.e | | 27 | 8.3 | d1.b |
| 11 | 6.3 | b1.1 | | 28 | 8.2 | d1.a |
| 12 | 6.2 | d1.d | | 29 | 8.1 | d1.f |
| 13 | 6.1 | d1.c | | 30 | 8.0 | d1.g |
| 14 | 6.0 | d2.e | | 31 | 9.7 | d0.b |
| 15 | 7.7 | d2.d | | 32 | 9.6 | d0.a |
| 16 | 7.6 | d2.c | | | | |

`dN.x` = digit N (left→right) segment x; icons are named by their struct bit (`b0.2` = struct `b0` bit 2).

### `wire/` — each of the 80 wire bits flipped alone

Relative to the blank frame `00 00 00 00 00 | 80 00 00 00 00`. Sheets: `sheet_wire_bytes0-4.png`,
`sheet_wire_bytes5-9.png`. Bits in bytes 5–9 are data bits (table above). **Bits in bytes 0–4 only seem
to "light a segment too":** a `1` there becomes an earlier start bit, so the real start bit (5.7) lands
in a data slot at distance `40 − p − 1` and lights whatever segment that slot drives. The start-bit model
reproduces all 79 single-bit photos (5.7 itself: clearing it leaves no frame at all, so that photo just
shows the previous screen).

### `framing/` — the experiment that settled it

Pattern A (left-digit bits from bytes 0–4) and B (a right-digit "8" in bytes 5–9), pushed alone and
together. **run1** (no reset between pushes) gave "t" for A and AB: the previous push had left a partial
frame, which ate the next push's first bits and moved the start bit to wire bit 30. **run2** puts a clean
reset frame before each test and repeats A three times. All 12 photos match the model, including
frames that light nothing (`E8`: start bit 7, data bit 22 = unused) and ones that light segments of
digits neither pattern contains (AB80: `n _ 7`).

## `glyphs/`, `text_53X*.jpg`, `text_BNS*.jpg`

The `lcd_text.py` font, 3 glyphs per shot (`sheet_glyphs.png`), "53X" (X uses the conventional
7-segment stand-in, the same shape as H) and "BNS" (shows as `bnS`: B and N only exist in lowercase). The glyph sheets and 53X were taken with the
icon bytes at the firmware's idle `00 40`, so the upper-right icon (`b1` bit 6) is lit in them;
`text_BNS*.jpg` uses `lcd_text.py`'s default of all icons off (`--icons 0040` turns it back on).
