#!/usr/bin/env python3
"""Switch a GM600 between trunked and conventional mode with two RAM writes — no key hold.

The mode lives in the CPU's internal RAM, which the standby supply keeps across power-off (that's why
the radio comes back up in the mode it was left in). Found by diffing internal RAM 0x0000-0x02FF in
both modes on the radio, then bisecting in emu6811 (warm reset with RAM kept):

  0x0085   0xFF = conventional, 0x01 = trunked
  0x0089   bit 1 (0x02) set = conventional, bit 2 (0x04) set = trunked; other bits untouched

Both are needed (either alone does nothing). The firmware reads them at boot, so the switch is:
write both with SBEP 0x17, then 0x10 (the radio resets itself; internal RAM survives). Verified on
the bench radio both ways (322 <-> 1). The radio re-runs its self-test and answers again after ~10 s.

  gm600_mode.py            # show the current mode
  gm600_mode.py conv       # switch to conventional
  gm600_mode.py trunk      # switch to trunked
"""
import os
import sys
import time

import gm600py.link.sbep as sbep

MODE_A, MODE_B = 0x0085, 0x0089
MODES = {"conv": True, "conventional": True, "trunk": False, "trunked": False}


def read_mode(r):
    a, b = r.read_block(MODE_A, 1)[0], r.read_block(MODE_B, 1)[0]
    if a == 0xFF and b & 0x06 == 0x02:
        return "conventional"
    if a == 0x01 and b & 0x06 == 0x04:
        return "trunked"
    return f"unknown (0085={a:02X} 0089={b:02X})"


def set_mode(r, want, wait=12.0):
    """``want`` = "conventional" / "conv" or "trunked" / "trunk". Writes the two bytes, resets the
    radio with 0x10 and waits ``wait`` s for it to come back. Returns the mode it reports then."""
    if want not in MODES:
        raise ValueError(f"mode must be one of {sorted(MODES)}, not {want!r}")
    conv = MODES[want]
    b = r.read_block(MODE_B, 1)[0]
    for addr, val in ((MODE_A, 0xFF if conv else 0x01),
                      (MODE_B, (b & ~0x06 & 0xFF) | (0x02 if conv else 0x04))):
        st, _, _ = r.transact(sbep.encode(0x17, addr.to_bytes(3, "big") + bytes([val])))
        if st != sbep.ACK:
            raise sbep.SBEPError(f"write @0x{addr:04X}: status {st:02X}")
    r.drain()
    r.sock.sendall(sbep.encode(0x10))           # reset: the firmware reads the mode at boot
    time.sleep(wait)
    r.drain()
    return read_mode(r)


def main():
    with sbep.Radio() as r:
        if len(sys.argv) < 2:
            print(read_mode(r), "- display", r.read_block(0x34BD, 3))
            return
        if sys.argv[1] not in MODES:
            sys.exit(f"usage: gm600_mode.py [{'|'.join(MODES)}]  (got {sys.argv[1]!r})")
        print("now:", set_mode(r, sys.argv[1]), "- display", r.read_block(0x34BD, 3))


if __name__ == "__main__":
    sys.exit(main())
