#!/usr/bin/env python3
"""Photograph every segment and every glyph of the GM600 7-seg display (lab host, webcam).

Launches lcd_raw.S (no firmware code), turns the backlight on, and saves a cropped webcam shot for:
blank / all-on references; each of the 80 raw wire bits flipped alone (wire_byte*_bit*); each bit
of the 5-byte digit struct [b0 b1 d0 d1 d2] alone (seg_*); one 3-glyph screen per font chunk. Recover the radio afterwards with
psu_reboot.py.

  lcd_photos.py OUTDIR [--video /dev/video2] [--crop 300x150+660+190]
"""
import argparse
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sbep
from lcd_text import FONT, GLYPHS, launch, led, pack, push, push_wire

NAMES = ["b0", "b1", "d0", "d1", "d2"]


def shoot(args, name):
    path = os.path.join(args.outdir, name + ".jpg")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "v4l2", "-input_format", "mjpeg",
                    "-video_size", "1280x720", "-i", args.video, "-frames:v", "15", "-update", "1",
                    "-vf", "crop=" + args.crop.replace("x", ":").replace("+", ":"), "-y", path],
                   check=True)
    return path


def show(r, struct):
    push(r, struct[2:], struct[0], struct[1], standalone=True)
    time.sleep(0.6)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir")
    ap.add_argument("--video", default="/dev/video2")
    ap.add_argument("--crop", default="300x150+660+190")
    ap.add_argument("--only", choices=["struct", "wire", "glyphs"], action="append",
                    help="run only these sweeps (default: all)")
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)
    with sbep.Radio() as r:
        launch(r, "lcd_raw.S")
        led(r, 0xC0)                                  # backlight on
        only = set(args.only or ["struct", "wire", "glyphs"])
        show(r, bytes(5)); shoot(args, "blank")
        show(r, b"\xff" * 5); shoot(args, "all_on")
        if "wire" in only:
            # Flip one bit of the 10 raw wire bytes at a time, relative to the blank frame
            # (5 x 00 + pack(00 00 00 00 00)); reaches bits the firmware's packing never sets.
            base = bytes(5) + pack(bytes(5))
            for i in range(10):
                for bit in range(8):
                    w = bytearray(base)
                    w[i] ^= 1 << bit
                    push_wire(r, w)
                    time.sleep(0.6)
                    print(shoot(args, f"wire_byte{i}_bit{bit}"))
        for i, n in enumerate(NAMES if "struct" in only else []):
            for bit in range(8):
                s = bytearray(5)
                s[i] = 1 << bit
                show(r, bytes(s))
                print(shoot(args, f"seg_{n}_bit{bit}"))
        glyphs = "".join(GLYPHS) if "glyphs" in only else ""
        for k in range(0, len(glyphs), 3):
            chunk = glyphs[k:k + 3].ljust(3)
            show(r, bytes([0x00, 0x40]) + bytes(FONT[c] for c in chunk))
            safe = "".join(c if c.isalnum() else {"-": "dash", "_": "under", " ": "sp"}[c] for c in chunk)
            print(shoot(args, f"glyphs_{k // 3:02d}_{safe}"))


if __name__ == "__main__":
    main()
