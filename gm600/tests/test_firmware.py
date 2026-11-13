"""GM600 tests: firmware."""
from _common import *  # noqa: F401,F403


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

    def test_aba(self):
        import emu6811
        m = emu6811.Mem()
        # ldaa #$F0 ; ldab #$20 ; aba -> A=0x10, carry set
        prog = bytes([0x86, 0xF0, 0xC6, 0x20, 0x1B, 0x20, 0xFE])
        m.ram[0x200:0x200 + len(prog)] = prog
        c = emu6811.CPU(m); c.PC = 0x200
        c.run(stop_pc=0x200 + len(prog) - 2)
        self.assertEqual(c.A, 0x10)
        self.assertTrue(c.gf(emu6811.C))


class RamdumpAssemblerTest(unittest.TestCase):
    """asm6811 must reproduce the hardware-verified dumper stub from ramdump.S."""

    # Verified in-circuit (0x3D80, 16-bit self-check, 2026-11-12): this stub dumped all 256 KB with
    # SHA-256 identical to the golden motofix.BIN.
    PROVEN = bytes.fromhex(
        "0fce3d804f5fab00361b1632088c3de426f44d26035d270220fec600f71058ce40008655b710"
        "3a86aab7103ab61074848027f9a600b71077088c800026e4cb04c13826d8ce80008655b7103a"
        "86aab7103ab61074848027f9a600b710770826e720fe2233")

    def test_ramdump_S_assembles_to_verified_bytes(self):
        import ramdump
        org, code = ramdump.build_stub()
        self.assertEqual(org, 0x3D80)
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

    def test_andb_orab_encodings(self):
        import asm6811
        a = asm6811.assemble(
            "        .org $100\n"
            "        andb #$01\n"        # C4 01
            "        andb $37B4\n"       # F4 37 B4
            "        orab #$02\n"        # CA 02
            "        orab $37B4\n")      # FA 37 B4
        self.assertEqual(a.code, bytes.fromhex("c401f437b4ca02fa37b4"))

    def test_branch_out_of_range_rejected(self):
        import asm6811
        with self.assertRaises(asm6811.AsmError):
            asm6811.assemble("        .org 0\n        bra $200\n")


class StubSelfCheckTest(unittest.TestCase):
    """Every RAM stub carries a 16-bit (Fletcher-style) self-check and refuses to run if damaged."""

    STUBS = ("ramdump.S", "flashtool.S", "lcd_text.S", "led_cmd.S", "lcd_raw.S", "keyevt.S", "asfic_push.S")

    def _run(self, a, code):
        import emu6811
        m = emu6811.Mem()
        m.ram[a.org:a.org + len(code)] = code
        c = emu6811.CPU(m)
        c.SP, c.PC = 0x3FFF, a.org
        c.run(max_insns=40000, stop_pc=a.symbols["selfok"])
        return c.PC

    def test_intact_stub_passes_and_damaged_stub_halts(self):
        import asm6811
        for name in self.STUBS:
            a = asm6811.assemble_file(os.path.join(STUBS, name))
            s1 = s2 = 0
            for v in a.code:
                s1 = (s1 + v) & 0xFF
                s2 = (s2 + s1) & 0xFF
            self.assertEqual((s1, s2), (0, 0), name)                    # Fletcher-style, both zero
            self.assertGreaterEqual(a.org, 0x3D80, name)              # not the firmware's 0x2000 pool
            self.assertEqual(self._run(a, a.code), a.symbols["selfok"], name)
            bad = bytearray(a.code)
            bad[-5] ^= 0x01                                             # one bit flipped near the end
            self.assertEqual(self._run(a, bytes(bad)), a.symbols["damaged"], name)
            swapped = bytearray(a.code)                                 # a plain 8-bit sum misses this
            i = next(i for i in range(len(a.code) - 8, 0, -1) if a.code[i] != a.code[i + 1])
            swapped[i], swapped[i + 1] = swapped[i + 1], swapped[i]
            self.assertEqual(self._run(a, bytes(swapped)), a.symbols["damaged"], name)


class KeypadPatchTest(unittest.TestCase):
    """The read_adc override patch: image edits are correct and reversible."""

    @staticmethod
    def _image():
        import gm600_keypad_patch as kp
        f = bytearray(0x40000)
        f[kp.PATCH_SITE:kp.PATCH_SITE + 3] = kp.PATCH_ORIG     # ldab ADR1 at the site
        f[kp.STUB_SITE:kp.COMP_SITE + 1] = b"\xFF" * (kp.COMP_SITE + 1 - kp.STUB_SITE)   # stub + comp byte erased
        return bytes(f)

    def test_patch_applies_both_sites(self):
        import gm600_keypad_patch as kp
        out = kp.patch_image(self._image())
        self.assertEqual(out[kp.PATCH_SITE:kp.PATCH_SITE + 3], kp.PATCH_NEW)
        self.assertEqual(out[kp.STUB_SITE:kp.STUB_SITE + len(kp.STUB)], kp.STUB)
        self.assertEqual(out[kp.COMP_SITE], kp.COMP)                # boot flash-XOR compensation

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


class SetChanTest(unittest.TestCase):
    """setchan frequency encoder: reproduces the words DPS wrote into the bench codeplug."""

    def test_freq_words_match_dps(self):
        from gm600py.radio.setchan import freq_word
        self.assertEqual(freq_word(446.06875), 0xAC6B)     # ch 1, 6.25 kHz grid (DPS-written)
        self.assertEqual(freq_word(438.625), 0x31B5)       # ch 4 RX, 5 kHz grid preferred
        self.assertEqual(freq_word(431.025), 0x2BC5)       # ch 4 TX
        self.assertEqual(freq_word(439.075), 0x320F)       # RU726 output
        self.assertEqual(freq_word(431.475), 0x2C1F)       # RU726 input
        with self.assertRaises(ValueError):
            freq_word(439.0761)                             # off-grid
        with self.assertRaises(ValueError):
            freq_word(146.5)                                # out of band


class FlashToolTest(unittest.TestCase):
    """flashtool: the resident AT29C020 stub assembles, and the address mapping is right."""

    def test_stub_assembles_deterministically(self):
        import flashtool
        org, code = flashtool.build_stub()
        self.assertEqual(org, 0x3D80)
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
        a = asm6811.assemble_file(os.path.join(STUBS, "flashtool.S"))
        self.org, self.stub, self.loop = a.org, a.code, a.symbols["loop"]
        self.m, self.c = self._boot(emu6811)

    def _boot(self, emu6811):
        cp = b"\xff" * 0x280
        m = emu6811.Mem(self.flash, cp)
        c = emu6811.CPU(m)
        c.reset(); c.run(max_insns=6_000_000, cop_timeout=150000)
        m.ram[self.org:self.org + len(self.stub)] = self.stub
        c.PC = self.org; c.sf(emu6811.I, True); c.SP = 0x01FF
        m.pin12_12v = True
        # let the stub pass its self-check and clear the SCI (as the host's 0.3 s wait does) before
        # any command arrives - an earlier byte would be dropped by that clear
        c.run(max_insns=100_000, stop_pc=self.loop)
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


    def test_echo_and_pl_patch_through_the_stub(self):
        from gm600py.firmware import pl_patch
        pat = bytes((i * 37 + 11) & 0xFF for i in range(256))
        before = bytes(self.m.flash)
        self.assertEqual(self._cmd([ord("E")] + list(pat), 256), pat)      # transport test
        self.assertEqual(bytes(self.m.flash), before)                     # ...touches no flash
        # page 14/15: only the two pl_patch sectors; the rest of those pages (vectors!) are refused
        for p, wh in ((14, 0x4E), (15, 0x7F), (15, 0x5F), (14, 0x7F)):
            self.assertEqual(self._cmd([ord("W"), p, wh, 0] + [0xAA] * 256, 1), b"\x85")
        self.assertEqual(bytes(self.m.flash), before)
        for base, orig, new in pl_patch.patch_diff(self.flash):           # safe order, as flashtool does
            w = 0x4000 | (base & 0x3FFF); p, wh, wl = base >> 14, w >> 8, w & 0xFF
            self.assertEqual(self._cmd([ord("R"), p, wh, wl], 256), orig)
            self.assertEqual(self._cmd([ord("W"), p, wh, wl] + list(new), 1), b"\x84")
            self.assertEqual(self._cmd([ord("R"), p, wh, wl], 256), new)
        self.assertEqual(bytes(self.m.flash), pl_patch.patch_image(self.flash))


    def test_conv_patch_sector_only_on_page_9(self):
        from gm600py.firmware import conv_patch
        before = bytes(self.m.flash)
        for wh in (0x4C, 0x4E, 0x40, 0x7F):                                  # the rest of page 9: refused
            self.assertEqual(self._cmd([ord("W"), 9, wh, 0] + [0xAA] * 256, 1), b"\x85")
        self.assertEqual(bytes(self.m.flash), before)
        (base, orig, new), = conv_patch.patch_diff(self.flash)
        w = 0x4000 | (base & 0x3FFF); p, wh, wl = base >> 14, w >> 8, w & 0xFF
        self.assertEqual((p, wh, wl), (9, 0x4D, 0x00))
        self.assertEqual(self._cmd([ord("R"), p, wh, wl], 256), orig)
        self.assertEqual(self._cmd([ord("W"), p, wh, wl] + list(new), 1), b"\x84")
        self.assertEqual(self._cmd([ord("R"), p, wh, wl], 256), new)
        self.assertEqual(bytes(self.m.flash), conv_patch.patch_image(self.flash))

    def test_cut_off_write_times_out_and_attach_is_safe(self):
        # a 'W' cut off mid-data (dropped link) must not swallow a reconnecting host's bytes as data
        before = bytes(self.m.flash)
        self.m.sci_out.clear()
        self.m.feed_rx(bytes([ord("W"), 15, 0x5E, 0x00]) + bytes(range(100)))   # a pl_patch sector, cut off
        self.c.run(max_insns=4_000_000, cop_timeout=0)                         # > the stub receive timeout (65536 polls, ~0.7 s on the radio)
        pat = bytes((i * 37 + 11) & 0xFF for i in range(256))
        self.assertEqual(self._cmd([ord("E")] + list(pat), 256), pat)            # what --attach sends first
        self.assertEqual(bytes(self.m.flash), before)                           # nothing was programmed


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


class AsficPushTest(unittest.TestCase):
    """asfic_push.S sets/clears GCB1 (byte 8 bit 1 of the 0x37AC ASFIC image) and pushes the image."""

    def test_gcb1_set_and_clear_in_vm(self):
        import asm6811
        import emu6811
        import sbep
        fw, cp = os.environ.get("GM600_FW"), os.environ.get("GM600_CP")
        if not fw or not cp:
            self.skipTest("set GM600_FW=motofix.BIN and GM600_CP=<640-byte codeplug> to run this")
        with open(fw, "rb") as f:
            flash = f.read()
        with open(cp, "rb") as f:
            m = emu6811.Mem(flash, f.read())
        c = emu6811.CPU(m)
        c.reset()
        c.run(max_insns=3_000_000, cop_timeout=150000)
        a = asm6811.assemble_file(os.path.join(STUBS, "asfic_push.S"))
        self.assertGreaterEqual(a.org, 0x3DB9)                  # clear of the resident keyevt.S
        m.ram[a.org:a.org + len(a.code)] = a.code
        pushed = []
        m.watch = lambda addr, v: pushed.append(v) if addr == 0x102A and m.reg[0x7E] & 0x61 == 0x01 else None

        def launch(req):
            m.ram[0x3F02] = req
            pushed.clear()
            m.feed_rx(sbep.encode(0x26, a.org.to_bytes(2, "big")))
            c.run(max_insns=300_000, cop_timeout=150000)

        power = m.ram[0x37B4] & 0x04
        launch(1)
        self.assertTrue(m.ram[0x37B4] & 0x02)                   # GCB1 set in the image
        self.assertEqual(m.ram[0x37B4] & 0x04, power)           # power latch untouched
        self.assertTrue(pushed and pushed[8] & 0x02)            # and sent to the ASFIC select
        launch(0)
        self.assertFalse(m.ram[0x37B4] & 0x02)
        c.run(max_insns=1_000_000, cop_timeout=150000)
        self.assertEqual(bytes(m.ram[0x34BD:0x34C0]), b"322")   # firmware still running normally


class PlPatchTest(unittest.TestCase):
    """pl_patch: pin 4 (GCB1) = NOT GCB0 in every ASFIC push; the boot flash-XOR check stays 0xFF."""

    def _flash(self):
        fw = os.environ.get("GM600_FW")
        if not fw:
            self.skipTest("set GM600_FW=motofix.BIN to run this")
        with open(fw, "rb") as f:
            return f.read()

    def test_boot_check_xor_kept(self):
        from gm600py.firmware import pl_patch, keypad_patch
        g = self._flash()
        self.assertEqual(pl_patch.flash_xor(g), 0xFF)
        self.assertEqual(pl_patch.flash_xor(pl_patch.patch_image(g)), 0xFF)
        self.assertEqual(pl_patch.flash_xor(keypad_patch.patch_image(g)), 0xFF)
        diff = pl_patch.patch_diff(g)
        self.assertEqual([s for s, _, _ in diff], [0x3DE00, 0x38F00])      # cave FIRST, then the hook
        self.assertEqual(g[0x3DE00:0x3DF00], b"\xff" * 256)                 # whole cave sector erased
        for s, _, _ in diff:
            self.assertTrue(0x02000 <= s < 0x3E000)                          # outside both boot blocks
        for steps in (diff, diff[::-1]):                                     # every partial state boots
            img = bytearray(g)
            for s, _, new in steps:
                img[s:s + 256] = new
                self.assertEqual(pl_patch.flash_xor(img), 0xFF)

    def test_patched_firmware_drives_gcb1_from_mute_in_vm(self):
        import emu6811
        from gm600py.firmware import pl_patch
        cp = os.environ.get("GM600_CP")
        if not cp:
            self.skipTest("set GM600_CP=<640-byte codeplug> to run this")
        with open(cp, "rb") as f:
            m = emu6811.Mem(pl_patch.patch_image(self._flash()), f.read())
        c = emu6811.CPU(m)
        pushes, cur = [], []

        def watch(addr, v):
            if addr == 0x102A and m.reg[0x7E] & 0x61 == 0x01:              # ASFIC select (decoder Y2)
                cur.append(v)
            elif addr == 0x107E and cur:
                if len(cur) == 21:
                    pushes.append(bytes(cur))
                cur.clear()
        m.watch = watch
        c.reset()
        c.run(max_insns=3_000_000, cop_timeout=150000)
        self.assertEqual(bytes(m.ram[0x34BD:0x34C0]), b"322")              # boot check passed, normal idle
        self.assertTrue(pushes)
        for p in pushes:
            self.assertEqual(bool(p[8] & 0x02), not p[8] & 0x01)           # GCB1 = NOT GCB0
            self.assertTrue(p[8] & 0x04)                                    # power latch untouched (on)
        self.assertTrue(any(not p[8] & 0x01 for p in pushes))              # an unmuted push was seen


if __name__ == "__main__":
    unittest.main()


class VmBootTest(unittest.TestCase):
    """With the bench radio's port/A-D levels modelled, the VM boots to the idle own-ident screen."""

    def test_vm_shows_own_unit_number_and_presses_keys(self):
        import emu6811
        import gm600_display
        fw, cp = os.environ.get("GM600_FW"), os.environ.get("GM600_CP")
        if not fw or not cp:
            self.skipTest("set GM600_FW=motofix.BIN and GM600_CP=<640-byte codeplug> to run this")
        with open(fw, "rb") as f:
            flash = f.read()
        with open(cp, "rb") as f:
            code = bytearray(f.read())
        unit = code[0x0F0B - 0x0D80] << 8 | code[0x0F0C - 0x0D80]
        m = emu6811.Mem(flash, bytes(code))
        tap = gm600_display.SpiTap(m)
        c = emu6811.CPU(m)
        c.reset()
        c.run(max_insns=3_000_000, cop_timeout=150000)
        want = "%3d" % unit
        self.assertEqual(bytes(m.ram[0x34BD:0x34C0]).decode(), want)
        self.assertEqual(set(tap.state()["segments"]), gm600_display.segments_for_text(want, 0x00, 0x40))
        # select C toggles the external-alarm icons
        m.adc = gm600_display.adc_for(gm600_display.BUTTONS["C"])
        c.run(max_insns=300_000, cop_timeout=150000)
        m.adc = list(emu6811.IDLE_ADC)
        c.run(max_insns=300_000, cop_timeout=150000)
        self.assertTrue({"b0.0", "b1.0"} <= set(tap.state()["segments"]))


class KeyHoldTest(unittest.TestCase):
    """keyevt.S + keypad-task pause = a remote key hold; in the VM a long-hold A latches conventional."""

    def test_long_hold_a_latches_conventional_in_vm(self):
        import asm6811
        import emu6811
        import sbep
        fw, cp = os.environ.get("GM600_FW"), os.environ.get("GM600_CP")
        if not fw or not cp:
            self.skipTest("set GM600_FW=motofix.BIN and GM600_CP=<640-byte codeplug> to run this")
        with open(fw, "rb") as f:
            flash = f.read()
        with open(cp, "rb") as f:
            m = emu6811.Mem(flash, f.read())
        c = emu6811.CPU(m)
        c.reset()
        c.run(max_insns=3_000_000, cop_timeout=150000)
        self.assertEqual(bytes(m.ram[0x34BD:0x34C0]), b"322")
        a = asm6811.assemble_file(os.path.join(STUBS, "keyevt.S"))
        m.ram[a.org:a.org + len(a.code)] = a.code
        m.ram[0x11D8] |= 0x10                                   # pause the keypad task

        def post(state):                                        # through the firmware's own 0x26
            m.ram[0x3F00], m.ram[0x3F01] = 20, state
            m.feed_rx(sbep.encode(0x26, a.org.to_bytes(2, "big")))
            c.run(max_insns=300_000, cop_timeout=150000)

        post(1)
        c.run(max_insns=2_000_000, cop_timeout=150000)
        self.assertEqual(bytes(m.ram[0x34BD:0x34C0]), b"  1")   # conventional channel 1
        post(0)
        m.ram[0x11D8] &= ~0x10 & 0xFF                           # resume the keypad task
        c.run(max_insns=1_000_000, cop_timeout=150000)
        self.assertEqual(bytes(m.ram[0x34BD:0x34C0]), b"  1")   # stays latched after release


class AsficPushTest(unittest.TestCase):
    """asfic_push.S sets/clears GCB1 (byte 8 bit 1 of the 0x37AC ASFIC image) and pushes the image."""

    def test_gcb1_set_and_clear_in_vm(self):
        import asm6811
        import emu6811
        import sbep
        fw, cp = os.environ.get("GM600_FW"), os.environ.get("GM600_CP")
        if not fw or not cp:
            self.skipTest("set GM600_FW=motofix.BIN and GM600_CP=<640-byte codeplug> to run this")
        with open(fw, "rb") as f:
            flash = f.read()
        with open(cp, "rb") as f:
            m = emu6811.Mem(flash, f.read())
        c = emu6811.CPU(m)
        c.reset()
        c.run(max_insns=3_000_000, cop_timeout=150000)
        a = asm6811.assemble_file(os.path.join(STUBS, "asfic_push.S"))
        self.assertGreaterEqual(a.org, 0x3DB9)                  # clear of the resident keyevt.S
        m.ram[a.org:a.org + len(a.code)] = a.code
        pushed = []
        m.watch = lambda addr, v: pushed.append(v) if addr == 0x102A and m.reg[0x7E] & 0x61 == 0x01 else None

        def launch(req):
            m.ram[0x3F02] = req
            pushed.clear()
            m.feed_rx(sbep.encode(0x26, a.org.to_bytes(2, "big")))
            c.run(max_insns=300_000, cop_timeout=150000)

        power = m.ram[0x37B4] & 0x04
        launch(1)
        self.assertTrue(m.ram[0x37B4] & 0x02)                   # GCB1 set in the image
        self.assertEqual(m.ram[0x37B4] & 0x04, power)           # power latch untouched
        self.assertTrue(pushed and pushed[8] & 0x02)            # and sent to the ASFIC select
        launch(0)
        self.assertFalse(m.ram[0x37B4] & 0x02)
        c.run(max_insns=1_000_000, cop_timeout=150000)
        self.assertEqual(bytes(m.ram[0x34BD:0x34C0]), b"322")   # firmware still running normally


class PlPatchTest(unittest.TestCase):
    """pl_patch: pin 4 (GCB1) = NOT GCB0 in every ASFIC push; the boot flash-XOR check stays 0xFF."""

    def _flash(self):
        fw = os.environ.get("GM600_FW")
        if not fw:
            self.skipTest("set GM600_FW=motofix.BIN to run this")
        with open(fw, "rb") as f:
            return f.read()

    def test_boot_check_xor_kept(self):
        from gm600py.firmware import pl_patch, keypad_patch
        g = self._flash()
        self.assertEqual(pl_patch.flash_xor(g), 0xFF)
        self.assertEqual(pl_patch.flash_xor(pl_patch.patch_image(g)), 0xFF)
        self.assertEqual(pl_patch.flash_xor(keypad_patch.patch_image(g)), 0xFF)
        diff = pl_patch.patch_diff(g)
        self.assertEqual([s for s, _, _ in diff], [0x3DE00, 0x38F00])      # cave FIRST, then the hook
        self.assertEqual(g[0x3DE00:0x3DF00], b"\xff" * 256)                 # whole cave sector erased
        for s, _, _ in diff:
            self.assertTrue(0x02000 <= s < 0x3E000)                          # outside both boot blocks
        for steps in (diff, diff[::-1]):                                     # every partial state boots
            img = bytearray(g)
            for s, _, new in steps:
                img[s:s + 256] = new
                self.assertEqual(pl_patch.flash_xor(img), 0xFF)

    def test_patched_firmware_drives_gcb1_from_mute_in_vm(self):
        import emu6811
        from gm600py.firmware import pl_patch
        cp = os.environ.get("GM600_CP")
        if not cp:
            self.skipTest("set GM600_CP=<640-byte codeplug> to run this")
        with open(cp, "rb") as f:
            m = emu6811.Mem(pl_patch.patch_image(self._flash()), f.read())
        c = emu6811.CPU(m)
        pushes, cur = [], []

        def watch(addr, v):
            if addr == 0x102A and m.reg[0x7E] & 0x61 == 0x01:              # ASFIC select (decoder Y2)
                cur.append(v)
            elif addr == 0x107E and cur:
                if len(cur) == 21:
                    pushes.append(bytes(cur))
                cur.clear()
        m.watch = watch
        c.reset()
        c.run(max_insns=3_000_000, cop_timeout=150000)
        self.assertEqual(bytes(m.ram[0x34BD:0x34C0]), b"322")              # boot check passed, normal idle
        self.assertTrue(pushes)
        for p in pushes:
            self.assertEqual(bool(p[8] & 0x02), not p[8] & 0x01)           # GCB1 = NOT GCB0
            self.assertTrue(p[8] & 0x04)                                    # power latch untouched (on)
        self.assertTrue(any(not p[8] & 0x01 for p in pushes))              # an unmuted push was seen


class ConvPatchTest(unittest.TestCase):
    """conv_patch: a cold start (mode block check fails) defaults to conventional channel 1."""

    def _flash(self):
        fw = os.environ.get("GM600_FW")
        if not fw:
            self.skipTest("set GM600_FW=motofix.BIN to run this")
        with open(fw, "rb") as f:
            return f.read()

    def _boot(self, img, ram=None):
        import emu6811
        cp = os.environ.get("GM600_CP")
        if not cp:
            self.skipTest("set GM600_CP=<640-byte codeplug> to run this")
        with open(cp, "rb") as f:
            m = emu6811.Mem(img, f.read())
        for a, v in (ram or {}).items():
            m.ram[a] = v
        c = emu6811.CPU(m)
        c.reset()
        c.run(max_insns=3_000_000, cop_timeout=150000)
        return m

    def test_one_xor_neutral_sector(self):
        from gm600py.firmware import conv_patch, pl_patch
        g = self._flash()
        self.assertEqual(conv_patch.state(g), "original")
        self.assertEqual([s for s, _, _ in conv_patch.patch_diff(g)], [0x24D00])
        p = conv_patch.patch_image(g)
        self.assertEqual(conv_patch.state(p), "patched")
        self.assertEqual([i for i in range(len(g)) if g[i] != p[i]], [0x24D31, 0x24D9A])
        self.assertEqual(pl_patch.flash_xor(p), 0xFF)
        self.assertEqual(pl_patch.flash_xor(conv_patch.patch_image(pl_patch.patch_image(g))), 0xFF)
        with self.assertRaises(ValueError):
            conv_patch.patch_image(p)                                        # refuses a patched image

    def test_cold_boot_is_conventional_in_vm(self):
        from gm600py.firmware import conv_patch, pl_patch
        g = self._flash()
        for img, want, mode in ((g, b"322", 0x01),
                                (conv_patch.patch_image(pl_patch.patch_image(g)), b"  1", 0xFF)):
            m = self._boot(img)                                              # internal RAM zero: check fails
            self.assertEqual(bytes(m.ram[0x34BD:0x34C0]), want)
            self.assertEqual(m.ram[0x85], mode)
            self.assertEqual(m.ram[0x89], (0xAA + sum(m.ram[0x84:0x89])) & 0xFF)

    def test_compensation_bra_never_executes(self):
        # The compensation byte is the operand of the dead `bra` at page 9 0x4D30 (20 FC -> 20 02, which
        # would land mid-function). Prove it's dead: cold boot, long-hold A both ways, every key, a warm
        # reset — the PC must never be at 0x4D30/0x4D31 with page 9 banked in.
        import emu6811
        import gm600_display
        from gm600py.firmware import conv_patch
        cp = os.environ.get("GM600_CP")
        if not cp:
            self.skipTest("set GM600_CP=<640-byte codeplug> to run this")
        with open(cp, "rb") as f:
            m = emu6811.Mem(conv_patch.patch_image(self._flash()), f.read())
        c = emu6811.CPU(m)
        hits = []

        def watch(cpu):
            if cpu.PC in (0x4D30, 0x4D31) and cpu.m.reg[0x58] >> 2 == 9:
                hits.append(cpu.PC)

        def run(n=3_000_000):
            c.run(max_insns=n, cop_timeout=150000, trace=watch)

        def hold(key, n):
            m.adc = gm600_display.adc_for(gm600_display.BUTTONS[key])
            run(n)
            m.adc = list(emu6811.IDLE_ADC)
            run(1_500_000)
        c.reset(); run()
        hold("A", 2_000_000)
        self.assertEqual(m.ram[0x85], 0x01)                                  # long-hold A -> trunked
        hold("A", 2_000_000)
        self.assertEqual(m.ram[0x85], 0xFF)                                  # and back
        for k in gm600_display.BUTTONS:
            hold(k, 300_000)
        c.reset(); run()
        self.assertEqual(hits, [])

    def test_retained_trunked_mode_still_wins(self):
        from gm600py.firmware import conv_patch
        state = [0x90, 0x01, 0x00, 0x00, 0x00]                               # a valid retained "trunked"
        ram = dict(zip(range(0x84, 0x89), state))
        ram[0x89] = (0xAA + sum(state)) & 0xFF
        m = self._boot(conv_patch.patch_image(self._flash()), ram)
        self.assertEqual(bytes(m.ram[0x34BD:0x34C0]), b"322")


if __name__ == "__main__":
    unittest.main()
