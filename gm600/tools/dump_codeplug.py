#!/usr/bin/env python3
"""Dump the full GM600 codeplug (640 B, radio 0x0D80-0x0FFF) via the applet socket.

Each 0x11 read is checked end to end: our echo, the ACK, the reply frame's checksum, and the
address the radio echoes back. Any mismatch aborts the dump, so a corrupted byte can't land in the
file silently.

usage: dump_codeplug.py OUT.bin      (SBEP_ADDR=host:port, default 127.0.0.1:2324)
"""
import sys
import time

from sbep import Radio, SBEPError

START, END, CHUNK = 0x0D80, 0x1000, 0x20


def main(outpath):
    t0 = time.time()
    out = bytearray()
    try:
        with Radio() as radio:
            for addr in range(START, END, CHUNK):
                out += radio.read_block(addr, CHUNK)
    except SBEPError as e:
        print("read failed:", e)
        sys.exit(1)
    with open(outpath, "wb") as f:
        f.write(out)
    print("dumped %d bytes in %.1fs -> %s" % (len(out), time.time() - t0, outpath))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: dump_codeplug.py OUT.bin   (keep your first dump; don't overwrite it)")
    main(sys.argv[1])
