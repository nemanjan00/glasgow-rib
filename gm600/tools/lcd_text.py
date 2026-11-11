#!/usr/bin/env python3
"""Drive the GM600's 3-digit 7-segment display from a RAM stub (lcd_text.S).

Uploads the stub with SBEP 0x17, verifies it, launches it with 0x26, then sends one pattern per
argument. Each pattern is three 7-seg bytes (one per digit, the firmware's font, e.g. "322" =
e6 76 76) or text (see FONT). The radio stays halted on the stub: recover with psu_reboot.py.

  lcd_text.py --raw e67676          push raw segment bytes
  lcd_text.py HEL LO                push text, one 3-char screen per argument (--delay between)
  lcd_text.py --no-launch e67676    stub already running: just send
  lcd_text.py --standalone HI       use lcd_raw.S (own SPI, no firmware code) instead
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import asm6811
import sbep

SYNC = 0xA5
B0, B1 = 0x00, 0x00          # icon bytes (struct b0/b1); default all icons off. The firmware's idle
#                              screen uses 00 40 (b1 bit 6 = upper-right icon). Map: images/lcd_segments.

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
    "X": "bcefg", "Z": "abdeg", "-": "g", "_": "d", " ": "",  # X: conventional stand-in (= H)
}
FONT = {c: sum(SEG[x] for x in segs) for c, segs in GLYPHS.items()}


def pack(s):
    """Port of firmware 0x9018: 5-byte digit struct -> 5-byte controller frame."""
    s0, s1, s2, s3, s4 = s
    o = [0] * 5
    # 9020: ldd 0,x; asld; asld; oraa #80; anda #9E
    d = ((s0 << 8 | s1) << 2) & 0xFFFF
    a = ((d >> 8) | 0x80) & 0x9E
    b = s2 >> 1
    if s2 & 1: a |= 0x40
    if s1 & 0x04: a |= 0x01
    if s2 & 0x20: a |= 0x20
    o[0] = a
    # 903E: tba; clrb; lsrd; lsrd; stab 4,y
    o[4] = ((b << 8) >> 2) & 0xFF
    # 9045
    a = s2
    a = a | 0x20 if s2 & 0x80 else a & 0xDF
    a = a | 0x80 if s2 & 0x10 else a & 0x7F
    o[1] = a & 0xE0
    # 905E
    o[3] = (s3 << 1) & 0x0E
    # 9066: ldab 4,x; tba; aslb; aslb; asld; asla x3
    a, b = s4, (s4 << 2) & 0xFF
    d = ((a << 8 | b) << 1) & 0xFFFF
    a = (d >> 8) << 3 & 0xFF
    # 906F: ldab 3,x; aslb; aslb; asld; oraa 3,y; staa 3,y
    b = (s3 << 2) & 0xFF
    d = ((a << 8 | b) << 1) & 0xFFFF
    a, b = d >> 8, d & 0xFF
    o[3] |= a
    # 907A: a=s4; lsra x7 -> C=bit6; rolb; lsra -> C=bit7; rolb
    def rol(b, c): return ((b << 1) | c) & 0xFF
    b = rol(b, (s4 >> 6) & 1)
    b = rol(b, (s4 >> 7) & 1)
    # 9086: a=s1; lsra rolb (bit0); lsra lsra lsra rolb (bit3); then (lsra rolb) x3 (bits 4..6); aslb
    b = rol(b, s1 & 1)
    b = rol(b, (s1 >> 3) & 1)
    for k in (4, 5, 6):
        b = rol(b, (s1 >> k) & 1)
    b = (b << 1) & 0xFF
    o[2] = b
    # 9098: a=s3; clrb; lsra x5 -> C=bit4; rolb; aslb; lsra lsra -> C=bit6; rolb; lsra -> bit7; rolb
    b = 0
    b = rol(b, (s3 >> 4) & 1)
    b = (b << 1) & 0xFF
    b = rol(b, (s3 >> 6) & 1)
    b = rol(b, (s3 >> 7) & 1)
    # 90A9: a=s4; asla x4 -> C=bit4; rolb; brclr 1,x #02 -> orab #8; orab 1,y; stab 1,y
    b = rol(b, (s4 >> 4) & 1)
    if s1 & 0x02: b |= 0x08
    o[1] |= b
    return bytes(o)


def text_to_segs(text):
    text = text.ljust(3)[:3]
    try:
        return bytes(FONT[c] for c in text.upper())
    except KeyError as e:
        raise SystemExit(f"no glyph for {e}")


def launch(r, stub="lcd_text.S"):
    a = asm6811.assemble_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), stub))
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


def push(r, segs, b0=B0, b1=B1, standalone=False):
    struct = bytes([b0, b1]) + bytes(segs)
    r.sock.sendall(bytes([SYNC]) + (bytes(5) + pack(struct) if standalone else struct))
    time.sleep(0.05)
    r.drain()


def push_wire(r, wire):
    """lcd_raw.S only: send 10 raw wire bytes to the display controller."""
    assert len(wire) == 10
    r.sock.sendall(bytes([SYNC]) + bytes(wire))
    time.sleep(0.05)
    r.drain()


def led(r, value):
    """Set the LED shift register (lcd_raw.S only): bit 7 = backlight, bits 0-2 = status LEDs."""
    r.sock.sendall(bytes([0x5A, value]))
    time.sleep(0.05)
    r.drain()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("screens", nargs="+")
    ap.add_argument("--raw", action="store_true", help="arguments are hex: d0d1d2 or b0b1d0d1d2")
    ap.add_argument("--no-launch", action="store_true")
    ap.add_argument("--standalone", action="store_true",
                    help="use lcd_raw.S: drive the controller with no firmware calls (host packs)")
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--icons", default="0000", help="icon bytes b0b1 in hex (e.g. 0040 = idle icon)")
    ap.add_argument("--backlight", action="store_true", help="turn the backlight on (--standalone)")
    ap.add_argument("--scroll", action="store_true", help="scroll the joined text through 3 digits")
    args = ap.parse_args()
    with sbep.Radio() as r:
        if not args.no_launch:
            launch(r, "lcd_raw.S" if args.standalone else "lcd_text.S")
        if args.backlight:
            if not args.standalone:
                raise SystemExit("--backlight needs --standalone")
            led(r, 0xC0)
        icons = tuple(bytes.fromhex(args.icons))
        screens = args.screens
        if args.scroll:
            t = "   " + " ".join(screens) + "   "
            screens = [t[i:i + 3] for i in range(len(t) - 2)]
        for i, s in enumerate(screens):
            if i:
                time.sleep(args.delay)
            if args.raw:
                b = bytes.fromhex(s)
                push(r, b[-3:], *(b[:2] if len(b) == 5 else icons), standalone=args.standalone)
            else:
                push(r, text_to_segs(s), *icons, standalone=args.standalone)
            print("pushed", s)


if __name__ == "__main__":
    main()
