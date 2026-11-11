#!/usr/bin/env python3
"""Transmit a narrowband-FM test signal with an optional CTCSS (PL) tone from a HackRF.

For opening a GM600 conventional channel's tone squelch on the bench: channel 001 on the reference
radio is 446.06875 MHz (PMR446 channel 6) with RxPL 3Z = 123.0 Hz. The HackRF is tuned 250 kHz
below the channel and the signal is shifted up digitally, so the HackRF's own LO leakage lands
outside the channel. Defaults: amp off, TX VGA 30 dB — a few cm-to-metre bench link (VGA 0 was too
weak for the bench radio to see a carrier; 35 decodes PL reliably), plus a +2.5 kHz frequency
correction measured for the bench HackRF (``--cal-hz``). Still far below PMR446's 500 mW.

  ctcss_tx.py                       # 446.06875 MHz, 123.0 Hz PL + 1 kHz audio, until Ctrl-C
  ctcss_tx.py --no-tone             # same carrier and audio, no PL (squelch must stay closed)
  ctcss_tx.py --freq 446.13125 --tone 88.5 --seconds 10
  ctcss_tx.py --write-only out.iq   # just generate the 8-bit IQ file (hackrf_transfer format)
"""
import argparse
import os
import subprocess
import sys
import tempfile
import time

import numpy as np

FS = 2_000_000          # 1 s of IQ at this rate loops seamlessly (integer cycles of every tone)
OFFSET = 250_000        # HackRF LO sits this far below the channel
CAL_HZ = 2500           # bench HackRF's correction: the radio decodes PL reliably at +2.5 kHz, not at 0


def make_iq(tone=123.0, audio=1000.0, tone_dev=500.0, audio_dev=2500.0):
    """One second of 8-bit interleaved IQ: FM with ``audio`` Hz at ``audio_dev`` and (if ``tone``)
    the CTCSS tone at ``tone_dev``, centred ``OFFSET`` Hz above the HackRF LO."""
    t = np.arange(FS) / FS
    dev = audio_dev * np.sin(2 * np.pi * audio * t) if audio else np.zeros(FS)
    if tone:
        dev = dev + tone_dev * np.sin(2 * np.pi * tone * t)
    phase = 2 * np.pi * np.cumsum(dev) / FS + 2 * np.pi * OFFSET * t
    iq = np.empty(2 * FS, np.int8)
    iq[0::2] = np.round(100 * np.cos(phase))
    iq[1::2] = np.round(100 * np.sin(phase))
    return iq


def demod_check(iq):
    """Deviation (Hz) at 123/the tone and at 1 kHz after FM-demodulating ``iq`` — a self-test."""
    z = iq[0::2].astype(np.float32) + 1j * iq[1::2].astype(np.float32)
    z *= np.exp(-2j * np.pi * OFFSET * np.arange(len(z)) / FS)
    z = np.convolve(z, np.ones(40) / 40, "same")[::40]
    f2 = FS / 40
    inst = np.angle(z[1:] * np.conj(z[:-1])) * f2 / (2 * np.pi)
    spec = np.abs(np.fft.rfft(inst * np.hanning(len(inst)))) / len(inst) * 4
    fr = np.fft.rfftfreq(len(inst), 1 / f2)
    return lambda f0: float(spec[max(0, np.argmin(abs(fr - f0)) - 3):np.argmin(abs(fr - f0)) + 4].max())


def transmit(path, freq_hz, seconds=None, vga=35, amp=False):
    """Loop ``path`` with hackrf_transfer. Returns the Popen (seconds=None) or waits ``seconds``."""
    cmd = ["hackrf_transfer", "-t", path, "-f", str(int(freq_hz - OFFSET)), "-s", str(FS),
           "-x", str(vga), "-a", "1" if amp else "0", "-R"]
    # The HackRF stays busy for a moment after the previous hackrf_transfer exits; a new one started
    # too soon quits at once and nothing is transmitted. Check it's still running, retry if not.
    for attempt in range(5):
        p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.8)
        if p.poll() is None:
            break
        time.sleep(1.0)
    else:
        raise RuntimeError("hackrf_transfer would not start (HackRF busy or missing?)")
    if seconds is None:
        return p
    try:
        p.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        p.terminate()
        p.wait()
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--freq", type=float, default=446.06875, help="channel, MHz")
    ap.add_argument("--tone", type=float, default=123.0, help="CTCSS tone, Hz")
    ap.add_argument("--no-tone", action="store_true")
    ap.add_argument("--audio", type=float, default=1000.0, help="audio test tone, Hz (0 = none)")
    ap.add_argument("--seconds", type=float, default=None, help="stop after N s (default: Ctrl-C)")
    ap.add_argument("--vga", type=int, default=35, help="HackRF TX VGA gain, dB (0-47)")
    ap.add_argument("--cal-hz", type=float, default=CAL_HZ,
                    help="frequency correction added to --freq (default: the bench HackRF's)")
    ap.add_argument("--amp", action="store_true", help="enable the HackRF RF amp (+14 dB)")
    ap.add_argument("--write-only", metavar="OUT.iq")
    args = ap.parse_args()
    tone = None if args.no_tone else args.tone
    iq = make_iq(tone=tone, audio=args.audio)
    dev = demod_check(iq)
    print(f"signal: {args.freq:.5f} MHz, PL {tone or 'none'}, audio {args.audio} Hz "
          f"(self-check: {dev(tone or 123.0):.0f} Hz dev at the tone, {dev(1000):.0f} Hz at 1 kHz)")
    if args.write_only:
        iq.tofile(args.write_only)
        return
    fd, path = tempfile.mkstemp(suffix=".iq")
    os.close(fd)
    iq.tofile(path)
    try:
        p = transmit(path, args.freq * 1e6 + args.cal_hz, args.seconds, args.vga, args.amp)
        if p is not None:
            print("transmitting - Ctrl-C to stop")
            try:
                p.wait()
            except KeyboardInterrupt:
                p.terminate()
                p.wait()
    finally:
        os.unlink(path)


if __name__ == "__main__":
    sys.exit(main())
