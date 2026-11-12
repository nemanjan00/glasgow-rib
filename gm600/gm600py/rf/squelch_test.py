#!/usr/bin/env python3
"""Tone-squelch test on a GM600 conventional channel: HackRF signal phases vs LEDs + display.

The radio must already be on the conventional channel: `keyhold.py` long-holds Button A remotely
(or hold it ~7 s by hand), then 001 is selected. Phases (each ``--seconds``):
  baseline  no signal
  notone    carrier + 1 kHz audio, no PL   -> a PL-coded channel should stay squelched
  tone      carrier + 1 kHz audio + PL     -> squelch should open
  off       no signal again
Logs over SBEP, four times a second: the firmware's receive flags in its input-state array
(0x347F = carrier detected, 0x347E = PL decoded = RX active / squelch open), the LED shadow (0x34AE:
bit0 amber, 1 red = "channel busy" flashes on a carrier without our PL, 2 green, 7 backlight) and
the display (0x34BD); saves a webcam crop of LEDs + LCD per phase.

  squelch_test.py [--freq 446.06875] [--tone 123.0] [--seconds 8] [--out DIR] [--video /dev/video2]
"""
import argparse
import os
import subprocess
import sys
import tempfile
import time

import gm600py.rf.ctcss_tx as ctcss_tx
from gm600py.link.sbep import Radio


def shot(video, path):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "v4l2", "-input_format", "mjpeg",
                    "-video_size", "1280x720", "-i", video, "-frames:v", "10", "-update", "1",
                    "-vf", "crop=500:150:600:200", "-y", path], check=False)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--freq", type=float, default=438.625, help="MHz (default: ch 004 RX)")
    ap.add_argument("--tone", type=float, default=123.0)
    ap.add_argument("--seconds", type=float, default=6.0)
    ap.add_argument("--vga", type=int, default=35, help="HackRF TX VGA gain, dB")
    ap.add_argument("--cal-hz", type=float, default=ctcss_tx.CAL_HZ, help="HackRF frequency correction")
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
                    tx = ctcss_tx.transmit(files[phase], args.freq * 1e6 + args.cal_hz, vga=args.vga)
                    time.sleep(2.0)                      # let the HackRF start and the radio settle
                seen, rx, t0 = [], [], time.time()
                while time.time() - t0 < args.seconds:
                    seen.append(r.read_block(0x34AE, 1)[0])
                    rx.append(r.read_block(0x347E, 2))
                    time.sleep(0.25)
                disp = r.read_block(0x34BD, 3)
                shot(args.video, os.path.join(args.out, phase + ".jpg"))
                if tx:
                    tx.terminate()
                    tx.wait()
                    time.sleep(1.0)                      # let the HackRF free up
                bits = sorted({b for b in seen})
                print(f"{phase:8} carrier {int(any(x[1] for x in rx))}  RX/PL {int(any(x[0] for x in rx))}"
                      f"  display {disp!r}  LED bytes {' '.join('%02x' % b for b in bits)}"
                      f" (red/busy {int(any(b & 2 for b in seen))})", flush=True)
    finally:
        for path in files.values():
            os.unlink(path)
    print(f"webcam crops in {args.out}/")


if __name__ == "__main__":
    sys.exit(main())
