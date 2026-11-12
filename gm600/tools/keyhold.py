#!/usr/bin/env python3
"""Press and hold a GM600 front-panel key over SBEP — no flash patch, no hardware.

How: pause the keypad task (set bit 0x10 in its TCB flags at 0x11D8, which the scheduler treats as
"waiting"), then post the key event through the firmware's own keypad-event routine 0x4D8B using the
non-halting RAM stub keyevt.S (launched with 0x26, returns into the running firmware). With the
keypad task paused, no real scan reports the key released, so the firmware sees it held. After
``--hold`` seconds the stub posts the release and the keypad task is resumed. Validated in emu6811
first (long-hold A latches conventional channel 1), then on the radio.

Event numbers (0x4D8B's D argument): 16-19 = vol down, vol up, clear, base call;
20-23 = A, B, C, D; 6-8 = scroll down, scroll up, on/off (see FIRMWARE.md).

  keyhold.py                       # long-hold A (event 20) for 9 s -> conventional mode
  keyhold.py --event 23 --hold 0.5 # tap D
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import asm6811
import sbep

KEYPAD_TCB_FLAGS = 0x11D8
PAUSE = 0x10
EVT, STATE = 0x3F00, 0x3F01


def write(r, addr, data):
    st, _, _ = r.transact(sbep.encode(0x17, addr.to_bytes(3, "big") + bytes(data)))
    if st != sbep.ACK:
        raise sbep.SBEPError(f"write @0x{addr:04X}: status {st:02X}")


def post(r, org, code, event, state):
    write(r, EVT, [event, state])
    sbep.launch_returning_stub(r, org, code)
    time.sleep(0.1)                     # the stub returns into the firmware
    r.drain()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--event", type=int, default=20, help="keypad event (20 = A)")
    ap.add_argument("--hold", type=float, default=9.0, help="seconds to hold")
    args = ap.parse_args()
    a = asm6811.assemble_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), "keyevt.S"))
    with sbep.Radio() as r:
        print(f"radio {r.id_string(1)}, display {r.read_block(0x34BD, 3)!r}")
        for off in range(0, len(a.code), 32):
            write(r, a.org + off, a.code[off:off + 32])
        flags = r.read_block(KEYPAD_TCB_FLAGS, 1)[0]
        write(r, KEYPAD_TCB_FLAGS, [flags | PAUSE])
        try:
            post(r, a.org, a.code, args.event, 1)
            t0 = time.time()
            while time.time() - t0 < args.hold:
                time.sleep(1.0)
                print(f"  {time.time() - t0:4.1f} s  display {r.read_block(0x34BD, 3)!r}", flush=True)
            post(r, a.org, a.code, args.event, 0)
        finally:
            flags = r.read_block(KEYPAD_TCB_FLAGS, 1)[0]
            write(r, KEYPAD_TCB_FLAGS, [flags & ~PAUSE & 0xFF])
        time.sleep(1.0)
        print(f"released; display {r.read_block(0x34BD, 3)!r}")


if __name__ == "__main__":
    sys.exit(main())
