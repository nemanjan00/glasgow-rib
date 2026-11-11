#!/usr/bin/env python3
"""Power-cycle the GM600 by toggling the ISDT MP305B bench PSU (the 12 V radio supply).
Run with the pymp305 hardware venv. Keeps the user-set V/I; just off -> wait -> on."""
import sys, time, os
# pymp305 is an external install; point PYMP305_PATH at its python/ dir if not importable.
_p = os.environ.get("PYMP305_PATH")
if _p:
    sys.path.insert(0, _p)
from pymp305 import MP305
gap=float(sys.argv[1]) if len(sys.argv)>1 else 1.5
with MP305.open() as psu:
    s=psu.read_state(); print("before: V=%.2f out=%s"%(s.set_voltage,s.output))
    psu.output_off(); print("output OFF"); time.sleep(gap)
    psu.output_on(); time.sleep(0.2)
    s=psu.read_state(); print("output ON: set V=%.2f meas V=%.2f I=%.3f"%(s.set_voltage,s.voltage,s.current))
    psu.release_remote()
