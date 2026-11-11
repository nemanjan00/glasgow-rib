#!/usr/bin/env python3
"""Drive the GM600's 3-digit 7-segment display from a RAM stub (lcd_text.S).

Uploads the stub with SBEP 0x17, verifies it, launches it with 0x26, then sends one pattern per
argument. Each pattern is three 7-seg bytes (one per digit, the firmware's font, e.g. "322" =
e6 76 76) or text (see FONT). The radio stays halted on the stub: recover with psu_reboot.py.

  lcd_text.py --raw e67676          push raw segment bytes
  lcd_text.py HEL LO                push text, one 3-char screen per argument (--delay between)
  lcd_text.py --no-launch e67676    stub already running: just send
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import asm6811
import sbep

SYNC = 0xA5
B0, B1 = 0x00, 0x40          # firmware's idle values for the two non-digit struct bytes

# 7-seg bit -> segment, from a one-bit-at-a-time sweep photographed on the radio (bit 3 is unused):
#   bit0 f (top-left)  bit1 a (top)  bit2 b (top-right)  bit4 e (bottom-left)
#   bit5 g (middle)    bit6 d (bottom)  bit7 c (bottom-right)
SEG = dict(f=0x01, a=0x02, b=0x04, e=0x10, g=0x20, d=0x40, c=0x80)
GLYPHS = {
    "0": "abcdef", "1": "bc", "2": "abdeg", "3": "abcdg", "4": "bcfg", "5": "acdfg",
    "6": "acdefg", "7": "abc", "8": "abcdefg", "9": "abcdfg",
    "A": "abcefg", "B": "cdefg", "C": "adef", "D": "bcdeg", "E": "adefg", "F": "aefg",
    "G": "acdef", "H": "bcefg", "I": "ef", "J": "bcde", "L": "def", "N": "ceg", "O": "abcdef",
    "P": "abefg", "Q": "abcfg", "R": "eg", "S": "acdfg", "T": "defg", "U": "bcdef", "Y": "bcdfg",
    "Z": "abdeg", "-": "g", "_": "d", " ": "",
}
FONT = {c: sum(SEG[x] for x in segs) for c, segs in GLYPHS.items()}


def text_to_segs(text):
    text = text.ljust(3)[:3]
    try:
        return bytes(FONT[c] for c in text.upper())
    except KeyError as e:
        raise SystemExit(f"no glyph for {e}")


def launch(r):
    a = asm6811.assemble_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), "lcd_text.S"))
    org, code = a.org, a.code
    for off in range(0, len(code), 32):
        addr = org + off
        st, _, _ = r.transact(sbep.encode(0x17, addr.to_bytes(3, "big") + code[off:off + 32]))
        assert st == sbep.ACK, (hex(addr), st)
    back = b"".join(r.read_block(org + o, min(64, len(code) - o)) for o in range(0, len(code), 64))
    assert back == code, "verify mismatch"
    r.drain()
    r.sock.sendall(sbep.encode(0x26, org.to_bytes(2, "big")))
    time.sleep(0.3)
    r.drain()


def push(r, segs, b0=B0, b1=B1):
    r.sock.sendall(bytes([SYNC, b0, b1]) + bytes(segs))
    time.sleep(0.05)
    r.drain()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("screens", nargs="+")
    ap.add_argument("--raw", action="store_true", help="arguments are hex: d0d1d2 or b0b1d0d1d2")
    ap.add_argument("--no-launch", action="store_true")
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--scroll", action="store_true", help="scroll the joined text through 3 digits")
    args = ap.parse_args()
    with sbep.Radio() as r:
        if not args.no_launch:
            launch(r)
        screens = args.screens
        if args.scroll:
            t = "   " + " ".join(screens) + "   "
            screens = [t[i:i + 3] for i in range(len(t) - 2)]
        for i, s in enumerate(screens):
            if i:
                time.sleep(args.delay)
            if args.raw:
                b = bytes.fromhex(s)
                push(r, b[-3:], *(b[:2] if len(b) == 5 else (B0, B1)))
            else:
                push(r, text_to_segs(s))
            print("pushed", s)


if __name__ == "__main__":
    main()
