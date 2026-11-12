#!/usr/bin/env python3
"""Set a GM600 conventional channel's RX or TX PL (CTCSS) tone over SBEP, no DPS needed.

Channel table at EEPROM 0x0F67, 4 records x 7 bytes: RX word, TX word, TX PL index, RX PL index,
options (CODEPLUG.md). The PL index list is the pl_tone enum in ../gm600_codeplug.ksy. Like setunit.py:
read the whole codeplug, save a backup, patch the byte, fix the block checksums, write the 32-byte
chunks that changed with 0x17, verify by reading back, then reset (0x10) so the radio reloads it.

  setpl.py 4 rx 123.0      # channel 004: RX PL 123.0 Hz (3Z)
  setpl.py 4 rx off        # channel 004: RX PL disabled
  setpl.py 1 tx 88.5
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sbep
from gm600_codeplug import CHANNELS, CHANNEL_LEN, N_CHANNELS, RADIO_BASE, fix_checksums, pl_tones, \
    summary, verify_checksums

CHUNK = 32


def tone_index(text):
    if text.lower() in ("off", "none", "disabled", "0"):
        return 0
    want = f"{float(text):.1f} Hz"
    for idx, label in pl_tones().items():
        if label.startswith(want):
            return idx
    raise SystemExit(f"no PL tone {want} in the pl_tone list")


def read_codeplug(r):
    return bytearray(b"".join(r.read_block(a, CHUNK) for a in range(RADIO_BASE, 0x1000, CHUNK)))


def main():
    if len(sys.argv) != 4 or sys.argv[2] not in ("rx", "tx"):
        sys.exit(__doc__.strip().split("\n\n")[-1])
    ch, which, idx = int(sys.argv[1]), sys.argv[2], tone_index(sys.argv[3])
    if not 1 <= ch <= N_CHANNELS:
        sys.exit(f"channel must be 1..{N_CHANNELS}")
    addr = CHANNELS + (ch - 1) * CHANNEL_LEN + (5 if which == "rx" else 4)
    with sbep.Radio() as r:
        old = read_codeplug(r)
        if verify_checksums(bytes(old)):
            sys.exit("the radio's codeplug has bad checksums already - not touching it")
        backup = f"codeplug-backup-{time.strftime('%Y%m%d-%H%M%S')}.bin"
        open(backup, "wb").write(old)
        new = bytearray(old)
        new[addr - RADIO_BASE] = idx
        fix_checksums(new)
        changed = sorted({(RADIO_BASE + i) // CHUNK * CHUNK for i in range(len(new)) if new[i] != old[i]})
        print(f"backup {backup}; 0x{addr:04X}: {old[addr - RADIO_BASE]} -> {idx} ({pl_tones()[idx]}); "
              f"writing chunks {[hex(a) for a in changed]}")
        for a in changed:
            chunk = bytes(new[a - RADIO_BASE:a - RADIO_BASE + CHUNK])
            st, op, _ = r.transact(sbep.encode(0x17, a.to_bytes(3, "big") + chunk))
            if st != sbep.ACK or op != 0x84:
                raise SystemExit(f"write @0x{a:04X} failed (status {st:02X}, op {op}); backup is {backup}")
        if read_codeplug(r) != new:
            raise SystemExit(f"read-back mismatch! restore from {backup}")
        r.drain()
        r.sock.sendall(sbep.encode(0x10))           # reset: reload the codeplug
    c = summary(bytes(new))["channels"][ch - 1]
    print(f"done: channel {ch:03d} RX {c['rx_mhz']} PL {c['rx_pl']} / TX {c['tx_mhz']} PL {c['tx_pl']} "
          f"(radio resetting)")


if __name__ == "__main__":
    sys.exit(main())
