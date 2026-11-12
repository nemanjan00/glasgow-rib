#!/usr/bin/env python3
"""GM600 (600-series DPS) codeplug checksums.

The 640-byte native codeplug (radio internal EEPROM 0x0D80-0x0FFF, as read over SBEP
opcode 0x11) is protected by FIVE per-block additive checksums. Each checksum byte sits
immediately after its block's data and equals:

    checksum = (sum(block_data_bytes) - 0x55) & 0xFF

Reversed from the radio firmware (motofix.BIN): the per-block validator at 0xB8E1/0xB900
sums each block byte-by-byte and checks  stored == 0xAA - ~sum  (= sum - 0x55). Verified
against the live native-eeprom.bin (all five blocks) and in the 68HC11 emulator (a wrong
checksum shows an error display; the corrected codeplug boots normally).

A host that edits the codeplug (an open CPS, a field patch) must recompute the affected
block's checksum before writing it back to the radio.
"""

RADIO_BASE = 0x0D80          # radio address of codeplug byte 0 (the 640-byte image starts here)

# Checksum byte radio-addresses, in order. Each block's DATA is (previous_checksum+1 .. this_checksum-1),
# the first block starting at RADIO_BASE.
CHECKSUM_ADDRS = [0x0D85, 0x0D93, 0x0E12, 0x0F07, 0x0FF4]
#   0x0D85/0x0D93 = serial/header blocks, 0x0E12 = Radio, 0x0F07 = Network, 0x0FF4 = Personality.

OFFSET = 0x55                # firmware constant: stored = (sum - 0x55) & 0xFF


def blocks(base=RADIO_BASE):
    """Yield (data_start_addr, data_end_addr_exclusive, checksum_addr) for each block."""
    start = base
    for c in CHECKSUM_ADDRS:
        yield start, c, c
        start = c + 1


def block_checksum(cp, data_start, data_end, base=RADIO_BASE):
    """The additive checksum of cp[data_start:data_end] (radio addresses)."""
    return (sum(cp[data_start - base:data_end - base]) - OFFSET) & 0xFF


def fix_checksums(cp, base=RADIO_BASE):
    """Recompute and store all five block checksums in-place. cp is a mutable 640-byte buffer."""
    for ds, de, ca in blocks(base):
        cp[ca - base] = block_checksum(cp, ds, de, base)
    return cp


def verify_checksums(cp, base=RADIO_BASE):
    """Return a list of (checksum_addr, stored, computed) for blocks whose checksum is wrong."""
    bad = []
    for ds, de, ca in blocks(base):
        computed = block_checksum(cp, ds, de, base)
        stored = cp[ca - base]
        if computed != stored:
            bad.append((ca, stored, computed))
    return bad
