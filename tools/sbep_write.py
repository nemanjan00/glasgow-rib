#!/usr/bin/env python3
"""Write-probe for GM600 per-channel option bits.

Replays DPS's captured write stream (sniff-write.log) verbatim, optionally
patching the four channel +6 option bytes so one write session binds up to
4 candidate bits (distinct bit per channel). Fully reversible: --restore
replays the stream unmodified to return the radio to baseline.

Modes:
  (default)   dry run: parse + print patched frames, send NOTHING
  --write     send the PATCHED stream (probe)
  --restore   send the UNMODIFIED stream (baseline)
"""
import socket, sys, time, re

HOST, PORT = "127.0.0.1", 2324
LOG = "sniff-write.log"

# channel +6 byte radio addresses and the probe bit to OR in (default byte = 0x08)
import os
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
PROBE = _SETS.get(os.environ.get("PROBE_SET", "balanced"))

def cks(b):
    return (0xFF - (sum(b) & 0xFF)) & 0xFF

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
        b = sent[i]
        if b == 0xFF:
            op = sent[i+1]; ln = (sent[i+2] << 8) | sent[i+3]
            total = 4 + (ln - 1) + 1
            frames.append(sent[i:i+total]); i += total
        elif (b & 0xF0) == 0xF0:
            n = b & 0x0F
            total = 1 + n + 1
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
    return body + bytes([cks(body)]), changed

def recv_all(s, nmin, t=3.0):
    s.settimeout(0.4); buf = bytearray(); dl = time.time() + t
    while time.time() < dl:
        try:
            d = s.recv(256)
            if d: buf += d
            elif len(buf) >= nmin: break
        except socket.timeout:
            if len(buf) >= nmin: break
    return bytes(buf)

def send_stream(frames, label):
    s = socket.create_connection((HOST, PORT), timeout=5)
    print("== sending %s (%d frames) ==" % (label, len(frames)))
    ok = 0; bad = 0
    for k, fr in enumerate(frames):
        s.settimeout(0.05)
        try:
            while True:
                if not s.recv(256): break
        except socket.timeout:
            pass
        s.settimeout(2.0)
        s.sendall(fr)
        rep = recv_all(s, len(fr) + 1)
        reply = rep[len(fr):]
        op = fr[1] if (fr[0] == 0xFF or (fr[0] & 0xF0) == 0xF0) and len(fr) > 1 else None
        tag = "0x%02X" % op if op is not None else "?"
        # for writes, look for 0x84 (ok) / 0x85 (fail) in reply
        note = ""
        if op == 0x17:
            if 0x84 in reply: note = "WRITE-OK"; ok += 1
            elif 0x85 in reply: note = "WRITE-FAIL"; bad += 1
            else: note = "NO-ACK?"; bad += 1
        print("  [%2d] op=%s tx=%s reply=%s %s" % (k, tag, fr.hex(), reply.hex(), note))
        time.sleep(0.05)
    s.close()
    print("== done: %d write-OK, %d write-fail/noack ==" % (ok, bad))

def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--dry"
    sent = parse_sent(LOG)
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
            print("WARNING: expected %d patches, applied %d" % (len(PROBE), len(all_changes)))
    if mode == "--dry":
        print("DRY RUN - nothing sent. %d frames ready." % len(patched))
        return
    if mode == "--write":
        send_stream(patched, "PROBE (patched)")
    elif mode == "--restore":
        send_stream(patched, "RESTORE (baseline)")
    else:
        print("unknown mode", mode)

if __name__ == "__main__":
    main()
