#!/usr/bin/env python3
"""Probe ASFIC programming bits on a running GM600: set one bit of the 0x37AC image, push it, measure.

0x37AC is the 21-byte image the firmware sends to the ASFIC (SPI dispatcher entry 8, decoder select
code 2). The firmware resends it only when it changes something itself, so a bit we set and push with
asfic_push.S stays in the ASFIC until then. For each candidate bit that is currently 0: set it, push,
read a networked DMM (SCPI, e.g. on accessory pin 4 = EXTERNAL_ALARM / GCB1), clear it, push again.
Bits are only ever SET, never cleared, so the power latch (GCB2) can't drop. Stops at the first
reading that moves by more than --delta volts, or if the radio stops answering (a clock-control bit
could break the link; a PSU power cycle recovers).

  python3 -m gm600py.radio.asfic_probe --bytes 0,1 --dmm 10.69.2.52
"""
import argparse
import socket
import sys
import time

import gm600py.firmware.asm6811 as asm6811
import gm600py.link.sbep as sbep
import gm600py.stubs as stubs

IMAGE = 0x37AC


class DMM:
    def __init__(self, host, port=5025):
        self.s = socket.create_connection((host, port), timeout=5)

    def volts(self):
        self.s.sendall(b"READ?\n")
        return float(self.s.recv(100))


def write(r, addr, data):
    st, _, _ = r.transact(sbep.encode(0x17, addr.to_bytes(3, "big") + bytes(data)))
    if st != sbep.ACK:
        raise sbep.SBEPError(f"write @0x{addr:04X}: status {st:02X}")


def push(r, stub):
    sbep.launch_returning_stub(r, stub.org, stub.code)
    r.drain()
    time.sleep(0.3)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--bytes", default="0,1", help="image byte indexes to probe, e.g. 0,1 or 0-20")
    ap.add_argument("--dmm", required=True, help="DMM host (SCPI on port 5025)")
    ap.add_argument("--delta", type=float, default=1.0, help="volts change that counts as a hit")
    ap.add_argument("--settle", type=float, default=0.6)
    args = ap.parse_args()
    idx = []
    for part in args.bytes.split(","):
        lo, _, hi = part.partition("-")
        idx += range(int(lo), int(hi or lo) + 1)
    dmm = DMM(args.dmm)
    stub = asm6811.assemble_file(stubs.path("asfic_push.S"))
    with sbep.Radio() as r:
        for off in range(0, len(stub.code), 32):
            write(r, stub.org + off, stub.code[off:off + 32])
        base_v = dmm.volts()
        print(f"image {bytes(r.read_block(IMAGE, 21)).hex(' ')}  baseline {base_v:.3f} V", flush=True)
        for i in idx:
            for bit in range(8):
                cur = r.read_block(IMAGE + i, 1)[0]
                if cur & (1 << bit):
                    continue
                write(r, IMAGE + i, [cur | (1 << bit)])
                try:
                    push(r, stub)
                    time.sleep(args.settle)
                    v = dmm.volts()
                finally:
                    now = r.read_block(IMAGE + i, 1)[0]          # also proves the link is alive
                    write(r, IMAGE + i, [now & ~(1 << bit) & 0xFF])
                    push(r, stub)
                after = dmm.volts()
                hit = abs(v - base_v) > args.delta
                print(f"byte {i:2d} bit {bit}  {v:7.3f} V  (after restore {after:.3f} V){'  <-- HIT' if hit else ''}",
                      flush=True)
                if hit:
                    return 0
    print("no bit moved the measured voltage")
    return 1


if __name__ == "__main__":
    sys.exit(main())
