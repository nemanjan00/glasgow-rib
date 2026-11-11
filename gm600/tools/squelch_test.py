#!/usr/bin/env python3
"""Tone-squelch test on a GM600 conventional channel: HackRF signal phases vs LEDs + display.

The radio must already be on the conventional channel (long-hold Button A ~7 s, then select 001;
see ../FAQ.md) — this can't be done over SBEP. Phases (each ``--seconds``):
  baseline  no signal
  notone    carrier + 1 kHz audio, no PL   -> a PL-coded channel should stay squelched
  tone      carrier + 1 kHz audio + PL     -> squelch should open
  off       no signal again
Logs the LED shadow (0x34AE: bit0 amber, 1 red, 2 green, 7 backlight) and the display text
(0x34BD) over SBEP four times a second, and saves a webcam crop of LEDs + LCD per phase.

  squelch_test.py [--freq 446.06875] [--tone 123.0] [--seconds 8] [--out DIR] [--video /dev/video2]
"""
import argparse
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ctcss_tx
from sbep import Radio


def shot(video, path):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "v4l2", "-input_format", "mjpeg",
                    "-video_size", "1280x720", "-i", video, "-frames:v", "10", "-update", "1",
                    "-vf", "crop=500:150:600:200", "-y", path], check=False)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--freq", type=float, default=446.06875)
    ap.add_argument("--tone", type=float, default=123.0)
    ap.add_argument("--seconds", type=float, default=8.0)
    ap.add_argument("--out", default="/tmp/squelch_test")
    ap.add_argument("--video", default="/dev/video2")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    files = {}
    for name, tone in (("notone", None), ("tone", args.tone)):
        fd, path = tempfile.mkstemp(suffix=".iq")
        os.close(fd)
        ctcss_tx.make_iq(tone=tone).tofile(path)
        files[name] = path
    try:
        with Radio() as r:
            disp = r.read_block(0x34BD, 3)
            print(f"radio {r.id_string(1)}, display {disp!r}")
            if disp.strip() not in (b"1", b"2", b"3", b"4", b"001", b"002", b"003", b"004"):
                print("warning: the display doesn't look like a conventional channel (001-004); "
                      "trunked radios ignore this test", file=sys.stderr)
            for phase in ("baseline", "notone", "tone", "off"):
                tx = None
                if phase in files:
                    tx = ctcss_tx.transmit(files[phase], args.freq * 1e6)
                    time.sleep(1.5)                      # let the HackRF start and the radio settle
                seen, t0 = [], time.time()
                while time.time() - t0 < args.seconds:
                    seen.append(r.read_block(0x34AE, 1)[0])
                    time.sleep(0.25)
                disp = r.read_block(0x34BD, 3)
                shot(args.video, os.path.join(args.out, phase + ".jpg"))
                if tx:
                    tx.terminate()
                    tx.wait()
                bits = sorted({b for b in seen})
                print(f"{phase:8} display {disp!r} LED bytes seen {' '.join('%02x' % b for b in bits)}"
                      f"  (amber {any(b & 1 for b in seen)}, red {any(b & 2 for b in seen)}, "
                      f"green {any(b & 4 for b in seen)})", flush=True)
    finally:
        for path in files.values():
            os.unlink(path)
    print(f"webcam crops in {args.out}/")


if __name__ == "__main__":
    sys.exit(main())
