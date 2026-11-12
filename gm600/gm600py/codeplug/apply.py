"""The one safe way to change a GM600 codeplug over SBEP — every codeplug-editing tool uses this.

    old = read_image(r)
    new = bytearray(old); new[addr - RADIO_BASE] = value
    apply(r, new)

apply(): re-reads the live image, refuses if its checksums are already bad (something is wrong; don't
make it worse), saves a timestamped backup, fixes the block checksums of ``new``, writes ONLY the
32-byte chunks that differ (0x17, each must answer 0x84), reads the whole image back and compares, then
resets the radio (0x10) so it reloads the codeplug. Returns (backup_path, [chunk addresses written]).
Backups go to gm600py.paths.backup_dir("codeplug"): $GM600_BACKUP_DIR/codeplug or
~/.local/share/gm600/codeplug.
"""
import os
import time

import gm600py.link.sbep as sbep
import gm600py.paths as paths
from gm600py.codeplug.checksum import RADIO_BASE, fix_checksums, verify_checksums

SIZE = 0x280                    # 640 bytes, 0x0D80-0x0FFF
CHUNK = 32                      # DPS-sized write/read blocks


class ApplyError(Exception):
    pass


def read_image(r):
    """The live 640-byte codeplug as a bytearray."""
    return bytearray(b"".join(r.read_block(a, CHUNK) for a in range(RADIO_BASE, RADIO_BASE + SIZE, CHUNK)))


def changed_chunks(old, new):
    """Radio addresses of the CHUNK-aligned blocks where ``old`` and ``new`` differ."""
    return sorted({(RADIO_BASE + i) // CHUNK * CHUNK for i in range(SIZE) if old[i] != new[i]})


def apply(r, new, backup_dir=None, reset=True):
    new = bytearray(new)
    if len(new) != SIZE:
        raise ApplyError(f"image must be {SIZE} bytes, not {len(new)}")
    old = read_image(r)
    if verify_checksums(bytes(old)):
        raise ApplyError("the radio's codeplug already has bad checksums - not writing")
    backup_dir = backup_dir or paths.backup_dir("codeplug")       # $GM600_BACKUP_DIR or ~/.local/share/gm600
    os.makedirs(backup_dir, exist_ok=True)
    backup = os.path.join(backup_dir, f"codeplug-{time.strftime('%Y%m%d-%H%M%S')}.bin")
    with open(backup, "wb") as f:
        f.write(old)
    fix_checksums(new)
    chunks = changed_chunks(old, new)
    for a in chunks:
        data = bytes(new[a - RADIO_BASE:a - RADIO_BASE + CHUNK])
        st, op, _ = r.transact(sbep.encode(0x17, a.to_bytes(3, "big") + data))
        if st != sbep.ACK or op != 0x84:
            raise ApplyError(f"write @0x{a:04X} failed (status {st:02X}, op {op}); backup: {backup}")
    if read_image(r) != new:
        raise ApplyError(f"read-back differs from what was written; restore from {backup}")
    if reset and chunks:
        r.drain()
        r.sock.sendall(sbep.encode(0x10))           # reset: reload the codeplug
        time.sleep(0.4)
    return backup, chunks
