#!/usr/bin/env python3
"""Program a GM600 conventional channel's RX/TX frequency and PL tones over SBEP, no DPS needed.

Channel table at EEPROM 0x0F67, 4 records x 7 bytes: RX word, TX word, TX PL index, RX PL index,
options (CODEPLUG.md). Frequency words: 375 MHz + (v & 0x7FFF) x (6.25 kHz if bit 15 else 5 kHz)
(FREQ_ENCODING.md); like DPS, the 5 kHz grid is preferred and 6.25 kHz used only when needed. The
options byte (power, monitor, data, Tx inhibit) is left as it is. The write goes through
gm600py.codeplug.apply (backup, checksum fix, changed chunks only, read-back verify, reset).

  setchan.py 1 439.075 431.475 --pl 123.0          # RX/TX MHz, same PL on TX and RX
  setchan.py 2 446.00625 446.00625 --tx-pl off --rx-pl off
"""
import argparse
import sys

import gm600py.link.sbep as sbep
from gm600py.codeplug import CHANNEL_LEN, CHANNELS, N_CHANNELS, RADIO_BASE, apply, freq_mhz, read_image, summary
from gm600py.radio.setpl import tone_index

BAND = (403.0, 470.0)            # UHF GM600 (DPS range 403-470 MHz)


def freq_word(mhz):
    """Encode a frequency; raises ValueError if it's off both the 5 kHz and the 6.25 kHz grid."""
    if not BAND[0] <= mhz <= BAND[1]:
        raise ValueError(f"{mhz} MHz is outside {BAND[0]}-{BAND[1]} MHz")
    off = round((mhz - 375.0) * 1_000_000)
    if off % 5000 == 0 and off // 5000 < 0x8000:
        word = off // 5000
    elif off % 6250 == 0 and off // 6250 < 0x8000:
        word = 0x8000 | off // 6250
    else:
        raise ValueError(f"{mhz} MHz is not on the 5 kHz or 6.25 kHz grid")
    assert abs(freq_mhz(word) - mhz) < 1e-9
    return word


def set_channel(r, channel, rx_mhz, tx_mhz, tx_pl, rx_pl, **apply_kw):
    """Returns (backup path, chunks written, the channel's decoded summary after)."""
    if not 1 <= channel <= N_CHANNELS:
        raise ValueError(f"channel must be 1..{N_CHANNELS}, not {channel}")
    rec = bytes(freq_word(rx_mhz).to_bytes(2, "big") + freq_word(tx_mhz).to_bytes(2, "big")
                + bytes([tone_index(tx_pl), tone_index(rx_pl)]))
    new = read_image(r)
    at = CHANNELS - RADIO_BASE + (channel - 1) * CHANNEL_LEN
    new[at:at + 6] = rec                                   # byte 6 (options) unchanged
    backup, chunks = apply(r, new, **apply_kw)
    return backup, chunks, summary(bytes(new))["channels"][channel - 1]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("channel", type=int)
    ap.add_argument("rx_mhz", type=float)
    ap.add_argument("tx_mhz", type=float)
    ap.add_argument("--pl", help="same PL on TX and RX (Hz or off)")
    ap.add_argument("--tx-pl")
    ap.add_argument("--rx-pl")
    args = ap.parse_args(argv)
    tx_pl = args.tx_pl or args.pl or "off"
    rx_pl = args.rx_pl or args.pl or "off"
    try:
        freq_word(args.rx_mhz), freq_word(args.tx_mhz), tone_index(tx_pl), tone_index(rx_pl)
    except ValueError as e:
        sys.exit(f"error: {e}")
    with sbep.Radio() as r:
        backup, chunks, c = set_channel(r, args.channel, args.rx_mhz, args.tx_mhz, tx_pl, rx_pl)
    print(f"backup {backup}; wrote chunks {[hex(a) for a in chunks]}; channel {args.channel:03d}: "
          f"RX {c['rx_mhz']} PL {c['rx_pl']} / TX {c['tx_mhz']} PL {c['tx_pl']}, options {c['options']} "
          "(radio resetting)")


if __name__ == "__main__":
    sys.exit(main())
