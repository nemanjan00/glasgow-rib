#!/usr/bin/env python3
"""GM600 control-head display model + SVG renderer + live web UI.

The glass is one start-bit-framed serial controller (see images/lcd_segments/README.md): it ignores 0
bits until a 1 (start bit), latches the next 33 data bits, then waits again; a partial frame survives
chip-select. 33 data bits = 21 digit segments + 9 icons + 3 unused. The front-panel LEDs and the
LCD/keypad backlight are a separate 1-byte shift register (bit 0 amber, 1 red, 2 green, 7 backlight).

  Controller   bit-level model of the display controller (feed it the bytes clocked out on SPI)
  SpiTap       attaches to an emu6811.Mem and decodes the firmware's (or a stub's) SPI writes
  render_svg   draws the front panel (assets/make_banner.py) with the live segments + LEDs
  serve_ui     tiny HTTP server: / = live page, /display.svg, /state.json

  gm600_display.py --text 53X out.svg [--backlight] [--icons 0040] [--leds 05]   render a preview
"""
import argparse
import http.server
import json
import threading

# data bit index (after the start bit) -> segment name; dN.x = digit N (left->right) segment x,
# bA.k = icon driven by struct byte bA bit k. Indices 5, 18, 22 are unused.
DATA_BITS = {
    0: "d0.f", 1: "d0.g", 2: "b0.2", 3: "b0.1", 4: "b0.0", 6: "b1.2", 7: "d0.e", 8: "d0.d",
    9: "d0.c", 10: "d1.e", 11: "b1.1", 12: "d1.d", 13: "d1.c", 14: "d2.e", 15: "d2.d", 16: "d2.c",
    17: "b1.0", 19: "b1.4", 20: "b1.5", 21: "b1.6", 23: "d2.b", 24: "d2.a", 25: "d2.f", 26: "d2.g",
    27: "d1.b", 28: "d1.a", 29: "d1.f", 30: "d1.g", 31: "d0.b", 32: "d0.a",
}
FRAME_BITS = 33
DISPLAY_SELECT = 0x61        # 0x107E bits asserted while the display controller is clocked
LED_STROBE = 0x21            # 0x107E bits raised together to latch the LED shift register
SPCR, SPDR, CSPORT = 0x1028, 0x102A, 0x107E


class Controller:
    """Start-bit-framed 33-bit display controller. ``segments`` is the latched set."""

    def __init__(self):
        self.segments = set()
        self.frames = 0
        self._collecting = False
        self._bits = []

    def feed_bit(self, bit):
        if not self._collecting:
            if bit:
                self._collecting, self._bits = True, []
            return
        self._bits.append(bit)
        if len(self._bits) == FRAME_BITS:
            self.segments = {DATA_BITS[i] for i, b in enumerate(self._bits) if b and i in DATA_BITS}
            self.frames += 1
            self._collecting = False

    def feed(self, data):
        for byte in bytes(data):
            for k in range(7, -1, -1):          # SPI is MSB-first
                self.feed_bit((byte >> k) & 1)


class SpiTap:
    """Watches an emu6811.Mem's register writes and keeps the display + LED state current.

    Display bytes: written to SPDR while 0x107E (PORTG) has all of 0x61 asserted. LEDs: the last SPDR
    byte sent with no select asserted is latched when 0x21 rises (firmware entry 10)."""

    def __init__(self, mem):
        self.display = Controller()
        self.leds = 0
        self.lock = threading.Lock()
        self._cs = mem.reg[CSPORT - 0x1000]
        self._last_other = None
        self._chain = mem.watch
        mem.watch = self._watch

    def _watch(self, addr, value):
        if self._chain:
            self._chain(addr, value)
        if addr == SPDR:
            if self._cs & DISPLAY_SELECT == DISPLAY_SELECT:
                with self.lock:
                    self.display.feed([value])
            elif self._cs & DISPLAY_SELECT == 0:      # LED byte: shifted out with nothing selected
                self._last_other = value
        elif addr == CSPORT:
            rising = value & ~self._cs
            # LED latch = 0x20 and 0x01 rising together after the byte (entry 10). Entry 2 raises
            # 0x20 alone *before* its own 13-byte transfer, so a lone 0x20 is not a latch.
            if rising & 0x21 == 0x21 and not value & 0x40 and self._last_other is not None:
                with self.lock:
                    self.leds = self._last_other
            self._cs = value

    def __getstate__(self):                 # snapshots (copy.deepcopy of a booted VM): no lock
        st = dict(self.__dict__)
        del st["lock"]
        return st

    def __setstate__(self, st):
        self.__dict__.update(st)
        self.lock = threading.Lock()

    def state(self):
        with self.lock:
            return {"segments": sorted(self.display.segments), "leds": self.leds,
                    "frames": self.display.frames}


# --- keypad --------------------------------------------------------------------------------------

# The keypad is three analog ladders on A/D channels ADR1-ADR3. The firmware's decoders (page 0, CPU
# 0x4FBB / 0x4F79 / 0x4FFD) turn a reading into a key code by window: <=0x29, 0x39-0x48, 0x79-0x88,
# 0xB9-0xC8 (ADR3 has no 0x39-0x48 key); anything else (idle reads 0xFF) is "no key". A press has to
# read the same code twice in a row (debounce). code -> (A/D channel, level in the middle of its window):
KEY_CODES = {
    0x10: (0, 0x10), 0x11: (0, 0x40), 0x12: (0, 0x80), 0x13: (0, 0xC0),     # ADR1 -> 0x4FBB
    0x14: (1, 0x10), 0x15: (1, 0x40), 0x16: (1, 0x80), 0x17: (1, 0xC0),     # ADR2 -> 0x4F79
    0x08: (2, 0x10), 0x06: (2, 0x80), 0x07: (2, 0xC0),                      # ADR3 -> 0x4FFD
}

# Physical button -> key code, identified in the emulator from the radio's idle (own-ident) state by
# what each code does — not yet checked by pressing the bench radio's buttons:
#   07 / 06  scroll through shortform entries up (04, 06, 11 ...) / down (976, 975, 969 ...)
#   11 / 10  volume up / down (stepped values to the audio chip over SPI, rising / falling)
#   12       clear (key click only, nothing to clear)          13  base call (shows "306")
#   14       select A (shows "---": the trunked/conventional mode change)
#   15       select B (call-in-absence; nothing to show)       16  select C (toggles alarm icons)
#   17       select D (dedicated call, shows "400")            08  on/off (no effect on a short press)
BUTTONS = {
    "A": 0x14, "B": 0x15, "C": 0x16, "D": 0x17,
    "scroll_up": 0x07, "scroll_down": 0x06,
    "vol_down": 0x10, "vol_up": 0x11, "clear": 0x12, "call": 0x13, "power": 0x08,
}


def adc_for(code):
    """AN0-AN3 readings that make the firmware decode ``code`` (the others at their idle level)."""
    import gm600py.firmware.emu6811 as emu6811
    adc = list(emu6811.IDLE_ADC)
    line, level = KEY_CODES[code]
    adc[line] = level
    return adc


def segments_for_text(text, b0=0x00, b1=0x00):
    """Segments lit by lcd_text-style text (font from lcd_text.FONT) plus icon bytes."""
    from gm600py.stubs.lcd_text import pack, text_to_segs
    ctrl = Controller()
    ctrl.feed(bytes(5) + pack(bytes([b0, b1]) + text_to_segs(text)))
    return ctrl.segments
