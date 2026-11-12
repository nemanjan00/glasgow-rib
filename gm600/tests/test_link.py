"""GM600 tests: link."""
from _common import *  # noqa: F401,F403


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


class MockRadioWriteRangeTest(unittest.TestCase):
    """mock_radio's 0x17 must reject out-of-range writes like the real radio/bridge."""

    def setUp(self):
        self.radio = MockRadio(bytes(640))

    def tearDown(self):
        self.radio.close()

    def test_write_above_eeprom_fails_without_growing(self):
        with Radio(f"127.0.0.1:{self.radio.port}") as r:
            st, op, _ = r.transact(encode(0x17, (0x2000).to_bytes(3, "big") + b"\x01"))
        self.assertEqual(op, 0x85)                 # write-fail result
        self.assertEqual(len(self.radio.eeprom), 640)

    def test_write_below_base_fails_without_wrapping(self):
        with Radio(f"127.0.0.1:{self.radio.port}") as r:
            st, op, _ = r.transact(encode(0x17, (0x0000).to_bytes(3, "big") + b"\x01"))
        self.assertEqual(op, 0x85)
        self.assertNotEqual(self.radio.eeprom[-1], 0x01)


class SplitFramesTest(unittest.TestCase):
    """sbep_write.split_frames must not crash on a capture that ends mid-frame."""

    def test_partial_trailing_header_is_dropped(self):
        import sbep_write
        # a complete 0x10 frame, then a truncated long-frame header (0xFF 0x17 with no length)
        frames = sbep_write.split_frames(bytes.fromhex("F110FEFF17"))
        self.assertEqual(frames, [bytes.fromhex("F110FE")])

    def test_partial_short_frame_is_dropped(self):
        import sbep_write
        # long read reply header claiming 4 data bytes but only 1 present
        self.assertEqual(sbep_write.split_frames(bytes.fromhex("F5")), [])


if __name__ == "__main__":
    unittest.main()
