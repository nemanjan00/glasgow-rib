"""GM600 codeplug (640-byte EEPROM image, 0x0D80-0x0FFF): checksums, summary, safe write.

Everything is re-exported here, so ``import gm600py.codeplug as gm600_codeplug`` keeps the old API."""
from gm600py.codeplug.checksum import *  # noqa: F401,F403
from gm600py.codeplug.checksum import CHECKSUM_ADDRS, OFFSET, RADIO_BASE, block_checksum, blocks, \
    fix_checksums, verify_checksums
from gm600py.codeplug.summary import *  # noqa: F401,F403
from gm600py.codeplug.summary import BLOCK_NAMES, CHANNELS, CHANNEL_LEN, N_CHANNELS, OWN_UNIT, freq_mhz, \
    pl_tones, summary
from gm600py.codeplug.apply import ApplyError, apply, changed_chunks, read_image  # noqa: F401
