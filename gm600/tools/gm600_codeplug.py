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


if __name__ == "__main__":
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


# --- human-readable summary (web preview, CLI) -------------------------------------------------

CHANNELS, CHANNEL_LEN, N_CHANNELS = 0x0F67, 7, 4      # conventional channel table (CODEPLUG.md)
OWN_UNIT = 0x0F0B                                     # MPT1327 Own Unit No. (BE16) = idle display


def freq_mhz(word):
    """FREQ_ENCODING.md: 375 MHz + (v & 0x7FFF) * (6.25 kHz if bit 15 else 5 kHz)."""
    return 375.0 + (word & 0x7FFF) * (0.00625 if word & 0x8000 else 0.005)


def pl_tones():
    """index -> label ("123.0 Hz (3Z)"), read from the pl_tone enum in ../gm600_codeplug.ksy."""
    import os
    import re
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "gm600_codeplug.ksy")
    text = open(path).read()
    block = text[text.index("  pl_tone:"):]
    out = {}
    for idx, doc in re.findall(r"\n    (\d+):\n      id: \w+\n      doc: ([^\n]+)", block):
        out[int(idx)] = doc.strip()
        if len(out) > 60:
            break
    return out


def summary(cp, base=RADIO_BASE):
    """Dict with the Own Unit No., the conventional channels and the block-checksum status."""
    tones = pl_tones()
    at = lambda a: a - base
    chans = []
    for n in range(N_CHANNELS):
        r = cp[at(CHANNELS) + n * CHANNEL_LEN: at(CHANNELS) + (n + 1) * CHANNEL_LEN]
        rx, tx = int.from_bytes(r[0:2], "big"), int.from_bytes(r[2:4], "big")
        if rx == 0 and tx == 0:
            continue
        chans.append({"channel": n + 1, "rx_mhz": round(freq_mhz(rx), 5), "tx_mhz": round(freq_mhz(tx), 5),
                      "tx_pl": tones.get(r[4], f"#{r[4]}"), "rx_pl": tones.get(r[5], f"#{r[5]}"),
                      "options": f"0x{r[6]:02X}"})
    bad = verify_checksums(bytes(cp), base)
    return {"own_unit": int.from_bytes(cp[at(OWN_UNIT):at(OWN_UNIT) + 2], "big"),
            "channels": chans,
            "checksums": "all valid" if not bad else f"BAD: {bad}",
            "size": len(cp)}
