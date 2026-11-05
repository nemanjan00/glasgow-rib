"""Tests for the GM600 tools against mock_radio.py (no hardware needed).

Run from this folder: python3 -m unittest test_tools
or from the repo root: python3 -m unittest discover -s gm600/tools
"""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mock_radio import MockRadio
from sbep import Radio, SBEPError, encode, decode, frame_length

HERE = os.path.dirname(os.path.abspath(__file__))
IMAGE = bytes((i * 7 + 3) & 0xFF for i in range(640))


def run_tool(radio, *args):
    env = dict(os.environ, SBEP_ADDR=f"127.0.0.1:{radio.port}")
    return subprocess.run([sys.executable, *args], cwd=HERE, env=env,
                          capture_output=True, text=True, timeout=60)


def write_file(path, text):
    with open(path, "w") as f:
        f.write(text)


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def capture_log(frames):
    """A socat -v style log of the host sending ``frames``."""
    lines = []
    for f in frames:
        lines.append(f"> 2026/11/03 21:01:27.952106  length={len(f)} from=0 to={len(f) - 1}")
        lines.append(" " + " ".join(f"{b:02x}" for b in f))
    return "\n".join(lines) + "\n"


class CodecTest(unittest.TestCase):
    def test_reference_vectors(self):
        self.assertEqual(encode(0x10), bytes.fromhex("F1 10 FE"))
        self.assertEqual(encode(0x23, b"\x01"), bytes.fromhex("F2 23 01 E9"))
        self.assertEqual(encode(0x11, bytes([0x20, 0, 0, 0])), bytes.fromhex("F5 11 20 00 00 00 D9"))
        self.assertEqual(encode(0x05), b"\x50")

    def test_long_frame_roundtrip(self):
        frame = encode(0x80, bytes(35))
        self.assertEqual(frame[:4], bytes.fromhex("FF 80 00 24"))
        self.assertEqual(frame_length(frame), len(frame))
        self.assertEqual(decode(frame), (0x80, bytes(35)))

    def test_bad_checksum(self):
        with self.assertRaises(ValueError):
            decode(bytes.fromhex("F2 23 01 E8"))


class ToolsTest(unittest.TestCase):
    def setUp(self):
        self.radio = MockRadio(IMAGE)

    def tearDown(self):
        self.radio.close()

    def test_radio_helpers(self):
        with Radio(f"127.0.0.1:{self.radio.port}") as r:
            self.assertEqual(r.id_string(1), "807IYA0903")
            self.assertEqual(r.read_block(0x0D80, 0x20), IMAGE[:0x20])
            status, op, data = r.transact(encode(0x11, bytes([0x80, 0, 0x0D, 0x80])))
            self.assertEqual((status, op), (0x80, None))

    def test_dump_codeplug(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "dump.bin")
            res = run_tool(self.radio, "dump_codeplug.py", out)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertEqual(read_bytes(out), IMAGE)

    def test_sbep_client(self):
        res = run_tool(self.radio, "sbep_client.py")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("807IYA0903", res.stdout)

    def _write_stream(self):
        """A DPS-like write session: queries, two 17 blocks, then 10."""
        return [encode(0x23, b"\x01"), encode(0x13),
                encode(0x17, bytes.fromhex("00 0E 13") + bytes(32)),
                encode(0x17, bytes.fromhex("00 0E 33") + bytes(32)),
                encode(0x10)]

    def test_sbep_write_restore(self):
        with tempfile.TemporaryDirectory() as d:
            log = os.path.join(d, "sniff-write.log")
            write_file(log, capture_log(self._write_stream()))
            res = run_tool(self.radio, "sbep_write.py", "--restore", log)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertIn("2 writes OK", res.stdout)
            self.assertEqual(self.radio.eeprom[0x93:0xD3], bytes(64))

    def test_sbep_write_aborts_on_failed_block(self):
        self.radio.fail_write_at = 0x0E13
        with tempfile.TemporaryDirectory() as d:
            log = os.path.join(d, "sniff-write.log")
            write_file(log, capture_log(self._write_stream()))
            res = run_tool(self.radio, "sbep_write.py", "--restore", log)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("write failed", res.stdout + res.stderr)
        self.assertIn("--restore", res.stdout + res.stderr)
        # Nothing after the failed block was sent.
        self.assertEqual([decode(f)[0] for f in self.radio.frames], [0x23, 0x13, 0x17])

    def test_sbep_write_refuses_partial_patch(self):
        # The stream doesn't cover the probe addresses: --write must not send anything.
        with tempfile.TemporaryDirectory() as d:
            log = os.path.join(d, "sniff-write.log")
            write_file(log, capture_log(self._write_stream()))
            res = run_tool(self.radio, "sbep_write.py", "--write", log)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("nothing sent", res.stderr)
        self.assertEqual(self.radio.frames, [])

    def test_sbep_write_rejects_unknown_probe_set(self):
        env = dict(os.environ, PROBE_SET="no-such-set")
        res = subprocess.run([sys.executable, "sbep_write.py", "--dry", "/dev/null"], cwd=HERE,
                             env=env, capture_output=True, text=True, timeout=60)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("unknown PROBE_SET", res.stderr)


class DPSReadTest(unittest.TestCase):
    """The mock answers Motorola DPS's "Read Radio" exactly as a real GM600 did.

    The expected bytes below are from a socat capture of DPS reading a real radio (2026-11-03): the
    model (`23 00`) and CP-version (`23 04`) queries, the max-block query (`13`), then 20 reads of
    32 B covering the whole codeplug 0x0D80..0x1000. If these match, DPS accepts the mock as a radio.
    """

    def setUp(self):
        self.radio = MockRadio(IMAGE)

    def tearDown(self):
        self.radio.close()

    def test_handshake_bytes_match_real_radio(self):
        # answer = ACK (50) + reply frame; compare to the captured radio bytes
        self.assertEqual(self.radio._answer(encode(0x23, b"\x00")),
                         bytes.fromhex("50 f4 8b 00 02 09 75"))             # model 0x0209
        self.assertEqual(self.radio._answer(encode(0x23, b"\x04")),
                         bytes.fromhex("50 f8 8b 04 30 32 2e 30 33 00 85"))  # CP version "02.03"
        self.assertEqual(self.radio._answer(encode(0x13)),
                         bytes.fromhex("50 f4 82 00 20 00 69"))             # max block = 0x20

    def test_full_read_reconstructs_image(self):
        out = bytearray()
        for k in range(20):                                # DPS reads 0x0D80..0x1000 in 32 B steps
            addr = 0x0D80 + 0x20 * k
            reply = self.radio._answer(encode(0x11, bytes([0x20]) + addr.to_bytes(3, "big")))
            self.assertEqual(reply[0], 0x50)                               # ACK first
            self.assertEqual(reply[1:5], bytes.fromhex("ff 80 00 24"))      # long read-reply header
            op, data = decode(reply[1:])                                    # also verifies checksum
            self.assertEqual(op, 0x80)
            self.assertEqual(data[:3], addr.to_bytes(3, "big"))            # echoed address
            out += data[3:]
        self.assertEqual(bytes(out), IMAGE)


if __name__ == "__main__":
    unittest.main()
