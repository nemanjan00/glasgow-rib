#!/usr/bin/env python3
"""Set a GM600 conventional channel's RX or TX PL (CTCSS) tone over SBEP, no DPS needed.

Channel table at EEPROM 0x0F67, 4 records x 7 bytes: RX word, TX word, TX PL index, RX PL index,
options (CODEPLUG.md). The PL index list is the pl_tone enum in gm600_codeplug.ksy. The write goes
through gm600py.codeplug.apply (backup, checksum fix, changed chunks only, read-back verify, reset).

  setpl.py 4 rx 123.0      # channel 004: RX PL 123.0 Hz (3Z)
  setpl.py 4 rx off        # channel 004: RX PL disabled
  setpl.py 1 tx 88.5
"""
import sys

import gm600py.link.sbep as sbep
from gm600py.codeplug import CHANNEL_LEN, CHANNELS, N_CHANNELS, RADIO_BASE, apply, pl_tones, read_image, summary

USAGE = __doc__.strip().split("\n\n")[-1]


def tone_index(text):
    """PL index for a frequency in Hz ("123.0") or off/none/disabled/0. Raises ValueError."""
    if text.lower() in ("off", "none", "disabled", "0"):
        return 0
    try:
        want = f"{float(text):.1f} Hz"
    except ValueError:
        raise ValueError(f"PL must be a frequency in Hz (e.g. 123.0) or 'off', not {text!r}")
    for idx, label in pl_tones().items():
        if label.startswith(want):
            return idx
    raise ValueError(f"no PL tone {want} in the pl_tone list")


def pl_address(channel, which):
    if which not in ("rx", "tx"):
        raise ValueError(f"which must be rx or tx, not {which!r}")
    if not 1 <= channel <= N_CHANNELS:
        raise ValueError(f"channel must be 1..{N_CHANNELS}, not {channel}")
    return CHANNELS + (channel - 1) * CHANNEL_LEN + (5 if which == "rx" else 4)


def set_pl(r, channel, which, tone, **apply_kw):
    """Set the PL; returns (backup path, chunks written, the channel's decoded summary after).
    ``apply_kw`` goes to gm600py.codeplug.apply (e.g. backup_dir)."""
    addr = pl_address(channel, which)
    idx = tone_index(tone)
    new = read_image(r)
    new[addr - RADIO_BASE] = idx
    backup, chunks = apply(r, new, **apply_kw)
    return backup, chunks, summary(bytes(new))["channels"][channel - 1]


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 3:
        sys.exit(f"usage:\n{USAGE}")
    try:
        try:
            channel = int(argv[0])
        except ValueError:
            raise ValueError(f"channel must be a number 1..4, not {argv[0]!r}")
        pl_address(channel, argv[1])
        tone_index(argv[2])
    except ValueError as e:
        sys.exit(f"error: {e}\nusage:\n{USAGE}")
    with sbep.Radio() as r:
        backup, chunks, c = set_pl(r, channel, argv[1], argv[2])
    print(f"backup {backup}; wrote chunks {[hex(a) for a in chunks]}; channel {channel:03d}: "
          f"RX {c['rx_mhz']} PL {c['rx_pl']} / TX {c['tx_mhz']} PL {c['tx_pl']} (radio resetting)")


if __name__ == "__main__":
    sys.exit(main())
