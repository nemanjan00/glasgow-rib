"""Human-readable GM600 codeplug summary: Own Unit No., conventional channels, checksum blocks."""
import os
import re

from gm600py.codeplug.checksum import (CHECKSUM_ADDRS, RADIO_BASE, block_checksum, blocks,
                                     verify_checksums)

# --- human-readable summary (web preview, CLI) -------------------------------------------------

CHANNELS, CHANNEL_LEN, N_CHANNELS = 0x0F67, 7, 4      # conventional channel table (CODEPLUG.md)
OWN_UNIT = 0x0F0B                                     # MPT1327 Own Unit No. (BE16) = idle display
BLOCK_NAMES = ["Header A", "Header B", "Radio", "Network", "Personality"]


def freq_mhz(word):
    """FREQ_ENCODING.md: 375 MHz + (v & 0x7FFF) * (6.25 kHz if bit 15 else 5 kHz)."""
    return 375.0 + (word & 0x7FFF) * (0.00625 if word & 0x8000 else 0.005)


def pl_tones():
    """index -> label ("123.0 Hz (3Z)"), read from the pl_tone enum in ../gm600_codeplug.ksy."""
    import os
    import re
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "gm600_codeplug.ksy")
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
    blks = []
    for (ds, de, ca), name in zip(blocks(base), BLOCK_NAMES):
        computed, stored = block_checksum(cp, ds, de, base), cp[at(ca)]
        blks.append({"name": name, "start": ds, "end": de, "checksum_addr": ca,
                     "stored": stored, "computed": computed, "ok": stored == computed})
    regions = [{"name": b["name"], "start": b["start"], "end": b["end"], "kind": f"blk{i}"}
               for i, b in enumerate(blks)]
    regions += [{"name": f"{b['name']} checksum", "start": b["checksum_addr"],
                 "end": b["checksum_addr"] + 1, "kind": "cks"} for b in blks]
    regions += [{"name": "Own Unit No. (shown on the display)", "start": OWN_UNIT, "end": OWN_UNIT + 2,
                 "kind": "unit"},
                {"name": "Conventional channel table (4 x 7 bytes)", "start": CHANNELS,
                 "end": CHANNELS + N_CHANNELS * CHANNEL_LEN, "kind": "chan"},
                {"name": "Factory field (in no checksum block)", "start": CHECKSUM_ADDRS[-1] + 1,
                 "end": base + len(cp), "kind": "factory"}]
    return {"own_unit": int.from_bytes(cp[at(OWN_UNIT):at(OWN_UNIT) + 2], "big"),
            "channels": chans,
            "checksums": "all valid" if not bad else f"BAD: {bad}",
            "blocks": blks, "regions": regions, "base": base, "bytes": list(cp),
            "size": len(cp)}
