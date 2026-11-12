#!/usr/bin/env python3
"""Write-probe for GM600 per-channel option bits.

Replays DPS's captured write stream (sniff-write.log) verbatim, optionally
patching the four channel +6 option bytes so one write session binds up to
4 candidate bits (distinct bit per channel). Fully reversible: --restore
replays the stream unmodified to return the radio to baseline.

usage: sbep_write.py [--dry | --write | --restore] [--fix | --force] [CAPTURE_LOG]

Modes:
  --dry       (default) parse + print patched frames, send NOTHING
  --write     send the PATCHED stream (probe)
  --restore   send the UNMODIFIED stream (baseline)

CAPTURE_LOG is the socat sniff log of a DPS write session (default: sniff-write.log in the current
directory). PROBE_SET=<name> picks a patch table below; SBEP_ADDR=host:port the applet socket.

Checksum safety (--write only): before sending, the resulting codeplug is reconstructed (the
radio's current image + the pending writes) and its five block checksums are verified (see
gm600_codeplug.py). A write that would leave any block checksum wrong is REFUSED with nothing sent —
the radio would then fail DPS's next read. `--fix` corrects them instead (appends 0x17 writes for
the affected checksum bytes); `--force` sends anyway. `--restore` is a trusted replay of DPS's own
valid stream and isn't re-checked.

Every frame's echo, ACK and reply are checked, and the stream ABORTS on the first problem (echo
mismatch, no ACK, or a 17 whose result isn't 84 for the same address), so a failed block is never
followed by more writes.
"""
import os
import re
import sys
import time

from gm600py.link.sbep import Radio, SBEPError, ACK, checksum, frame_length, encode, decode
from gm600py.codeplug import verify_checksums, fix_checksums, RADIO_BASE

LOG = "sniff-write.log"
EEPROM_LO, EEPROM_HI = 0x0D80, 0x1000       # the 640-byte codeplug, [lo, hi)

# Channel +6 byte radio addresses and the probe bit to OR in (default byte = 0x08).
# PROBE table selectable via env PROBE_SET; default = checksum-neutral test.
#
# Checksum note: the +6 option bytes and the per-channel Rx/Tx bytes (0x0F68.. / 0x0F80) are all in
# the PERSONALITY block (checksum 0x0FF4), but 0x0F06 is in the NETWORK block (checksum 0x0F07).
# Only the sets whose changes net to zero WITHIN each block are checksum-neutral on their own:
#   balanced, bits012f, bits012p  -> neutral (compensated inside the personality block).
# The rest (bits, bits_comp, bits012, b4, b567) are NOT self-neutral: `bits` doesn't compensate at
# all, and the `0x0F06` "compensation" lands in the Network block, so it leaves BOTH the personality
# and network checksums wrong. They exist as raw bit-isolation probes; `--write` recomputes/refuses
# via the checksum guard (`--fix`/`--force`), so they are only usable with `--fix`.
_SETS = {
    # neutral: +1/-1 within the personality block
    "balanced": {0x0F6D: 0x09,   # ch1 +1 (set bit0)
                 0x0F74: 0x07},  # ch2 -1 (clear bit3)
    # NOT neutral: raw 4-bit probe, no compensation (needs --fix)
    "bits":     {0x0F6D: 0x09, 0x0F74: 0x0A, 0x0F7B: 0x0C, 0x0F82: 0x18},
    # NOT neutral: the 0x0F06 tweak is in the NETWORK block, so both blocks' sums move (needs --fix)
    "bits_comp": {0x0F6D: 0x09, 0x0F74: 0x0A, 0x0F7B: 0x0C, 0x0F82: 0x18, 0x0F06: 0xE9},
    # NOT neutral: 0x0F06 is Network block, not Personality (needs --fix)
    "bits012":  {0x0F6D: 0x09, 0x0F74: 0x0A, 0x0F7B: 0x0C, 0x0F06: 0xF9},
    # NOT neutral: 0x0F06 is Network block (needs --fix)
    "b4":   {0x0F6D: 0x18, 0x0F06: 0xF0},   # ch1 bit4
    "b567": {0x0F6D: 0x28, 0x0F74: 0x48, 0x0F7B: 0x88, 0x0F06: (0x100-(0x20+0x40+0x80))&0xFF},
    # neutral: each option bit compensated by that channel's Rx-low byte, all in the personality block
    "bits012f": {0x0F6D: 0x09, 0x0F68: 0x6b-1,     # ch1 bit0, Rx 0x6b->0x6a
                 0x0F74: 0x0A, 0x0F6F: 0x75-2,     # ch2 bit1, Rx 0x75->0x73
                 0x0F7B: 0x0C, 0x0F76: 0x6f-4},    # ch3 bit2, Rx 0x6f->0x6b
    # NOT neutral on its own (single bit4 + one Rx byte, net +0x08 in personality) (needs --fix)
    "b4f":  {0x0F6D: 0x18, 0x0F68: 0x6b-0x10},     # ch1 bit4, Rx 0x6b->0x5b
    # neutral: option bits compensated by lowering ch4 Tx PL tone index (0x13->0x0C, valid tone)
    "bits012p": {0x0F6D: 0x09, 0x0F74: 0x0A, 0x0F7B: 0x0C, 0x0F80: 0x13-7},
}
PROBE_SET = os.environ.get("PROBE_SET", "balanced")
if PROBE_SET not in _SETS:
    sys.exit("unknown PROBE_SET %r; choose one of: %s" % (PROBE_SET, ", ".join(_SETS)))
PROBE = _SETS[PROBE_SET]

def parse_sent(path):
    sent = []; cur = None
    for line in open(path):
        if line.startswith('> '): cur = 's'; continue
        if line.startswith('< '): cur = 'r'; continue
        if line.startswith(' ') and cur == 's':
            for tok in line.split():
                if re.fullmatch(r'[0-9a-fA-F]{2}', tok):
                    sent.append(int(tok, 16))
    return bytes(sent)

def split_frames(sent):
    i = 0; frames = []
    while i < len(sent):
        if (sent[i] & 0xF0) == 0xF0:
            total = frame_length(sent[i:])
            if total is None or i + total > len(sent):
                break                       # capture ends mid-frame: drop the partial tail
            frames.append(sent[i:i+total]); i += total
        else:
            i += 1
    return frames

def patch_frame(fr):
    """If a long 0x17 write covers a PROBE address, patch the data byte + fix cks."""
    if len(fr) < 8 or fr[0] != 0xFF or fr[1] != 0x17:
        return fr, []
    ln = (fr[2] << 8) | fr[3]
    a = (fr[4] << 16) | (fr[5] << 8) | fr[6]
    data = bytearray(fr[7:-1])
    changed = []
    for off in range(len(data)):
        addr = a + off
        if addr in PROBE:
            old = data[off]; new = PROBE[addr]
            data[off] = new; changed.append((addr, old, new))
    if not changed:
        return fr, []
    body = bytes(fr[0:7]) + bytes(data)
    return body + bytes([checksum(body)]), changed

def check(fr, status, op, data):
    """Returns an error string if the radio's answer to ``fr`` isn't what DPS got, else None."""
    if status != ACK:
        return "no ACK (status 0x%02X)" % status
    sent_op = fr[1]
    if sent_op == 0x17:
        if op != 0x84:
            return "write failed (reply op 0x%02X)" % op
        if data[:3] != fr[4:7]:
            return "write result for the wrong address (%s)" % data[:3].hex()
    return None

RESTORE_HINT = ("The radio may be partly written. Fix the cause, then run "
                "`sbep_write.py --restore <capture log>` to put the captured codeplug back.")


def read_image(radio):
    """Dump the current 640-byte codeplug (0x0D80-0x1000) over 0x11 reads."""
    img = bytearray()
    while len(img) < EEPROM_HI - EEPROM_LO:
        img += radio.read_block(EEPROM_LO + len(img), min(64, EEPROM_HI - EEPROM_LO - len(img)))
    return img


def apply_writes(img, frames):
    """Apply every frame's 0x17 payload onto `img` (radio addresses), in place."""
    for fr in frames:
        op, data = decode(fr)
        if op != 0x17:
            continue
        a = int.from_bytes(data[:3], "big")
        for i, b in enumerate(data[3:]):
            off = a + i - EEPROM_LO
            if 0 <= off < len(img):
                img[off] = b
    return img


def checksum_guard(radio, frames, fix, force):
    """Refuse (or repair) a write that would leave a block checksum wrong.

    Reads the radio's current codeplug, simulates the pending writes, and runs verify_checksums on
    the result. Returns the frames to send — unchanged if the checksums hold, or with corrective
    0x17 writes appended under --fix. Aborts (nothing sent) otherwise, unless --force."""
    img = read_image(radio)
    apply_writes(img, frames)
    bad = verify_checksums(img)
    if not bad:
        print("checksum guard: all 5 block checksums valid after the write.")
        return frames
    where = ", ".join("0x%04X (stored %02X, want %02X)" % b for b in bad)
    if fix:
        fixed = bytearray(img)
        fix_checksums(fixed)
        extra = [encode(0x17, ca.to_bytes(3, "big") + bytes([fixed[ca - RADIO_BASE]]))
                 for ca, _, _ in bad]
        print("checksum --fix: correcting %d block(s): %s" % (len(bad), where))
        return frames + extra
    if force:
        print("WARNING: write breaks %d block checksum(s): %s — --force given, sending anyway."
              % (len(bad), where))
        return frames
    sys.exit("ABORT, nothing sent: write would break %d block checksum(s): %s\n"
             "Re-run with --fix to correct them, or --force to send regardless." % (len(bad), where))


def send_stream(frames, label, *, verify=False, fix=False, force=False):
    writes = 0
    with Radio() as radio:
        if verify:
            frames = checksum_guard(radio, frames, fix, force)
        print("== sending %s (%d frames) ==" % (label, len(frames)))
        for k, fr in enumerate(frames):
            op = fr[1]
            # 10 restarts the radio: only a status byte comes back.
            try:
                status, rop, data = radio.transact(fr, reply=(op != 0x10))
            except SBEPError as e:
                sys.exit("  [%2d] op=0x%02X ABORT: %s\n%s" % (k, op, e, RESTORE_HINT))
            err = check(fr, status, rop, data)
            if err:
                sys.exit("  [%2d] op=0x%02X tx=%s ABORT: %s\n%s" % (k, op, fr.hex(), err, RESTORE_HINT))
            if op == 0x17:
                writes += 1
            print("  [%2d] op=0x%02X ok" % (k, op))
            time.sleep(0.05)
    print("== done: all %d frames acknowledged, %d writes OK ==" % (len(frames), writes))

def main():
    args = sys.argv[1:]
    flags = []
    while args and args[0].startswith("--"):
        flags.append(args.pop(0))
    unknown = set(flags) - {"--dry", "--write", "--restore", "--fix", "--force"}
    if unknown:
        sys.exit("unknown flag(s): %s\n%s" % (" ".join(sorted(unknown)), __doc__))
    mode = next((f for f in ("--dry", "--write", "--restore") if f in flags), "--dry")
    fix, force = "--fix" in flags, "--force" in flags
    sent = parse_sent(args[0] if args else LOG)
    frames = split_frames(sent)
    patched = []; all_changes = []
    for fr in frames:
        if mode == "--restore":
            patched.append(fr)
        else:
            pf, ch = patch_frame(fr)
            patched.append(pf); all_changes += ch
    if mode != "--restore":
        print("patches:")
        for addr, old, new in all_changes:
            print("  radio 0x%04X: 0x%02X -> 0x%02X" % (addr, old, new))
        if len(all_changes) != len(PROBE):
            msg = "expected %d patches, applied %d" % (len(PROBE), len(all_changes))
            if mode == "--write":
                # The capture doesn't cover every probe address: writing would be a different
                # experiment than the one asked for.
                sys.exit("ABORT, nothing sent: " + msg)
            print("WARNING: " + msg)
    if mode == "--dry":
        print("DRY RUN - nothing sent. %d frames ready." % len(patched))
        return
    if mode == "--write":
        send_stream(patched, "PROBE (patched)", verify=True, fix=fix, force=force)
    else:
        send_stream(patched, "RESTORE (baseline)")   # trusted replay of DPS's own valid stream

if __name__ == "__main__":
    main()
