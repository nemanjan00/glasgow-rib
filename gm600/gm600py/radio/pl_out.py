#!/usr/bin/env python3
"""Accessory pin 4 as a PL-decoded (tone-squelch-open) output, driven from the host over SBEP.

Pin 4 is EXTERNAL_ALARM = ASFIC GCB1 = bit 1 of byte 8 of the ASFIC image at 0x37AC (open collector:
on = pulled to ~0.6 V, off = pulled up to the supply). The firmware's "PL decoded / RX active" flag is
0x347E. Each poll compares the two and, when they differ, launches asfic_push.S with GCB1REQ = the
wanted state: the stub changes GCB1 in byte 8 with interrupts off (nothing else in the image is
touched, never the power-latch bit 2) and pushes the image to the ASFIC under the firmware's SPI lock.
The firmware rebuilds the image on its own squelch changes; if it drops GCB1, the next poll puts it
back. A prototype: latency is one poll plus a push (~0.2-0.5 s), and the host must stay connected.

Standalone (the live panel can do the same with --pl-out; only one SBEP client at a time):
  python3 -m gm600py.radio.pl_out [--poll 0.1] [--dmm 10.69.2.52]
"""
import argparse
import socket
import sys
import time

import gm600py.firmware.asm6811 as asm6811
import gm600py.link.sbep as sbep
import gm600py.stubs as stubs

RXFLAG = 0x347E          # PL decoded / RX active (0x347F = carrier)
GCBYTE = 0x37B4          # 0x37AC + 8
GCB1 = 0x02
GCB1REQ = 0x3F02         # asfic_push.S parameter: 0/1 = clear/set GCB1, bit 7 = plain push


def _write(r, addr, data):
    st, _, _ = r.transact(sbep.encode(0x17, addr.to_bytes(3, "big") + bytes(data)))
    if st != sbep.ACK:
        raise sbep.SBEPError(f"write @0x{addr:04X}: status {st:02X}")


class PlOut:
    def __init__(self, r):
        self.r = r
        a = asm6811.assemble_file(stubs.path("asfic_push.S"))
        self.org, self.code = a.org, a.code
        self.upload()

    def upload(self):
        for off in range(0, len(self.code), 32):
            _write(self.r, self.org + off, self.code[off:off + 32])

    def set_gcb1(self, on):
        """Set or clear GCB1 in the image and push it to the ASFIC."""
        _write(self.r, GCB1REQ, [1 if on else 0])
        try:
            sbep.launch_returning_stub(self.r, self.org, self.code)
        except sbep.SBEPError as e:
            if "changed before launch" not in str(e):
                raise
            self.upload()                       # a reset's boot RAM test wiped the stub
            _write(self.r, GCB1REQ, [1 if on else 0])
            sbep.launch_returning_stub(self.r, self.org, self.code)
        self.r.drain()
        time.sleep(0.03)                        # the stub returns into the firmware

    def sync(self, pl, gcbyte):
        """Make GCB1 follow the PL flag. Returns True if it pushed."""
        want = bool(pl)
        if want != bool(gcbyte & GCB1):
            self.set_gcb1(want)
            return True
        return False

    def poll_once(self):
        pl = self.r.read_block(RXFLAG, 1)[0]
        g = self.r.read_block(GCBYTE, 1)[0]
        return pl, g, self.sync(pl, g)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--poll", type=float, default=0.1, help="seconds between polls")
    ap.add_argument("--dmm", help="optional SCPI DMM host on pin 4, logged on every change")
    args = ap.parse_args()
    dmm = socket.create_connection((args.dmm, 5025), timeout=5) if args.dmm else None

    def volts():
        if not dmm:
            return ""
        dmm.sendall(b"READ?\n")
        return f"  pin4 {float(dmm.recv(100)):.3f} V"

    with sbep.Radio() as r:
        out = PlOut(r)
        last = None
        try:
            while True:
                try:
                    pl, g, pushed = out.poll_once()
                except (sbep.SBEPError, OSError) as e:
                    print(f"link error: {e}", flush=True)
                    time.sleep(1.0)
                    r.drain()
                    continue
                if pushed or pl != last:
                    time.sleep(0.2 if pushed else 0)
                    print(f"{time.strftime('%H:%M:%S')} PL {'open ' if pl else 'shut '} gcb 0x{g:02X}"
                          f"{' -> push' if pushed else ''}{volts()}", flush=True)
                    last = pl
                time.sleep(args.poll)
        except KeyboardInterrupt:
            pass
        finally:
            out.set_gcb1(False)                 # leave pin 4 off
    return 0


if __name__ == "__main__":
    sys.exit(main())
