#!/usr/bin/env python3
"""Set the GM600 displayed idle number (MPT1327 Own Unit No.) over SBEP, no DPS needed.

The Own Unit No. is a big-endian 16-bit value in the codeplug at 0x0F0B-0x0F0C (322=0x0142), in the
Personality block (checksum at 0x0FF4). The write goes through gm600py.codeplug.apply (backup,
checksum fix, changed chunks only, read-back verify, reset). Verified on hardware (322/456/712).

  usage: setunit.py <number>      e.g. setunit.py 456
"""
import sys

import gm600py.link.sbep as sbep
from gm600py.codeplug import OWN_UNIT, RADIO_BASE, apply, read_image


def set_unit(r, value, **apply_kw):
    if not 0 <= value <= 0xFFFF:
        raise ValueError(f"Own Unit No. must be 0..65535, not {value}")
    new = read_image(r)
    new[OWN_UNIT - RADIO_BASE:OWN_UNIT - RADIO_BASE + 2] = value.to_bytes(2, "big")
    return apply(r, new, **apply_kw)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        value = int(argv[0])
        if len(argv) != 1 or not 0 <= value <= 0xFFFF:
            raise ValueError
    except (ValueError, IndexError):
        sys.exit("usage: setunit.py <number>   (0..65535, e.g. 456)")
    with sbep.Radio() as r:
        backup, chunks = set_unit(r, value)
    print(f"set Own Unit No. = {value} (0x{value:03X}); wrote {[hex(a) for a in chunks]}; "
          f"backup {backup}; reset sent")


if __name__ == "__main__":
    sys.exit(main())
