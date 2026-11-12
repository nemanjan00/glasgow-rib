"""CLI: check (and optionally fix) a codeplug image's block checksums.

  python3 -m gm600py.codeplug <codeplug.bin> [--fix out.bin]
"""
from gm600py.codeplug.checksum import fix_checksums, verify_checksums

import sys
if len(sys.argv) < 2:
    print("usage: gm600_codeplug.py <codeplug.bin> [--fix out.bin]", file=sys.stderr)
    sys.exit(2)
data = bytearray(open(sys.argv[1], "rb").read())
if len(data) != 0x280:
    print("warning: expected 640 bytes, got %d" % len(data), file=sys.stderr)
bad = verify_checksums(data)
if not bad:
    print("all 5 block checksums OK")
else:
    for ca, stored, computed in bad:
        print("MISMATCH @0x%04X: stored %02X, computed %02X" % (ca, stored, computed))
if len(sys.argv) >= 4 and sys.argv[2] == "--fix":
    fix_checksums(data)
    open(sys.argv[3], "wb").write(data)
    print("fixed checksums written to", sys.argv[3])
