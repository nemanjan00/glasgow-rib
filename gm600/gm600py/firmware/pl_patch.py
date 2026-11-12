#!/usr/bin/env python3
"""Flash patch: accessory pin 4 = "speaker unmuted" (a PL-decoded COR output on a PL channel), persistent.

See gm600py/stubs/pl_patch.S for the mechanism. Two changes to the 256 KB flash image:

  * PATCH_SITE (CPU 0x8F7F, file 0x38F7F, fixed bank): `ldab #$15; ldx #$37AC` (C6 15 CE 37 AC)
    -> `jsr $DE00; brn k` (BD DE 00 21 kk) — the start of SPI-dispatcher entry 8 (ASFIC image push).
    BRN ("branch never") is a 2-byte no-op whose operand k is free: it is chosen so this sector's XOR
    is unchanged.
  * CAVE (CPU 0xDE00, file 0x3DE00): the 25-byte plfix routine, in a sector that is entirely erased
    (FF) in the golden image and referenced by nothing, followed by ONE compensation byte (never
    executed) that makes this sector's XOR unchanged too.

The firmware checks its flash at boot (CPU 0x8DE8): XOR of every byte of the banked pages (count from
page 0 0x5B50) and of the fixed 0x8000-0xFFFF region must be 0xFF, else it sets an error flag (0x3480)
and the display shows "- 1" (found in the emulator: changing ONE erased byte does it). In that state
the firmware NAKs SBEP 0x11/0x17 [VM], so it can't be repaired over BUS+ — only off-board (T48).
Because EACH sector is XOR-neutral on its own, every intermediate state (cave written, hook not yet;
or the reverse) still passes the check. flash_xor() reproduces it.

Write order matters for a different reason: the hook must never point at an unwritten cave (FF FF =
`stx $FFxx`, a crash on the first unmute). patch_diff() returns the sectors in the safe apply order
(cave first, then the hook); revert in the reverse order (hook first, then the cave).

Both land in the fixed region (pages 14/15) and outside the AT29C020 boot blocks (first and last 8 KB).
This module builds the patched image and the per-sector diff for a flash writer; it refuses an image
whose original bytes don't match. Revert = write the original bytes back.

  python3 -m gm600py.firmware.pl_patch motofix.BIN patched.BIN   # write a patched copy + print the diff
"""
import sys

import gm600py.firmware.asm6811 as asm6811
import gm600py.stubs as stubs

GOLDEN_SHA256 = "db74c7c88911e9df1f7328e09975ae3a2db5d212be3df74bdeee0dd00aa530b9"
PATCH_SITE = 0x38F7F                          # file offset (CPU 0x8F7F)
PATCH_ORIG = bytes.fromhex("C615CE37AC")      # ldab #$15; ldx #$37AC
def _xor(bs):
    x = 0
    for b in bs:
        x ^= b
    return x


# jsr $DE00; brn k — k makes the 5-byte replacement XOR-neutral (sector XOR unchanged)
PATCH_NEW = bytes([0xBD, 0xDE, 0x00, 0x21]) + bytes([_xor(PATCH_ORIG) ^ _xor(b"\xBD\xDE\x00\x21")])
CAVE_CPU = 0xDE00
CAVE_SITE = 0x38000 + (CAVE_CPU - 0x8000)     # file 0x3DE00
SECTOR = 256                                  # AT29C020 program granularity


def cave_code():
    a = asm6811.assemble_file(stubs.path("pl_patch.S"))
    assert a.org == CAVE_CPU, hex(a.org)
    return a.code


def flash_xor(img):
    """The firmware's boot check (0x8DE8): XOR over pages 0..n-1 (n = page-0 byte 0x5B50) and the
    fixed region (file 0x38000-0x3FFFF). A good image gives 0xFF."""
    x = 0
    for page in range(img[0x1B50]):
        for b in img[page * 0x4000:(page + 1) * 0x4000]:
            x ^= b
    for b in img[0x38000:0x40000]:
        x ^= b
    return x


def patch_image(flash):
    """Return the patched 256 KB image; raises if the image isn't the expected original."""
    img = bytearray(flash)
    if len(img) != 0x40000:
        raise ValueError(f"expected a 256 KB flash image, got {len(img)} bytes")
    code = cave_code()
    if bytes(img[PATCH_SITE:PATCH_SITE + 5]) != PATCH_ORIG:
        raise ValueError(f"unexpected bytes at 0x{PATCH_SITE:05X}: {bytes(img[PATCH_SITE:PATCH_SITE + 5]).hex()}")
    if any(b != 0xFF for b in img[CAVE_SITE:CAVE_SITE + len(code) + 1]):
        raise ValueError(f"cave at 0x{CAVE_SITE:05X} is not erased")
    if any(b != 0xFF for b in img[CAVE_SITE & ~0xFF:(CAVE_SITE & ~0xFF) + SECTOR]):
        raise ValueError("the whole cave sector must be erased (a rewrite reprograms all 256 bytes)")
    assert _xor(PATCH_NEW) == _xor(PATCH_ORIG)    # the hook sector alone is XOR-neutral
    img[PATCH_SITE:PATCH_SITE + 5] = PATCH_NEW
    img[CAVE_SITE:CAVE_SITE + len(code)] = code
    comp = CAVE_SITE + len(code)                  # compensation byte, right after the code: was FF;
    img[comp] = 0xFF ^ _xor(code) ^ _xor(b"\xFF" * len(code))   # makes the cave sector XOR-neutral
    return bytes(img)


def patch_diff(flash):
    """[(sector file offset, original 256 bytes, patched 256 bytes)] in the SAFE APPLY ORDER: the cave
    sector first, then the hook. Revert = the same list reversed, writing the originals. Each sector on
    its own keeps the boot-check XOR, so any prefix of either sequence boots."""
    new = patch_image(flash)
    return [(s, bytes(flash[s:s + SECTOR]), new[s:s + SECTOR])
            for s in (CAVE_SITE // SECTOR * SECTOR, PATCH_SITE // SECTOR * SECTOR)]


def main():
    if len(sys.argv) != 3:
        print(__doc__.split("\n\n")[-1])
        return 2
    import hashlib
    flash = open(sys.argv[1], "rb").read()
    if hashlib.sha256(flash).hexdigest() != GOLDEN_SHA256:
        print("warning: input is not the golden motofix.BIN", file=sys.stderr)
    open(sys.argv[2], "wb").write(patch_image(flash))
    for s, old, new in patch_diff(flash):
        changed = [i for i in range(SECTOR) if old[i] != new[i]]
        print(f"sector 0x{s:05X} (CPU 0x{0x8000 + s - 0x38000:04X}): {len(changed)} bytes change, "
              f"0x{s + changed[0]:05X}-0x{s + changed[-1]:05X}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
