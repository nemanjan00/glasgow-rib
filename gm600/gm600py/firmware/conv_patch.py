#!/usr/bin/env python3
"""Flash patch: a cold start comes up CONVENTIONAL instead of trunked.

The mode lives in a small block of internal RAM that the HC11 standby supply keeps across power-off
(FIRMWARE.md, "The mode bytes"). Page 9 owns it:

    0x0084..0x0088  state: 0x85 = mode (FF conventional / 01 trunked), 0x86 = conventional channel index
    0x0089          check byte = 0xAA + sum(0x84..0x88)

At boot (page 9 0x4106: jsr 0x4DAA) the firmware recomputes the check byte. If it matches, the
retained mode is used. If not — the standby supply ran out after a long power-off, or the block was
clobbered (seen after a DPS write) — 0x4D95 loads the defaults:

    4D95  C6 90     ldab #$90
    4D97  D7 84     stab $84
    4D99  C6 01     ldab #$01      <- default mode = trunked
    4D9B  D7 85     stab $85
    4D9D  7F 00 86  clr  $0086     ;  conventional channel index 0 = channel 1
    ...             jsr $49B5; bsr 0x4D32 (new check byte)

The patch changes that immediate to FF, so the defaults become conventional channel 1. A retained
mode is still honoured, so a long-hold A to trunked survives short power cuts exactly as before.

The same bytes sit at the same file offset in R03.03.21 (motofix.BIN, 0903) and R03.03.10 (0298), so
one patch fits both. Boot flash check (FIRMWARE.md): the sector stays XOR-neutral on its own via a
compensation byte in the same 256-byte sector — the operand of the unreachable `bra` at 0x4D30
(`20 FC`, emitted after the rts at 0x4D2F; nothing branches or jumps there). One sector write.

  python3 -m gm600py.firmware.conv_patch flash.bin patched.bin   # write a patched copy + print the diff
"""
import sys

SECTOR = 256
SITE = 0x24D9A                    # page 9, CPU 0x4D9A: operand of `ldab #$01` (default mode)
COMP = 0x24D31                    # page 9, CPU 0x4D31: operand of the dead `bra` at 0x4D30
SECTOR_BASE = SITE & ~0xFF        # 0x24D00 (page 9, window 0x4D00)
assert COMP & ~0xFF == SECTOR_BASE
# Context checked around both sites: the default-init sequence and the dead bra after an rts.
INIT_ORIG = bytes.fromhex("c690d784c601d7857f0086")      # file 0x24D95
INIT_AT = 0x24D95
DEAD_ORIG = bytes.fromhex("383920fc3c")                   # pulx; rts | bra (dead) | pshx (next fn)
DEAD_AT = 0x24D2E
NEW_MODE = 0xFF                                           # conventional
NEW_COMP = 0xFC ^ 0x01 ^ NEW_MODE                         # 0x02 keeps the sector XOR

# Images this was checked against (emulator cold boot: golden -> trunked '322', patched -> '  1').
KNOWN = {
    "db74c7c88911e9df1f7328e09975ae3a2db5d212be3df74bdeee0dd00aa530b9": "R03.03.21 golden (0903)",
    "4250be5f": "R03.03.10 (0298) full dump 2026-11-13",          # sha256 prefix
}


def _xor(bs):
    x = 0
    for b in bs:
        x ^= b
    return x


def state(img):
    """'original', 'patched', or raises if the sector isn't one of the two."""
    if img[INIT_AT:INIT_AT + 4] != INIT_ORIG[:4] or img[INIT_AT + 6:INIT_AT + 11] != INIT_ORIG[6:]:
        raise ValueError(f"unexpected default-init code at 0x{INIT_AT:05X}: {bytes(img[INIT_AT:INIT_AT + 11]).hex()}")
    if img[DEAD_AT:DEAD_AT + 3] != DEAD_ORIG[:3] or img[DEAD_AT + 4] != DEAD_ORIG[4]:
        raise ValueError(f"unexpected bytes around the dead bra at 0x{DEAD_AT:05X}: {bytes(img[DEAD_AT:DEAD_AT + 4]).hex()}")
    pair = (img[SITE - 1], img[SITE], img[COMP])
    if pair == (0xC6, 0x01, 0xFC):
        return "original"
    if pair == (0xC6, NEW_MODE, NEW_COMP):
        return "patched"
    raise ValueError(f"site/compensation bytes are neither original nor patched: {bytes(pair).hex()}")


def patch_image(flash):
    """Return the patched 256 KB image; raises unless the input is the unpatched original."""
    img = bytearray(flash)
    if len(img) != 0x40000:
        raise ValueError(f"expected a 256 KB flash image, got {len(img)} bytes")
    if state(img) != "original":
        raise ValueError("image is already patched")
    img[SITE] = NEW_MODE
    img[COMP] = NEW_COMP
    assert _xor(img[SECTOR_BASE:SECTOR_BASE + SECTOR]) == _xor(flash[SECTOR_BASE:SECTOR_BASE + SECTOR])
    return bytes(img)


def patch_diff(flash):
    """[(sector file offset, original 256 bytes, patched 256 bytes)] — a single sector."""
    new = patch_image(flash)
    return [(SECTOR_BASE, bytes(flash[SECTOR_BASE:SECTOR_BASE + SECTOR]), new[SECTOR_BASE:SECTOR_BASE + SECTOR])]


def main():
    if len(sys.argv) != 3:
        print(__doc__.split("\n\n")[-1])
        return 2
    import hashlib
    flash = open(sys.argv[1], "rb").read()
    h = hashlib.sha256(flash).hexdigest()
    name = KNOWN.get(h) or next((v for k, v in KNOWN.items() if h.startswith(k)), None)
    print(f"input: {name or 'UNKNOWN image (not one this was checked against)'}")
    open(sys.argv[2], "wb").write(patch_image(flash))
    for s, old, new in patch_diff(flash):
        changed = [i for i in range(SECTOR) if old[i] != new[i]]
        print(f"sector 0x{s:05X} (page 9 CPU 0x{0x4000 + (s & 0x3FFF):04X}): bytes "
              + ", ".join(f"0x{s + i:05X} {old[i]:02X}->{new[i]:02X}" for i in changed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
