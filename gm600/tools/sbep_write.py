#!/usr/bin/env python3
"""Write-probe for GM600 per-channel option bits.

Replays DPS's captured write stream (sniff-write.log) verbatim, optionally
patching the four channel +6 option bytes so one write session binds up to
4 candidate bits (distinct bit per channel). Fully reversible: --restore
replays the stream unmodified to return the radio to baseline.

usage: sbep_write.py [--dry | --write | --restore] [CAPTURE_LOG]

Modes:
  --dry       (default) parse + print patched frames, send NOTHING
  --write     send the PATCHED stream (probe)
  --restore   send the UNMODIFIED stream (baseline)

CAPTURE_LOG is the socat sniff log of a DPS write session (default: sniff-write.log in the current
directory). PROBE_SET=<name> picks a patch table below; SBEP_ADDR=host:port the applet socket.

Every frame's echo, ACK and reply are checked, and the stream ABORTS on the first problem (echo
mismatch, no ACK, or a 17 whose result isn't 84 for the same address), so a failed block is never
followed by more writes.
"""
import os
import re
import sys
import time

from sbep import Radio, SBEPError, ACK, checksum, frame_length

LOG = "sniff-write.log"

# channel +6 byte radio addresses and the probe bit to OR in (default byte = 0x08)
# PROBE table selectable via env PROBE_SET; default = checksum-neutral test
_SETS = {
    # net byte-sum delta == 0 : tests the "block total sum" checksum model
    "balanced": {0x0F6D: 0x09,   # ch1 +1 (set bit0)
                 0x0F74: 0x07},  # ch2 -1 (clear bit3)
    # original 4-bit probe (needs valid checksum)
    "bits":     {0x0F6D: 0x09, 0x0F74: 0x0A, 0x0F7B: 0x0C, 0x0F82: 0x18},
    # 4-bit probe + padding-byte compensation (delta +0x17 -> set 0x0F06=0xE9)
    "bits_comp": {0x0F6D: 0x09, 0x0F74: 0x0A, 0x0F7B: 0x0C, 0x0F82: 0x18, 0x0F06: 0xE9},
    # bits 0,1,2 on ch1/2/3 (ch4 untouched); delta +7 -> 0x0F06=0xF9
    "bits012":  {0x0F6D: 0x09, 0x0F74: 0x0A, 0x0F7B: 0x0C, 0x0F06: 0xF9},
    # single bit per channel for isolation; override via env PROBE_SET
    "b4":   {0x0F6D: 0x18, 0x0F06: 0xF0},   # ch1 bit4, delta +0x10
    "b567": {0x0F6D: 0x28, 0x0F74: 0x48, 0x0F7B: 0x88, 0x0F06: (0x100-(0x20+0x40+0x80))&0xFF},
    # bits 0,1,2 on ch1/2/3, each compensated by that channel's Rx-low byte
    # (option +k, Rx-low -k) -> personality block sum unchanged, all in-block, no network touch
    "bits012f": {0x0F6D: 0x09, 0x0F68: 0x6b-1,     # ch1 bit0, Rx 0x6b->0x6a
                 0x0F74: 0x0A, 0x0F6F: 0x75-2,     # ch2 bit1, Rx 0x75->0x73
                 0x0F7B: 0x0C, 0x0F76: 0x6f-4},    # ch3 bit2, Rx 0x6f->0x6b
    # single bit4 on ch1 compensated by ch1 Rx-low (avoids ch4/TxPL inhibit conflict)
    "b4f":  {0x0F6D: 0x18, 0x0F68: 0x6b-0x10},     # ch1 bit4, Rx 0x6b->0x5b
    # bits 0,1,2 on ch1/2/3, compensated by lowering ch4 Tx PL tone index (0x13->0x0C, valid tone)
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

def send_stream(frames, label):
    print("== sending %s (%d frames) ==" % (label, len(frames)))
    writes = 0
    with Radio() as radio:
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
    mode = args.pop(0) if args and args[0].startswith("--") else "--dry"
    if mode not in ("--dry", "--write", "--restore"):
        sys.exit(__doc__)
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
        send_stream(patched, "PROBE (patched)")
    else:
        send_stream(patched, "RESTORE (baseline)")

if __name__ == "__main__":
    main()
