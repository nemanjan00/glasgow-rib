#!/usr/bin/env python3
"""Serial SBEP bridge backed by the GM600 firmware emulator ("serial dumb emu").

The emulated radio (emu6811 running motofix.BIN) is the radio: it boots the real firmware
and, for an identity query (0x23), receives + validates the frame and BUILDS the authoritative
reply in its own RAM. A thin bridge then does the final byte-out that the firmware's RTOS TX
task would (that task doesn't get dispatched in the emulator — a cooperative-scheduler timing
race), so real SBEP tools / Motorola DPS / NPS can talk to the emulated radio over a socket.

Per SBEP opcode:
  0x23  feed to the VM, run until it builds the 0x8B reply in RAM, read it back out (the VM's
        own computed response: model 02 09, serial, CP version, …).
  0x13  transfer-capability constant 0x82 `00 20 00` (hardcoded in the firmware).
  0x11  read from the VM's live EEPROM image (what a read opcode returns anyway).
  0x17  apply to the VM's live EEPROM (so later reads see the write), answer 0x84.
  0x10  ACK.
Everything is echoed first (single-wire half-duplex), then ACK (0x50), then the reply frame,
exactly as a real GM600 answers (byte-for-byte compatible with mock_radio.py / a real capture).

usage: gm600_vm_bridge.py <motofix.BIN> <codeplug.bin> [PORT]   (default port 2399)
"""
import socket
import sys
import threading

sys.path.insert(0, __import__("os").path.dirname(__file__))
import emu6811
from sbep import ACK, NAK, REFUSED, encode, decode, frame_length

# reply-buffer layout the firmware fills for a 0x23 query (found by RE, stable per boot):
RB_OP, RB_LEN, RB_PAYLOAD = 0x1A75, 0x1A77, 0x1A7F
BOOT_INSNS = 6_000_000
REPLY_INSNS = 2_500_000          # budget to let the VM build a 0x23 reply


class VMRadio:
    """The emulated GM600 as an SBEP endpoint."""

    def __init__(self, flash_path, codeplug_path, base=0x0D80):
        flash = open(flash_path, "rb").read()
        self.base = base
        self.m = emu6811.Mem(flash, open(codeplug_path, "rb").read())
        self.c = emu6811.CPU(self.m)
        self.c.reset()
        self.c.run(max_insns=BOOT_INSNS, cop_timeout=150000)   # boot the firmware
        self.lock = threading.Lock()

    def _vm_query_23(self, frame, index):
        """Feed a 0x23 query and read back the reply the firmware builds in RAM."""
        m = self.m
        m.ram[RB_OP] = 0                                 # clear so we can detect a fresh build
        m.feed_rx(frame)
        # run until the firmware writes the 0x8B reply opcode into the buffer, or budget runs out
        ran = 0
        while ran < REPLY_INSNS:
            self.c.run(max_insns=50_000, cop_timeout=0); ran += 50_000
            if m.ram[RB_OP] == 0x8B and m.ram[RB_LEN]:
                break
        ln = m.ram[RB_LEN]
        op = m.ram[RB_OP]
        if op != 0x8B or not ln:
            return None
        payload = bytes(m.ram[RB_PAYLOAD:RB_PAYLOAD + ln])
        # sanity: first payload byte is the echoed query index
        if not payload or payload[0] != index:
            return None
        return encode(0x8B, payload)

    def answer(self, frame):
        op, data = decode(frame)
        with self.lock:
            if op == 0x10:
                return bytes([ACK])
            if op == 0x23:
                reply = self._vm_query_23(frame, data[0])
                return bytes([ACK]) + reply if reply else bytes([NAK])
            if op == 0x13:
                return bytes([ACK]) + encode(0x82, b"\x00\x20\x00")
            if op == 0x11:
                length, addr = data[0], int.from_bytes(data[1:4], "big") & 0xFFFF
                if length > 0x40:
                    return bytes([REFUSED])
                off = addr - self.base
                block = (bytes(self.m.eeprom[off:off + length])
                         if 0 <= off < len(self.m.eeprom) else bytes(length))
                return bytes([ACK]) + encode(0x80, data[1:4] + block)
            if op == 0x17:
                addr = int.from_bytes(data[:3], "big") & 0xFFFF
                off = addr - self.base
                self.m.eeprom[off:off + len(data) - 3] = data[3:]
                return bytes([ACK]) + encode(0x84, data[:3])
            return bytes([NAK])


def serve(radio, port):
    srv = socket.create_server(("127.0.0.1", port))
    real = srv.getsockname()[1]
    print(f"GM600 VM bridge listening on 127.0.0.1:{real} "
          f"(firmware-backed; point a tool's SBEP_ADDR / DPS COM here)", flush=True)
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=_handle, args=(radio, conn), daemon=True).start()


def _handle(radio, conn):
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
                frame = bytes(buf[:n]); del buf[:n]
                try:
                    reply = radio.answer(frame)
                except Exception:
                    reply = bytes([NAK])
                try:
                    conn.sendall(frame + reply)           # echo, then reply
                except OSError:
                    return


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr); sys.exit(2)
    port = int(sys.argv[3]) if len(sys.argv) > 3 else 2399
    radio = VMRadio(sys.argv[1], sys.argv[2])
    serve(radio, port)
