# GM600 display: one photo per segment

Taken 2026-11-11 on the bench radio (serial 807IYA0903) with the webcam, backlight on, by
`tools/lcd_photos.py`. The radio runs `tools/lcd_raw.S`, which drives the display controller with its own
SPI code (no firmware calls), so nothing else is drawn on the glass.

The glass is one controller (SPI chip-select `0x61` on `0x107E`, SPCR `0x52`) carrying 3 seven-segment
digits and 10 icons; `all_on.jpg` shows everything, `blank.jpg` nothing.

## `struct/` — one bit of the firmware's 5-byte digit struct `[b0 b1 d0 d1 d2]`

This is what firmware entry 5 (`0x9018`) takes; `lcd_text.pack()` turns it into the wire bytes.
Sheets: `sheet_digit_bits.png`, `sheet_icon_bits.png`.

| bit | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|--|--|--|--|--|--|--|--|--|
| `d0`–`d2` (digit, left→right) | f top-left | a top | b top-right | — | e bottom-left | g middle | d bottom | c bottom-right |
| `b0` | ▲ lower left | small icon, left middle | ▶ upper left | — | — | — | — | — |
| `b1` | ▼ lower right | ▼ bottom centre | small mark, lower left | — | dash, right | box, right | icon, upper right (firmware idle: on) | — |

## `wire/` — one bit of the 10 raw bytes on the wire

Relative to the blank frame `00 00 00 00 00 | 80 00 00 00 00` (= 5 zero bytes + `pack(00 00 00 00 00)`,
the frame the firmware would send for a blank struct). Sheets: `sheet_wire_bytes0-4.png`,
`sheet_wire_bytes5-9.png`. Matched to the struct photos by where the segment appears (the box was too faint
for the matcher and was read off the sheet by eye). `dN.x` = digit N segment x; icons are named by
their struct bit.

| byte | bit0 | bit1 | bit2 | bit3 | bit4 | bit5 | bit6 | bit7 |
|--|--|--|--|--|--|--|--|--|
| 0 | d0.a | — | — | — | — | — | — | — |
| 1 | d2.a | d2.f | d2.g | d1.b | d1.a | d1.f | d1.g | d0.b |
| 2 | d2.c | b1.0 | — | b1.4 | b1.5 | b1.6 | — | d2.b |
| 3 | d0.d | d0.c | d1.e | b1.1 | d1.d | d1.c | d2.e | d2.d |
| 4 | d0.f | d0.g | b0.2 | b0.1 | b0.0 | — | b1.2 | d0.e |
| 5 | b1.2 | — | b0.0 | b0.1 | b0.2 | d0.g | d0.f | d0.f ¹ |
| 6 | d2.e | d1.c | d1.d | b1.1 | d1.e | d0.c | d0.d | d0.e |
| 7 | — | b1.6 | b1.5 | b1.4 | — | b1.0 | d2.c | d2.d |
| 8 | d1.g | d1.f | d1.a | d1.b | d2.g | d2.f | d2.a | d2.b |
| 9 | — | — | — | — | — | — | d0.a | d0.b |

Every segment answers to one bit in bytes 0–4 **and** one in bytes 5–9 (the halves are in roughly
mirrored order). The firmware only ever fills bytes 5–9 and sends 0–4 as zeros.
¹ byte 5 bit 7 is set in every frame the firmware sends; clearing it showed `d0.f`. Not understood yet.

## `glyphs/`, `text_53X*.jpg`

The `lcd_text.py` font, 3 glyphs per shot (`sheet_glyphs.png`), and "53X" (X uses the conventional
7-segment stand-in, the same shape as H).
