#!/usr/bin/env python3
"""A fake GM600 behind a fake applet socket, for testing the tools without hardware.

Behaves like ``glasgow run rib … socket`` with a GM600 attached: echoes every frame, ACKs it with
``50``, and answers ``23`` (ID strings), ``13`` (block size), ``11`` (reads of up to 64 bytes from a
640-byte EEPROM image at 0x0D80), ``17`` (writes, answered ``84`` + address) and ``10`` (ACK only).
Reads of more than 64 bytes are refused with a bare ``80``, like the real radio.

usage: mock_radio.py [EEPROM.bin] [PORT]      (default: a blank image, port 2399)
"""
import socket
import sys
import threading

from sbep import ACK, REFUSED, encode, decode, frame_length

IDS = {0: b"\x02\x09", 1: b"807IYA0903", 3: b"R03.03.21\x00", 4: b"02.03\x00"}


class MockRadio:
    def __init__(self, image=bytes(640), port=0, *, fail_write_at=None):
        self.eeprom = bytearray(image)
        self.fail_write_at = fail_write_at      # address whose 17 gets an 85 (failed) result
        self.frames = []                        # every frame received, in order
        self.srv = socket.create_server(("127.0.0.1", port))
        self.port = self.srv.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def close(self):
        self.srv.close()

    def _answer(self, frame):
        op, data = decode(frame)
        self.frames.append(frame)
        if op == 0x10:
            return bytes([ACK])
        if op == 0x23:
            return bytes([ACK]) + encode(0x8B, bytes([data[0]]) + IDS.get(data[0], b""))
        if op == 0x13:
            return bytes([ACK]) + encode(0x82, b"\x00\x20\x00")
        if op == 0x11:
            length, addr = data[0], int.from_bytes(data[1:4], "big") & 0xFFFF
            if length > 0x40:
                return bytes([REFUSED])
            off = addr - 0x0D80
            block = bytes(self.eeprom[off:off + length]) if 0 <= off < 640 else bytes(length)
            return bytes([ACK]) + encode(0x80, data[1:4] + block)
        if op == 0x17:
            addr = int.from_bytes(data[:3], "big") & 0xFFFF
            if addr == self.fail_write_at:
                return bytes([ACK]) + encode(0x85, data[:3])
            off = addr - 0x0D80
            self.eeprom[off:off + len(data) - 3] = data[3:]
            return bytes([ACK]) + encode(0x84, data[:3])
        return bytes([0x60])

    def _serve(self):
        while True:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            buf = bytearray()
            with conn:
                while True:
                    try:
                        chunk = conn.recv(4096)
                    except OSError:
                        break
                    if not chunk:
                        break
                    buf += chunk
                    while (n := frame_length(buf)) is not None and len(buf) >= n:
                        frame = bytes(buf[:n])
                        del buf[:n]
                        conn.sendall(frame + self._answer(frame))   # echo, then the reply


if __name__ == "__main__":
    image = open(sys.argv[1], "rb").read() if len(sys.argv) > 1 else bytes(640)
    radio = MockRadio(image, int(sys.argv[2]) if len(sys.argv) > 2 else 2399)
    print(f"mock GM600 listening on 127.0.0.1:{radio.port}")
    radio.thread.join()
