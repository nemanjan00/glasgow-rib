"""GM600 tests: codeplug."""
from _common import *  # noqa: F401,F403


class CodeplugChecksumTest(unittest.TestCase):
    """The five additive per-block checksums (gm600_codeplug.py)."""

    @staticmethod
    def _valid():
        img = bytearray((i * 7 + 3) & 0xFF for i in range(640))
        fix_checksums(img)
        return img

    def test_reference_image_verifies(self):
        self.assertEqual(verify_checksums(self._valid()), [])

    def test_flipped_byte_fails_only_its_block(self):
        img = self._valid()
        img[0x0E20 - RADIO_BASE] ^= 0xFF              # a byte in the Network block (0x0E13-0x0F06)
        self.assertEqual([ca for ca, _, _ in verify_checksums(img)], [0x0F07])

    def test_fix_repairs_a_broken_block(self):
        img = self._valid()
        img[0x0E20 - RADIO_BASE] ^= 0xFF
        fix_checksums(img)
        self.assertEqual(verify_checksums(img), [])

    def test_tail_bytes_belong_to_no_block(self):
        # The last checksum byte is 0x0FF4; 0x0FF5-0x0FFF falls outside every block's data+checksum.
        self.assertEqual(CHECKSUM_ADDRS[-1], 0x0FF4)
        covered = set()
        for ds, de, ca in blocks():
            covered.update(range(ds, de))
            covered.add(ca)
        self.assertTrue(covered.isdisjoint(range(0x0FF5, 0x1000)))


class SbepWriteChecksumGuardTest(unittest.TestCase):
    """sbep_write's pre-write guard: refuse/repair a write that breaks a block checksum."""

    def setUp(self):
        self.img = CodeplugChecksumTest._valid()
        self.radio = MockRadio(bytes(self.img))

    def tearDown(self):
        self.radio.close()

    def _conn(self):
        return Radio(f"127.0.0.1:{self.radio.port}")

    def _write(self, addr, byte):
        return encode(0x17, addr.to_bytes(3, "big") + bytes([byte]))

    def test_guard_passes_a_sum_neutral_write(self):
        import sbep_write
        frames = [self._write(0x0E20, (self.img[0x0E20 - RADIO_BASE] + 1) & 0xFF),
                  self._write(0x0E21, (self.img[0x0E21 - RADIO_BASE] - 1) & 0xFF)]
        with self._conn() as r:
            out = sbep_write.checksum_guard(r, frames, fix=False, force=False)
        self.assertEqual(out, frames)

    def test_guard_refuses_a_breaking_write(self):
        import sbep_write
        frames = [self._write(0x0E20, self.img[0x0E20 - RADIO_BASE] ^ 0xFF)]
        with self._conn() as r, self.assertRaises(SystemExit) as cm:
            sbep_write.checksum_guard(r, frames, fix=False, force=False)
        self.assertIn("0x0F07", str(cm.exception))

    def test_guard_fix_appends_corrections(self):
        import sbep_write
        frames = [self._write(0x0E20, self.img[0x0E20 - RADIO_BASE] ^ 0xFF)]
        with self._conn() as r:
            out = sbep_write.checksum_guard(r, frames, fix=True, force=False)
            self.assertGreater(len(out), len(frames))
            img2 = sbep_write.read_image(r)
        sbep_write.apply_writes(img2, out)
        self.assertEqual(verify_checksums(img2), [])


class CodeplugSummaryTest(unittest.TestCase):
    """gm600_codeplug.summary decodes the documented reference-radio fields (FREQ_ENCODING.md)."""

    def test_reference_channels(self):
        import gm600_codeplug as g
        cp = bytearray(640)
        base = 0x0D80
        cp[0x0F0B - base:0x0F0D - base] = (322).to_bytes(2, "big")
        recs = ["AC6BAC6B001308", "AC75AC75000008", "AC6FAC6F000008", "31B52BC5130008"]
        for n, h in enumerate(recs):
            cp[0x0F67 - base + 7 * n:0x0F67 - base + 7 * (n + 1)] = bytes.fromhex(h)
        g.fix_checksums(cp)
        s = g.summary(bytes(cp))
        self.assertEqual(s["own_unit"], 322)
        self.assertEqual(s["checksums"], "all valid")
        self.assertEqual([c["rx_mhz"] for c in s["channels"]], [446.06875, 446.13125, 446.09375, 438.625])
        self.assertEqual(s["channels"][3]["tx_mhz"], 431.025)
        self.assertEqual(s["channels"][0]["rx_pl"], "123.0 Hz (3Z)")
        self.assertEqual(s["channels"][3]["tx_pl"], "123.0 Hz (3Z)")


class ApplyTest(unittest.TestCase):
    """gm600py.codeplug.apply and the tools built on it (setpl, setunit), against the mock radio."""

    def setUp(self):
        import gm600py.codeplug as cp
        self.cp = cp
        img = bytearray(IMAGE)
        cp.fix_checksums(img)
        self.img = bytes(img)
        self.radio = MockRadio(self.img)
        self.addr = f"127.0.0.1:{self.radio.port}"
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        self.radio.close()

    def writes(self):
        out = []
        for f in self.radio.frames:
            op, data = decode(f)
            if op == 0x17:
                out.append(int.from_bytes(data[:3], "big"))
        return out

    def test_setpl_changes_one_byte_fixes_checksum_writes_only_changed_chunks(self):
        from gm600py.radio.setpl import set_pl
        with Radio(self.addr) as r:
            backup, chunks, ch = set_pl(r, 4, "rx", "123.0", backup_dir=self.tmp)
        ee = bytes(self.radio.eeprom)
        diff = [0x0D80 + i for i in range(640) if ee[i] != self.img[i]]
        self.assertEqual(ee[0x0F81 - 0x0D80], 19)                     # 123.0 Hz (3Z)
        self.assertEqual(self.cp.verify_checksums(ee), [])
        self.assertEqual(set(diff) - {0x0F81, 0x0FF4}, set())         # the PL byte + its checksum only
        self.assertEqual(self.writes(), [0x0F80, 0x0FE0])             # only the two changed chunks
        self.assertEqual(chunks, [0x0F80, 0x0FE0])
        self.assertEqual(ch["rx_pl"], "123.0 Hz (3Z)")
        self.assertEqual(read_bytes(backup), self.img)                # backup = the pre-write image
        self.assertEqual(decode(self.radio.frames[-1])[0], 0x10)      # reset at the end

    def test_setunit(self):
        from gm600py.radio.setunit import set_unit
        with Radio(self.addr) as r:
            set_unit(r, 456, backup_dir=self.tmp)
        ee = bytes(self.radio.eeprom)
        self.assertEqual(int.from_bytes(ee[0x0F0B - 0x0D80:0x0F0D - 0x0D80], "big"), 456)
        self.assertEqual(self.cp.verify_checksums(ee), [])
        self.assertEqual(self.writes(), [0x0F00, 0x0FE0])

    def test_refuses_a_radio_with_bad_checksums(self):
        bad = bytearray(self.img)
        bad[0x0FF4 - 0x0D80] ^= 0xFF
        self.radio.eeprom[:] = bad
        with Radio(self.addr) as r:
            with self.assertRaises(self.cp.ApplyError):
                self.cp.apply(r, bytes(self.img), backup_dir=self.tmp)
        self.assertEqual(self.writes(), [])

    def test_no_change_writes_nothing_and_does_not_reset(self):
        with Radio(self.addr) as r:
            _, chunks = self.cp.apply(r, self.img, backup_dir=self.tmp)
        self.assertEqual(chunks, [])
        self.assertEqual(self.writes(), [])
        self.assertNotIn(0x10, [decode(f)[0] for f in self.radio.frames])

    def test_cli_argument_errors_are_clean(self):
        for args in (["setpl.py", "x", "rx", "1"], ["setpl.py", "4", "rx", "abc"], ["setpl.py", "9", "rx", "1"],
                     ["setpl.py", "4", "up", "1"], ["setunit.py", "abc"]):
            res = run_tool(self.radio, *args)
            self.assertNotEqual(res.returncode, 0, args)
            self.assertNotIn("Traceback", res.stderr, args)
            self.assertIn("usage", res.stderr, args)
        self.assertEqual(self.writes(), [])


if __name__ == "__main__":
    unittest.main()
