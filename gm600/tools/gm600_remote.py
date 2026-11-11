#!/usr/bin/env python3
"""Remote-control the emulated GM600: read its screen and press its buttons.

The emulator (emu6811 running motofix.BIN) gives us the two things a remote control needs without
any bench hardware:

  * READ THE SCREEN  — the LCD segment buffer the firmware drives over SPI lives at RAM 0x37AC
    (21 bytes: three 7-segment digits + status icons) and the LED shadow at 0x34AE. read_screen()
    returns the current contents.
  * PRESS A BUTTON   — the 6-key front panel is an analog resistor ladder read through the A/D
    (results ADR1..ADR4 at 0x1031-0x1034; idle = 0xFF/no key). press() drives a key's levels onto
    the A/D for a hold time, then releases, exactly as pressing a key pulls the line down.

This is the "script that periodically reads the screen and simulates button presses" idea, running
against the firmware itself. The button->level map is radio-specific; KEYS below is seeded from
discovery (run with --discover to re-probe which levels change the display on this codeplug).
"""
import sys
sys.path.insert(0, __import__("os").path.dirname(__file__))
import emu6811

LCD_ADDR, LCD_LEN = 0x37AC, 21          # LCD segment buffer (3 digits + icons)
LED_ADDR, LED_LEN = 0x34AE, 3           # LED shadow
BOOT_INSNS = 6_000_000
HOLD_INSNS = 400_000                    # how long a key is held (several A/D sample cycles)

# Keypad levels as (ADR1, ADR2) injected on the two analog key lines; 0xFF = released.
# The firmware registers a key the moment a line is pulled below ~0xE0 (every probed level
# triggered the key-event, LCD byte 5 0x8F->0x00). In the trunking-idle screen all keys read as
# a generic "key pressed" rather than distinct actions — distinct per-key behaviour appears once
# the radio is in a menu, which needs an entry sequence (long-press) not yet mapped. Treat these
# as line/level presses; refine with --discover once a menu screen is reachable.
KEYS = {
    "line1": (0x00, 0xFF), "line2": (0xFF, 0x00),
    "k1": (0x00, 0xFF), "k2": (0x40, 0xFF), "k3": (0x80, 0xFF),
    "k4": (0xFF, 0x00), "k5": (0xFF, 0x40), "k6": (0xFF, 0x80),
}


class Remote:
    def __init__(self, flash_path, codeplug_path):
        self.m = emu6811.Mem(open(flash_path, "rb").read(), open(codeplug_path, "rb").read())
        self.c = emu6811.CPU(self.m)
        self.c.reset()
        self.c.run(max_insns=BOOT_INSNS, cop_timeout=150000)

    def _settle(self, insns=HOLD_INSNS):
        self.c.run(max_insns=insns, cop_timeout=150000)

    def read_screen(self):
        """Return (lcd_bytes, led_bytes) — the live segment buffers."""
        self._settle(60_000)
        return (bytes(self.m.ram[LCD_ADDR:LCD_ADDR + LCD_LEN]),
                bytes(self.m.ram[LED_ADDR:LED_ADDR + LED_LEN]))

    def press(self, key, hold=HOLD_INSNS):
        """Press and release a key (name in KEYS, or an (adr1, adr2) tuple)."""
        a1, a2 = KEYS[key] if isinstance(key, str) else key
        self.m.adc = [a1, a2, 0xFF, 0xFF]            # key down
        self.c.run(max_insns=hold, cop_timeout=150000)
        self.m.adc = [0xFF, 0xFF, 0xFF, 0xFF]        # release
        self.c.run(max_insns=hold // 2, cop_timeout=150000)

    def discover(self, hold=HOLD_INSNS):
        """Probe A/D levels; report which produce a display state not seen at idle."""
        idle = {self.read_screen()[0] for _ in range(6)}     # collect the idle/flash states
        hits = []
        for line in range(2):
            for lv in (0x00, 0x30, 0x60, 0x90, 0xC0):
                adc = [0xFF, 0xFF, 0xFF, 0xFF]; adc[line] = lv
                self.m.adc = adc; self.c.run(max_insns=hold, cop_timeout=150000)
                scr = bytes(self.m.ram[LCD_ADDR:LCD_ADDR + LCD_LEN])
                self.m.adc = [0xFF] * 4; self.c.run(max_insns=hold, cop_timeout=150000)
                new = scr not in idle
                hits.append((line, lv, scr.hex(), new))
                print("ADR%d=%02X -> %s %s" % (line + 1, lv, scr.hex(), "[new state]" if new else ""))
        return hits


# LCD buffer layout, reversed by diffing captured states (splash / idle "322" / flashing):
#   bytes 0,1,2,8,19 = status icons / flashing indicators (scanning, the flashing LED, A/B/C marks)
#   bytes 3,5,6,12,13,14,16,17,18,20 = the three 7-segment digits (multiplexed across COM lines)
#   bytes 4,7,9,10,11,15 = constant framing
ICON_BYTES = (0, 1, 2, 8, 19)
DIGIT_BYTES = (3, 5, 6, 12, 13, 14, 16, 17, 18, 20)


def fmt_screen(lcd, led):
    digits = "".join("%02x" % lcd[i] for i in DIGIT_BYTES)
    icons = "".join("%02x" % lcd[i] for i in ICON_BYTES)
    return "LCD %s  (digits %s / icons %s)  LED %s" % (lcd.hex(), digits, icons, led.hex())


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) < 2:
        print("usage: gm600_remote.py <motofix.BIN> <codeplug.bin> [--discover | key ...]", file=sys.stderr)
        sys.exit(2)
    r = Remote(args[0], args[1])
    rest = args[2:]
    print("screen:", fmt_screen(*r.read_screen()))
    if rest and rest[0] == "--discover":
        r.discover()
    else:
        for key in rest:
            r.press(key)
            print("after %-4s:" % key, fmt_screen(*r.read_screen()))
