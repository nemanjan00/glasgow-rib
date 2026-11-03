#!/usr/bin/env python3
"""Dump the full GM600 codeplug (640 B, radio 0x0D80-0x0FFF) via the applet socket."""
import socket, sys, time

HOST, PORT = "127.0.0.1", 2324

def cks(b): return (0xFF - (sum(b) & 0xFF)) & 0xFF
def short(op, data=b""):
    body = bytes([0xF0 | (1+len(data)), op]) + bytes(data)
    return body + bytes([cks(body)])

def xfer(s, frame, want, t=3.0):
    s.settimeout(0.05)
    try:
        while s.recv(256): pass
    except socket.timeout: pass
    s.settimeout(2.0); s.sendall(frame)
    buf = bytearray(); dl = time.time()+t
    while len(buf) < want and time.time() < dl:
        try:
            d = s.recv(256)
            if d: buf += d
        except socket.timeout: break
    return bytes(buf)

def main(outpath):
    s = socket.create_connection((HOST, PORT), timeout=5)
    out = bytearray()
    for addr in range(0x0D80, 0x1000, 0x20):
        ah, am, al = (addr>>16)&0xFF, (addr>>8)&0xFF, addr&0xFF
        f = short(0x11, bytes([0x20, ah, am, al]))
        rep = xfer(s, f, len(f) + 1 + 8 + 0x20 + 1 + 8)
        # long reply: FF 80 00 24 <ah><am><al> <32 data> cks  (len 0x24 = op + 3 addr + 32)
        hdr = bytes([0xFF, 0x80, 0x00, 0x24, ah, am, al])
        i = rep.find(hdr)
        if i < 0 or i+7+0x20 > len(rep):
            print("read fail @0x%04X: %s" % (addr, rep.hex())); sys.exit(1)
        data = rep[i+7:i+7+0x20]
        out += data
    s.close()
    open(outpath, "wb").write(out)
    print("dumped %d bytes -> %s" % (len(out), outpath))

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/cp_dump.bin")
