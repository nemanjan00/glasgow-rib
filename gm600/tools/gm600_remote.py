#!/usr/bin/env python3
"""Remote-control a GM600: read its screen (and press buttons) over the serial link.

The point is the REAL radio. The control head is a multiplexed segment LCD the firmware renders
into a 21-byte shadow buffer in RAM at 0x37AC (three 7-segment digits + status icons), with an LED
shadow at 0x34AE. SBEP `0x11` reads any 24-bit address on a running radio, so we can read that live
buffer over the wire while the radio operates normally -- a true screen mirror, no prog mode, no
hardware mods. This is what the emulator work was *for*: it found these addresses so we could point
them at the real chip.

Two backends, same screen decode:

  * RadioBackend  (default, `--radio [host:port]`) -- the real GM600 through the glasgow-rib applet
    socket (see sbep.py / ../README.md). read_screen() is the headline feature and works today.
  * EmuBackend    (`--emu <motofix.BIN> <codeplug.bin>`) -- the emu6811 VM, for development without
    a bench.

PRESSING BUTTONS, honestly: the front panel is a 6-key analog resistor ladder read through the A/D
(the firmware re-samples it every cycle). On the emulator we just set the A/D levels. On the REAL
radio there is no clean serial path: poking the decoded-key RAM byte over `0x17` is overwritten by
the next A/D sample long before a serial round-trip lands, and GM600 firmware exposes no keypad-test
opcode (the dispatch table has 0x10/11/13/17/18/19/20/21/22/23/26/2D/2E -- no keypad inject). Real
button injection therefore needs hardware on the keypad line or a takeover stub; see press() below.
The emulator backend presses for real.
"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

LCD_ADDR, LCD_LEN = 0x37AC, 21          # LCD segment shadow buffer (3 digits + icons)
LED_ADDR, LED_LEN = 0x34AE, 3           # LED shadow

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


class RadioBackend:
    """The real GM600 over SBEP (glasgow-rib applet socket). Screen read is live `0x11`."""

    def __init__(self, addr=None):
        from sbep import Radio
        self.radio = Radio(addr)                 # SBEP_ADDR env or host:port; default 127.0.0.1:2324

    def read_screen(self):
        # 0x11 reads any address on the running radio; the LCD/LED shadows are plain RAM.
        lcd = self.radio.read_block(LCD_ADDR, LCD_LEN)
        led = self.radio.read_block(LED_ADDR, LED_LEN)
        return bytes(lcd), bytes(led)

    def press(self, key, hold=None):
        raise NotImplementedError(
            "No clean serial keypress on the real radio: the keypad is an analog A/D ladder the "
            "firmware re-samples every cycle, so a 0x17 RAM poke is overwritten before the round-"
            "trip lands, and GM600 firmware has no keypad-inject opcode. Drive the keypad line in "
            "hardware, or use a RAM-stub takeover. Use --emu to press in the emulator.")

    def close(self):
        self.radio.close()


class EmuBackend:
    """The emu6811 VM booting motofix.BIN -- for development without a bench. Presses for real."""

    BOOT_INSNS = 6_000_000
    HOLD_INSNS = 400_000
    # Keypad levels (ADR1, ADR2) injected on the two analog key lines; 0xFF = released. Only the two
    # LINES are proven to register a press (pulling either below ~0xE0 triggers the key event). The
    # per-key levels below are a GUESS at where each of the six keys sits on its ladder and are not
    # yet distinguished by the firmware at the idle screen (needs a menu to tell them apart); "k1"
    # and "k4" are just line1/line2 at level 0 and are kept only as aliases.
    KEYS = {
        "line1": (0x00, 0xFF), "line2": (0xFF, 0x00),
        "k1": (0x00, 0xFF), "k2": (0x40, 0xFF), "k3": (0x80, 0xFF),   # line-1 ladder (k1 == line1)
        "k4": (0xFF, 0x00), "k5": (0xFF, 0x40), "k6": (0xFF, 0x80),   # line-2 ladder (k4 == line2)
    }

    def __init__(self, flash_path, codeplug_path):
        import emu6811
        self.m = emu6811.Mem(open(flash_path, "rb").read(), open(codeplug_path, "rb").read())
        self.c = emu6811.CPU(self.m)
        self.c.reset()
        self.c.run(max_insns=self.BOOT_INSNS, cop_timeout=150000)

    def _settle(self, insns):
        self.c.run(max_insns=insns, cop_timeout=150000)

    def read_screen(self):
        self._settle(60_000)
        return (bytes(self.m.ram[LCD_ADDR:LCD_ADDR + LCD_LEN]),
                bytes(self.m.ram[LED_ADDR:LED_ADDR + LED_LEN]))

    def press(self, key, hold=None):
        hold = hold or self.HOLD_INSNS
        a1, a2 = self.KEYS[key] if isinstance(key, str) else key
        self.m.adc = [a1, a2, 0xFF, 0xFF]            # key down
        self.c.run(max_insns=hold, cop_timeout=150000)
        self.m.adc = [0xFF, 0xFF, 0xFF, 0xFF]        # release
        self.c.run(max_insns=hold // 2, cop_timeout=150000)

    def close(self):
        pass


def mirror(backend, period=0.5, count=None):
    """Poll the screen and print it whenever it changes -- a live mirror of the radio's display."""
    last = None
    n = 0
    while count is None or n < count:
        lcd, led = backend.read_screen()
        cur = (lcd, led)
        if cur != last:
            print("%s  %s" % (time.strftime("%H:%M:%S"), fmt_screen(lcd, led)))
            last = cur
        n += 1
        if count is None or n < count:
            time.sleep(period)


def _usage():
    print("usage: gm600_remote.py [--radio [host:port] | --emu <motofix.BIN> <codeplug.bin>] "
          "[--mirror | key ...]", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    args = sys.argv[1:]
    backend = None
    if args and args[0] == "--emu":
        if len(args) < 3:
            _usage()
        backend = EmuBackend(args[1], args[2])
        rest = args[3:]
    else:
        if args and args[0] == "--radio":
            addr = args[1] if len(args) > 1 and ":" in args[1] else None
            rest = args[2:] if addr else args[1:]
        else:
            addr, rest = None, args
        backend = RadioBackend(addr)

    try:
        if rest and rest[0] == "--mirror":
            print("screen:", fmt_screen(*backend.read_screen()))
            mirror(backend)
        else:
            print("screen:", fmt_screen(*backend.read_screen()))
            for key in rest:
                backend.press(key)
                print("after %-5s:" % key, fmt_screen(*backend.read_screen()))
    finally:
        backend.close()
