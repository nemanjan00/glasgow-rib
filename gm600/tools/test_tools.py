"""Tests for the GM600 tools against mock_radio.py (no hardware needed).

Run from this folder: python3 -m unittest test_tools
or from the repo root: python3 -m unittest discover -s gm600/tools
"""
import os
import subprocess
import sys
import tempfile
import threading
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mock_radio import MockRadio
from sbep import Radio, SBEPError, encode, decode, frame_length
from gm600_codeplug import verify_checksums, fix_checksums, blocks, CHECKSUM_ADDRS, RADIO_BASE

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


class RamdumpAssemblerTest(unittest.TestCase):
    """asm6811 must reproduce the hardware-verified dumper stub from ramdump.S."""

    # Verified in-circuit: this stub dumped all 256 KB byte-identical to the off-board T48 read.
    PROVEN = bytes.fromhex(
        "0fc600f71058ce40008655b7103a86aab7103ab61074848027f9a600b71077088c800026e4"
        "cb04c13826d8ce80008655b7103a86aab7103ab61074848027f9a600b710770826e720fe")

    def test_ramdump_S_assembles_to_verified_bytes(self):
        import ramdump
        org, code = ramdump.build_stub()
        self.assertEqual(org, 0x2000)
        self.assertEqual(code, self.PROVEN)

    def test_modes_and_relative_branch(self):
        import asm6811
        a = asm6811.assemble(
            "        .org $100\n"
            "MASK    = $80\n"
            "loop:   ldaa #MASK\n"       # IMM8 via equate -> 86 80
            "        staa $1077\n"       # EXT            -> B7 10 77
            "        bne loop\n")        # REL back to $100 from $105 -> -7 (F9)
        self.assertEqual(a.code, bytes.fromhex("8680b7107726f9"))

    def test_branch_out_of_range_rejected(self):
        import asm6811
        with self.assertRaises(asm6811.AsmError):
            asm6811.assemble("        .org 0\n        bra $200\n")


class LcdTextTest(unittest.TestCase):
    """lcd_text.S/.py: the hardware-verified 7-seg stub and its font."""

    # Verified on the radio: pushed 322 over a 686 codeplug, then HI/HEL/LO/AbC/PSU/147/-9-.
    PROVEN = bytes.fromhex(
        "0f8e21ffbd2037c1a526f9bd2037f72100bd2037f72101bd2037f72102bd2037f72103bd2037"
        "f72104ce21003c3cc605bd8f1f383820cdbd2045b61074842027f6f61077398655b7103a86aa"
        "b7103a39")

    def test_stub_assembles_to_verified_bytes(self):
        import asm6811
        a = asm6811.assemble_file(os.path.join(os.path.dirname(__file__), "lcd_text.S"))
        self.assertEqual(a.org, 0x2000)
        self.assertEqual(a.code, self.PROVEN)

    def test_font_matches_firmware_digit_cache(self):
        # 0x3497 on the radio: "322" = e6 76 76, "686" = f3 f7 f3.
        import lcd_text
        self.assertEqual(lcd_text.text_to_segs("322"), bytes.fromhex("e67676"))
        self.assertEqual(lcd_text.text_to_segs("686"), bytes.fromhex("f3f7f3"))

    def test_pack_matches_firmware_entry5(self):
        # Run the firmware's own 0x9018 in the emulator on random structs and compare its SPI bytes
        # (5 zeros + the packed frame) with the Python port.
        import random
        import emu6811
        import lcd_text
        img = os.environ.get("GM600_FW")
        if not img:
            self.skipTest("set GM600_FW=/path/to/motofix.BIN (a flash dump) to run this")
        m = emu6811.Mem(flash=open(img, "rb").read())
        c = emu6811.CPU(m)
        rng = random.Random(3)
        for _ in range(300):
            s = bytes(rng.randrange(256) for _ in range(5))
            m.spi_out = bytearray()
            for i, v in enumerate(s):
                m.wb(0x2100 + i, v)
            c.SP = 0x21FF
            c.pushw(0x2100); c.pushw(0); c.pushw(0x3000); c.pushw(0x3000)
            c.PC = 0x9018
            self.assertEqual(c.run(max_insns=5000, stop_pc=0x3000), "stop")
            self.assertEqual(bytes(m.spi_out), bytes(5) + lcd_text.pack(s), s.hex())


class EmulatorTest(unittest.TestCase):
    """The 68HC11 core must execute correctly (validated on a known program)."""

    def test_cpu_core_selftest(self):
        import emu6811
        self.assertTrue(emu6811.run_selftest())

    def test_mul_and_idiv(self):
        import emu6811
        m = emu6811.Mem()
        # ldd #1000 ; ldx #7 ; idiv -> X=142 rem D=6
        prog = bytes([0xCC, 0x03, 0xE8, 0xCE, 0x00, 0x07, 0x02, 0x20, 0xFE])
        m.ram[0x200:0x200 + len(prog)] = prog
        c = emu6811.CPU(m); c.PC = 0x200
        c.run(stop_pc=0x200 + len(prog) - 2)
        self.assertEqual(c.X, 1000 // 7)
        self.assertEqual(c.D, 1000 % 7)


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


class VmBridgeWriteRangeTest(unittest.TestCase):
    """gm600_vm_bridge's 0x17 must reject writes outside the 640-byte EEPROM (no grow, no wrap)."""

    @staticmethod
    def _bridge():
        import gm600_vm_bridge as vb
        r = vb.VMRadio.__new__(vb.VMRadio)            # skip the (slow) firmware boot; only test 0x17
        r.base = 0x0D80
        r.lock = threading.Lock()
        r.m = types.SimpleNamespace(eeprom=bytearray(640))
        return r

    def test_in_range_write_ok(self):
        r = self._bridge()
        reply = r.answer(encode(0x17, (0x0D80).to_bytes(3, "big") + b"\xAA\xBB"))
        self.assertEqual(r.m.eeprom[:2], b"\xAA\xBB")
        self.assertEqual(len(r.m.eeprom), 640)
        self.assertEqual(decode(reply[1:])[0], 0x84)

    def test_high_write_does_not_grow_image(self):
        r = self._bridge()
        reply = r.answer(encode(0x17, (0x2000).to_bytes(3, "big") + b"\x01"))   # ramdump stub addr
        self.assertEqual(len(r.m.eeprom), 640)
        self.assertEqual(decode(reply[1:])[0], 0x85)

    def test_low_write_does_not_wrap(self):
        r = self._bridge()
        reply = r.answer(encode(0x17, (0x0000).to_bytes(3, "big") + b"\x01"))
        self.assertEqual(len(r.m.eeprom), 640)
        self.assertNotEqual(r.m.eeprom[-1], 0x01)      # a negative offset must not write from the end
        self.assertEqual(decode(reply[1:])[0], 0x85)


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


class KeypadPatchTest(unittest.TestCase):
    """The read_adc override patch: image edits are correct and reversible."""

    @staticmethod
    def _image():
        import gm600_keypad_patch as kp
        f = bytearray(0x40000)
        f[kp.PATCH_SITE:kp.PATCH_SITE + 3] = kp.PATCH_ORIG     # ldab ADR1 at the site
        f[kp.STUB_SITE:kp.STUB_SITE + len(kp.STUB)] = b"\xFF" * len(kp.STUB)
        return bytes(f)

    def test_patch_applies_both_sites(self):
        import gm600_keypad_patch as kp
        out = kp.patch_image(self._image())
        self.assertEqual(out[kp.PATCH_SITE:kp.PATCH_SITE + 3], kp.PATCH_NEW)
        self.assertEqual(out[kp.STUB_SITE:kp.STUB_SITE + len(kp.STUB)], kp.STUB)

    def test_patch_rejects_wrong_image(self):
        import gm600_keypad_patch as kp
        bad = bytearray(self._image())
        bad[kp.PATCH_SITE] ^= 0xFF                              # not `ldab ADR1` anymore
        with self.assertRaises(ValueError):
            kp.patch_image(bytes(bad))

    def test_patch_diff_round_trips(self):
        import gm600_keypad_patch as kp
        img = self._image()
        patched = kp.patch_image(img)
        # reverting (swap original/patched in each diff tuple) restores the original image
        rev = bytearray(patched)
        for off, orig, new in kp.patch_diff():
            self.assertEqual(bytes(rev[off:off + len(new)]), new)
            rev[off:off + len(new)] = orig
        self.assertEqual(bytes(rev), img)


class FlashToolTest(unittest.TestCase):
    """flashtool: the resident AT29C020 stub assembles, and the address mapping is right."""

    def test_stub_assembles_deterministically(self):
        import flashtool
        org, code = flashtool.build_stub()
        self.assertEqual(org, 0x2000)
        self.assertGreater(len(code), 0)
        org2, code2 = flashtool.build_stub()
        self.assertEqual(code, code2)                 # deterministic

    def test_device_address_to_window_mapping(self):
        import flashtool
        self.assertEqual(flashtool._win(0x1000), (0, 0x50, 0x00))   # read_adc patch sector
        self.assertEqual(flashtool._win(0x1E00), (0, 0x5E, 0x00))   # stub slot sector
        self.assertEqual(flashtool._win(0x5555), (1, 0x55, 0x55))   # command address
        self.assertEqual(flashtool._win(0x2AAA), (0, 0x6A, 0xAA))   # command address
        self.assertEqual(flashtool._win(0x3FFF2), (15, 0x7F, 0xF2)) # upper lockout byte


class VmFlashWriteModelTest(unittest.TestCase):
    """emu6811's AT29C020 write model: gated by 12V+PA6, product-ID, sector program."""

    @staticmethod
    def _mem():
        import emu6811
        flash = bytes((i * 7 + 3) & 0xFF for i in range(0x40000))
        m = emu6811.Mem(flash, b"\xff" * 0x280)
        m.reg[0x58] = 0                                  # bank window = page 0
        return m, flash

    def _id_entry(self, m):
        m.wb(0xD555, 0xAA); m.wb(0xAAAA, 0x55); m.wb(0xD555, 0x90)   # AA/55/90 via CSPROG region

    def test_commands_ignored_without_12v(self):
        m, flash = self._mem()
        m.pin12_12v = False; m.reg[0] = 0x40            # PA6 high but no 12V on pin-12
        self._id_entry(m)
        self.assertEqual(m.rb(0x8000), flash[0x38000])  # plain array — ID never engaged
        self.assertNotEqual(m.rb(0x8000), 0x1F)

    def test_commands_ignored_without_pa6(self):
        m, flash = self._mem()
        m.pin12_12v = True; m.reg[0] = 0x00             # 12V but PA6 low (/OE low = read mode)
        self._id_entry(m)
        self.assertEqual(m.rb(0x8000), flash[0x38000])

    def test_product_id_when_unlocked(self):
        m, _ = self._mem()
        m.pin12_12v = True; m.reg[0] = 0x40
        self._id_entry(m)
        self.assertEqual(m.rb(0x8000), 0x1F)            # manufacturer
        self.assertEqual(m.rb(0x8001), 0xDA)            # device
        self.assertEqual(m.rb(0x4002), 0xFE)            # lower boot-block lockout (window chip 0x0002)
        m.wb(0xD555, 0xAA); m.wb(0xAAAA, 0x55); m.wb(0xD555, 0xF0)   # exit
        self.assertNotEqual(m.rb(0x8000), 0x1F)

    def test_sector_program_when_unlocked(self):
        m, _ = self._mem()
        m.pin12_12v = True; m.reg[0] = 0x40
        m.wb(0xD555, 0xAA); m.wb(0xAAAA, 0x55); m.wb(0xD555, 0xA0)   # SDP program prefix
        pat = bytes((i ^ 0x3C) & 0xFF for i in range(256))
        for i in range(256):
            m.wb(0x5000 + i, pat[i])                    # load sector 0x1000 via the window
        self.assertEqual(m.rb(0x5000), pat[0])          # first read commits the program
        self.assertEqual(bytes(m.flash[0x1000:0x1100]), pat)

    def test_program_ignored_without_12v(self):
        m, flash = self._mem()
        m.pin12_12v = False; m.reg[0] = 0x40
        m.wb(0xD555, 0xAA); m.wb(0xAAAA, 0x55); m.wb(0xD555, 0xA0)
        for i in range(256):
            m.wb(0x5000 + i, 0xAA)
        self.assertEqual(bytes(m.flash[0x1000:0x1100]), flash[0x1000:0x1100])


class FlashStubIntegrationTest(unittest.TestCase):
    """Run the flashtool stub in the VM against the modelled flash: probe, apply, whitelist."""

    def setUp(self):
        import emu6811, asm6811
        import os
        fw = os.environ.get("GM600_FW")
        if not fw or not os.path.exists(fw):
            self.skipTest("set GM600_FW=/path/to/motofix.BIN (a flash dump) to run this")
        self.flash = open(fw, "rb").read()
        self.stub = asm6811.assemble_file(os.path.join(HERE, "flashtool.S")).code
        self.m, self.c = self._boot(emu6811)

    def _boot(self, emu6811):
        cp = b"\xff" * 0x280
        m = emu6811.Mem(self.flash, cp)
        c = emu6811.CPU(m)
        c.reset(); c.run(max_insns=6_000_000, cop_timeout=150000)
        m.ram[0x2000:0x2000 + len(self.stub)] = self.stub
        c.PC = 0x2000; c.sf(emu6811.I, True); c.SP = 0x01FF
        m.pin12_12v = True
        return m, c

    def _cmd(self, payload, n, budget=3_000_000):
        self.m.sci_out.clear(); self.m.feed_rx(bytes(payload))
        self.c.run(max_insns=budget, cop_timeout=0)
        return bytes(self.m.sci_out[:n])

    def test_nonzero_page_bank_encoding(self):
        # Regression for the mis-banked write that corrupted the real radio: the stub must encode
        # BANK = page<<2 (MM1CR), not the raw page. With the raw-page bug, "page 13" set BANK=0x0D ->
        # page 3 -> device 0xC000 (live firmware). Page 0 (all the keypad-patch sectors) is immune
        # because 0<<2 == 0, which is why the old test never caught it. Exercise a non-zero page.
        c000_before = bytes(self.m.flash[0xC000:0xC100])
        pat = bytes((i ^ 0x5A) & 0xFF for i in range(256))
        p, wh, wl = 13, 0x40, 0x00                                   # device 0x34000 (erased in golden)
        self.assertEqual(self._cmd([ord("W"), p, wh, wl] + list(pat), 1), b"\x84")
        self.assertEqual(self._cmd([ord("R"), p, wh, wl], 256), pat)
        self.assertEqual(bytes(self.m.flash[0x34000:0x34100]), pat)          # landed at the RIGHT bank
        self.assertEqual(bytes(self.m.flash[0xC000:0xC100]), c000_before)    # page 3 NOT touched
        # page 3 (0xC000) is LIVE boot code and must be REFUSED (0x85), leaving the flash untouched
        c000 = bytes(self.m.flash[0xC000:0xC100])
        p, wh, wl = 3, 0x40, 0x00                                    # device 0xC000
        self.assertEqual(self._cmd([ord("W"), p, wh, wl] + [0xAA] * 256, 1), b"\x85")
        self.assertEqual(bytes(self.m.flash[0xC000:0xC100]), c000)

    def test_probe_and_apply_and_whitelist(self):
        import gm600_keypad_patch as kp
        from collections import defaultdict
        self.assertEqual(self._cmd(b"I", 4, 500_000), bytes([0x1F, 0xDA, 0xFE, 0xFE]))
        # window-bypass to the fixed region must be refused, leaving pages 14/15 intact
        before = bytes(self.m.flash[0x3D500:0x3D600])
        self.assertEqual(self._cmd([ord("W"), 0, 0xD5, 0x00] + [0xAA] * 256, 1), b"\x85")
        self.assertEqual(bytes(self.m.flash[0x3D500:0x3D600]), before)
        # full keypad patch apply (stub sector first), verify == patch_image
        sect = defaultdict(dict)
        for off, orig, new in kp.patch_diff():
            for i, b in enumerate(new):
                sect[(off + i) & ~0xFF][(off + i) & 0xFF] = b
        for base in (0x1E00, 0x1000, 0x1100):
            w = 0x4000 | (base & 0x3FFF); p, wh, wl = base >> 14, w >> 8, w & 0xFF
            cur = self._cmd([ord("R"), p, wh, wl], 256)
            want = bytearray(cur)
            for o, b in sect[base].items():
                want[o] = b
            self.assertEqual(self._cmd([ord("W"), p, wh, wl] + list(want), 1), b"\x84")
            self.assertEqual(self._cmd([ord("R"), p, wh, wl], 256), bytes(want))
        self.assertEqual(bytes(self.m.flash), kp.patch_image(self.flash))


class BootFaultInjectionTest(unittest.TestCase):
    """Reproduce the real-radio brick in the VM: corrupting page 3 (device 0xC000 — boot-path code)
    makes the firmware execute garbage, hit an SWI, vector to the 0x8EE4 'BRA *' fault spin, and
    (on hardware) COP-reset-loop. Restoring 0xC000 from the golden dump boots clean again. This is
    the exact failure caused by the bank-encoding bug (a "page 13" write landing at page 3)."""

    FAULT_SPIN = 0x8EE4          # SWI/default-interrupt handler = BRA * (verified: bytes 20 FE)

    def setUp(self):
        import os
        fw = os.environ.get("GM600_FW")
        if not fw or not os.path.exists(fw):
            self.skipTest("set GM600_FW=/path/to/motofix.BIN (golden flash dump) to run this")
        self.gold = open(fw, "rb").read()

    def _reaches_fault(self, flash):
        import emu6811
        m = emu6811.Mem(flash, b"\xff" * 0x280)
        c = emu6811.CPU(m); c.reset()
        r = c.run(max_insns=6_000_000, cop_timeout=150000, stop_pc=self.FAULT_SPIN)
        return r == "stop"

    def test_page3_corruption_bricks_and_restore_recovers(self):
        gold = self.gold
        self.assertEqual(gold[0xC000:0xC008].hex(), "34c60130e700f634",
                         "golden page-3 reference bytes changed — update this test")
        broken = bytearray(gold)
        broken[0xC000:0xC100] = bytes((i ^ 0x3C) & 0xFF for i in range(256))   # what the bug wrote
        recovered = bytearray(broken)
        recovered[0xC000:0xC100] = gold[0xC000:0xC100]                          # the reflash fix
        self.assertFalse(self._reaches_fault(gold),       "golden must not hit the fault spin")
        self.assertTrue(self._reaches_fault(bytes(broken)), "corrupt page 3 must reach the fault spin")
        self.assertFalse(self._reaches_fault(bytes(recovered)), "restoring 0xC000 must boot clean")


if __name__ == "__main__":
    unittest.main()
