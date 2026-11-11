"""Shared SBEP helpers for the GM600 tools: framing, checksums, and a socket client.

Talks to the glasgow-rib applet's ``socket`` operation. The applet echoes every byte on the bus, so
each transaction is: our frame (echoed back), a status byte (ACK ``50``, NAK ``60``, or a bare
``80`` for a refused request), then an optional reply frame. See ``../PROTOCOL.md``.

The address defaults to ``127.0.0.1:2324``; set ``SBEP_ADDR=host:port`` to change it.
"""
import os
import socket
import time

ACK, NAK, REFUSED = 0x50, 0x60, 0x80


def checksum(frame_without_cks):
    """``0xFF − sum`` of the preceding frame bytes, so a whole frame sums to ``0xFF``."""
    return (0xFF - sum(frame_without_cks)) & 0xFF


def encode(op, data=b""):
    """Builds a frame; the reference codec from PROTOCOL.md. ``length`` = data bytes + checksum."""
    if op <= 0xE and not data:
        return bytes([op << 4])
    n = len(data) + 1
    lo = n if n <= 0xE else 0xF
    frame = bytearray()
    if op <= 0xE:
        frame.append(op << 4 | lo)
    else:
        frame += bytes([0xF0 | lo, op])
    if lo == 0xF:
        frame += n.to_bytes(2, "big")
    frame += data
    frame.append(checksum(frame))
    return bytes(frame)


def frame_length(buf):
    """Length of the frame starting at ``buf[0]``, or None if more header bytes are needed."""
    if not buf:
        return None
    hi, lo = buf[0] >> 4, buf[0] & 0xF
    pos = 2 if hi == 0xF else 1
    if lo == 0xF:
        if len(buf) < pos + 2:
            return None
        return pos + 2 + int.from_bytes(buf[pos:pos + 2], "big")
    return pos + lo


def decode(frame):
    """Returns ``(op, data)`` for a whole frame. Raises ValueError on a bad checksum."""
    hi, lo = frame[0] >> 4, frame[0] & 0xF
    pos = 1
    if hi == 0xF:
        op = frame[1]
        pos = 2
    else:
        op = hi
    if lo == 0xF:
        pos += 2
    if len(frame) == pos:                 # bare status byte such as ACK
        return op, b""
    if sum(frame) & 0xFF != 0xFF:
        raise ValueError(f"bad checksum: {frame.hex(' ')}")
    return op, bytes(frame[pos:-1])


class SBEPError(IOError):
    pass


class Radio:
    """A connection to the applet socket. One client at a time: close it when done."""

    def __init__(self, addr=None):
        host, _, port = (addr or os.environ.get("SBEP_ADDR", "127.0.0.1:2324")).rpartition(":")
        self.sock = socket.create_connection((host, int(port)), timeout=5)
        self.buf = bytearray()

    @staticmethod
    def default_timeout(frame):
        """Seconds allowed for each phase (echo, then status and reply): DPS's write-result
        timeout, ``len × 12 + 1000`` ms, which also covers the echo of the frame itself."""
        return 1.0 + 0.012 * len(frame)

    def close(self):
        self.sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def drain(self):
        """Discards anything already received (stale echoes, other traffic)."""
        self.buf.clear()
        self.sock.settimeout(0.05)
        try:
            while self.sock.recv(4096):
                pass
        except socket.timeout:
            pass

    def _need(self, n, deadline):
        while len(self.buf) < n:
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            self.sock.settimeout(left)
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                return False
            if not chunk:
                raise SBEPError("applet closed the connection")
            self.buf += chunk
        return True

    def _take(self, n):
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def transact(self, frame, *, reply=True, timeout=None):
        """Sends one frame and checks its echo. Returns ``(status, op, data)``: the status byte,
        and the reply frame's opcode and data (``None, None`` if no reply is expected or the
        status isn't an ACK). Raises SBEPError on a wrong or missing echo, or no status byte.

        ``timeout`` (seconds) applies separately to the echo and to the status plus reply;
        the default is :meth:`default_timeout`."""
        if timeout is None:
            timeout = self.default_timeout(frame)
        self.drain()
        self.sock.sendall(frame)
        deadline = time.monotonic() + timeout
        if not self._need(len(frame), deadline):
            raise SBEPError(f"short echo for {frame.hex(' ')}: {bytes(self.buf).hex(' ')}")
        echo = self._take(len(frame))
        if echo != frame:
            raise SBEPError(f"echo mismatch (collision?): sent {frame.hex(' ')}, got {echo.hex(' ')}")
        deadline = time.monotonic() + timeout
        if not self._need(1, deadline):
            raise SBEPError(f"no status byte after {frame.hex(' ')}")
        status = self._take(1)[0]
        if status != ACK or not reply:
            return status, None, None
        while True:
            n = frame_length(self.buf)
            if n is not None and self._need(n, deadline):
                break
            if not self._need(len(self.buf) + 1, deadline):
                raise SBEPError(f"no reply frame after {frame.hex(' ')}: {bytes(self.buf).hex(' ')}")
        op, data = decode(self._take(n))
        return status, op, data

    def read_block(self, addr, length):
        """``11`` read of up to 64 bytes. Returns the data, checking the echoed address."""
        status, op, data = self.transact(encode(0x11, bytes([length]) + addr.to_bytes(3, "big")))
        if status != ACK or op != 0x80:
            raise SBEPError(f"read @0x{addr:04X}: status {status:02X}, op {op}")
        if data[:3] != addr.to_bytes(3, "big") or len(data) != 3 + length:
            raise SBEPError(f"read @0x{addr:04X}: unexpected reply {data.hex(' ')}")
        return data[3:]

    def id_string(self, index):
        """``23`` ID query. Returns the string (without its index byte and any NUL)."""
        status, op, data = self.transact(encode(0x23, bytes([index])))
        if status != ACK or op != 0x8B:
            raise SBEPError(f"ID {index}: status {status:02X}, op {op}")
        return data[1:].rstrip(b"\0").decode("ascii", "replace")


def launch_stub(radio, org, code):
    """Re-read a RAM stub immediately before jumping to it with the ``26`` backdoor.

    The firmware keeps running between an upload and the launch, so a verify done at upload time
    can be stale by the time ``26`` jumps in. Raises SBEPError on any difference. Returns the
    ``26`` frame it sent (the caller reads the echo and launch ACK)."""
    back = bytearray()
    for off in range(0, len(code), 64):
        back += radio.read_block(org + off, min(64, len(code) - off))
    if bytes(back) != bytes(code):
        raise SBEPError(f"stub at 0x{org:04X} changed before launch - not jumping into it")
    frame = encode(0x26, org.to_bytes(2, "big"))
    radio.drain()
    radio.sock.sendall(frame)
    return frame
