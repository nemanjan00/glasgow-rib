#!/usr/bin/env python3
"""Dump the full GM600 codeplug (640 B, radio 0x0D80-0x0FFF) via the applet socket.

Each 0x11 read returns, after our echoed frame: an ACK byte (0x50) then a 40-byte
long reply `FF 80 00 24 <ah><am><al> <32 data> <cks>`. We verify the ACK and the
frame checksum (sum of the whole reply frame, incl. the leading 0xFF, == 0xFF mod
256) so a corrupted byte can't land in the dump silently, and we read exactly the
expected length (echo + ACK + reply) so a good read returns immediately instead of
waiting out a timeout.
"""
import socket, sys, time

HOST, PORT = "127.0.0.1", 2324
ACK = 0x50
REPLY_LEN = 40           # FF 80 00 24 ah am al + 32 data + cks

def cks(b): return (0xFF - (sum(b) & 0xFF)) & 0xFF
def short(op, data=b""):
    body = bytes([0xF0 | (1+len(data)), op]) + bytes(data)
    return body + bytes([cks(body)])

def xfer(s, frame, t=1.0):
    # drain any stale bytes
    s.settimeout(0.05)
    try:
        while s.recv(256): pass
    except (socket.timeout, OSError): pass
    s.settimeout(t)
    s.sendall(frame)
    want = len(frame) + 1 + REPLY_LEN     # echo + ACK + reply
    buf = bytearray(); dl = time.time() + t
    while len(buf) < want and time.time() < dl:
        try:
            d = s.recv(256)
            if not d: break
            buf += d
        except socket.timeout:
            break
    return bytes(buf)

def read_block(s, addr):
    ah, am, al = (addr >> 16) & 0xFF, (addr >> 8) & 0xFF, addr & 0xFF
    f = short(0x11, bytes([0x20, ah, am, al]))
    rep = xfer(s, f)
    tail = rep[len(f):] if rep[:len(f)] == f else rep    # strip our echo
    hdr = bytes([0xFF, 0x80, 0x00, 0x24, ah, am, al])
    i = tail.find(hdr)
    if i < 0:
        raise IOError("@0x%04X no reply header: %s" % (addr, rep.hex()))
    if i < 1 or tail[i-1] != ACK:
        raise IOError("@0x%04X missing ACK 0x50: %s" % (addr, rep.hex()))
    frame = tail[i:i + REPLY_LEN]
    if len(frame) < REPLY_LEN:
        raise IOError("@0x%04X short reply (%d B): %s" % (addr, len(frame), rep.hex()))
    if (sum(frame) & 0xFF) != 0xFF:
        raise IOError("@0x%04X bad checksum: %s" % (addr, frame.hex()))
    return frame[7:7 + 0x20]

def main(outpath):
    t0 = time.time()
    s = socket.create_connection((HOST, PORT), timeout=5)
    out = bytearray()
    try:
        for addr in range(0x0D80, 0x1000, 0x20):
            out += read_block(s, addr)
    except IOError as e:
        print("read fail", e); sys.exit(1)
    finally:
        s.close()
    open(outpath, "wb").write(out)
    print("dumped %d bytes in %.1fs -> %s" % (len(out), time.time() - t0, outpath))

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/cp_dump.bin")
