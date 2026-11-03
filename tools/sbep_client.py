#!/usr/bin/env python3
"""Minimal SBEP client for the GM600 via the glasgow-rib applet socket.
Half-duplex with echo: applet echoes our TX bytes, then the radio reply follows.
Frame checksum = (0xFF - sum(frame_bytes_excl_cks)) & 0xFF.
READ-ONLY by default; writes require --allow-write.
"""
import socket, sys, time

HOST, PORT = "127.0.0.1", 2324

def cks(b):
    return (0xFF - (sum(b) & 0xFF)) & 0xFF

def short_frame(op, data=b""):
    n = 1 + len(data)              # op + data count
    assert n <= 0x0D
    body = bytes([0xF0 | n, op]) + bytes(data)
    return body + bytes([cks(body)])

def long_frame(op, data=b""):
    n = 1 + len(data)             # opcode + data count (big-endian)
    body = bytes([0xFF, op, (n >> 8) & 0xFF, n & 0xFF]) + bytes(data)
    return body + bytes([cks(body)])

class Radio:
    def __init__(self):
        self.s = socket.create_connection((HOST, PORT), timeout=5)
        self.s.settimeout(2.0)
        self.buf = bytearray()
    def _drain(self, want, deadline):
        while len(self.buf) < want and time.time() < deadline:
            try:
                d = self.s.recv(256)
                if d: self.buf += d
                else: break
            except socket.timeout:
                break
        out = bytes(self.buf[:want]); del self.buf[:want]
        return out
    def xfer(self, frame, expect_reply_len, settle=0.4):
        # flush stale
        self.s.settimeout(0.05)
        try:
            while True:
                d = self.s.recv(256)
                if not d: break
        except socket.timeout:
            pass
        self.buf.clear()
        self.s.settimeout(2.0)
        self.s.sendall(frame)
        deadline = time.time() + 3.0
        # expect echo of len(frame) + reply
        total = len(frame) + expect_reply_len
        data = self._drain(total, deadline)
        time.sleep(settle)
        # grab anything extra
        self.s.settimeout(0.2)
        try:
            while True:
                d = self.s.recv(256)
                if not d: break
                data += d
        except socket.timeout:
            pass
        echo = data[:len(frame)]
        reply = data[len(frame):]
        return echo, reply
    def close(self):
        self.s.close()

def main():
    r = Radio()
    print("connected to applet", HOST, PORT)
    # 1) serial query 0x23 sub 0x01
    f = short_frame(0x23, bytes([0x01]))
    echo, reply = r.xfer(f, 40)
    print("TX serial-query :", f.hex())
    print("   echo         :", echo.hex(), "OK" if echo == f else "MISMATCH")
    print("   reply        :", reply.hex())
    # extract ascii from reply
    txt = bytes(c for c in reply if 32 <= c < 127)
    print("   ascii        :", txt.decode("ascii", "replace"))
    # 2) read 32 bytes @ 0x000F60 (covers channel +6 bytes)
    addr = 0x000F60; ln = 0x20
    f = short_frame(0x11, bytes([ln, (addr >> 16) & 0xFF, (addr >> 8) & 0xFF, addr & 0xFF]))
    echo, reply = r.xfer(f, 4 + 3 + ln + 1 + 2)
    print("TX read@0x0F60  :", f.hex())
    print("   echo         :", echo.hex(), "OK" if echo == f else "MISMATCH")
    print("   reply        :", reply.hex())
    r.close()

if __name__ == "__main__":
    main()
