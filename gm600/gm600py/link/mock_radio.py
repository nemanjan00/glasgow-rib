#!/usr/bin/env python3
"""A fake GM600 behind a fake applet socket, for testing the tools without hardware.

Behaves like ``glasgow run rib … socket`` with a GM600 attached: echoes every frame, ACKs it with
``50``, and answers ``23`` (ID strings), ``13`` (block size), ``11`` (reads of up to 64 bytes from an
EEPROM image), ``17`` (writes, answered ``84`` + address) and ``10`` (ACK only). Reads of more than
64 bytes are refused with a bare ``80``, like the real radio.

This is a faithful enough GM600 that **Motorola's own DPS reads it**: fed a real dump, its reply
bytes are identical to what a real radio returns to a DPS "Read Radio" (verified against a capture,
byte-for-byte). So you can load a saved codeplug into DPS with no hardware — bridge the applet port
this listens on to the DPS VM's COM1 (see tools/README.md) and Read Radio.

``image`` is served at ``base``: a 640-byte image is the codeplug alone (base 0x0D80); a larger dump
(e.g. the 4096-byte low-EEPROM) is served from 0x0000, so reads of any address in it work.

usage: mock_radio.py [EEPROM.bin] [PORT]      (default: a blank image, port 2399)
"""
import socket
import sys
import threading

from gm600py.link.sbep import ACK, NAK, REFUSED, encode, decode, frame_length

IDS = {0: b"\x02\x09", 1: b"807IYA0903", 3: b"R03.03.21\x00", 4: b"02.03\x00"}


class MockRadio:
    def __init__(self, image=bytes(640), port=0, *, base=0x0D80, fail_write_at=None):
        self.eeprom = bytearray(image)
        self.base = base                        # EEPROM address of self.eeprom[0]
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
            off = addr - self.base
            block = bytes(self.eeprom[off:off + length]) if 0 <= off < len(self.eeprom) else bytes(length)
            return bytes([ACK]) + encode(0x80, data[1:4] + block)
        if op == 0x17:
            addr = int.from_bytes(data[:3], "big") & 0xFFFF
            if addr == self.fail_write_at:
                return bytes([ACK]) + encode(0x85, data[:3])
            off = addr - self.base
            payload = data[3:]
            # Range-check like the real radio/bridge: a slice-assign past the end would grow the
            # image and a negative offset would write from the end, both silently corrupting it.
            if off < 0 or off + len(payload) > len(self.eeprom):
                return bytes([ACK]) + encode(0x85, data[:3])
            self.eeprom[off:off + len(payload)] = payload
            return bytes([ACK]) + encode(0x84, data[:3])
        return bytes([0x60])

    def _serve(self):
        while True:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            # one thread per connection, so a stale/retrying client (e.g. a VM serial with
            # auto-reconnect) can never wedge the server
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
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
                    try:
                        reply = self._answer(frame)
                    except Exception:
                        reply = bytes([NAK])            # bad/unexpected frame: NAK, never die
                    try:
                        conn.sendall(frame + reply)     # echo, then the reply
                    except OSError:
                        return


if __name__ == "__main__":
    image = open(sys.argv[1], "rb").read() if len(sys.argv) > 1 else bytes(640)
    base = 0x0D80 if len(image) == 640 else 0x0000   # 640 B = codeplug only; a larger dump starts at 0
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 2399
    radio = MockRadio(image, port, base=base)
    print(f"mock GM600 listening on 127.0.0.1:{radio.port} "
          f"(image {len(image)} B @ 0x{base:04X}; point DPS/a tool's SBEP_ADDR here)")
    radio.thread.join()
