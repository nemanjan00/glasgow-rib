"""GM600 tests: display."""
from _common import *  # noqa: F401,F403


class LcdTextTest(unittest.TestCase):
    """lcd_text.S/.py: the hardware-verified 7-seg stub and its font."""

    # Verified on the radio: the original at 0x2000 pushed 322 over a 686 codeplug, then HI/HEL/...;
    # this build (0x3D80, 16-bit self-check) showed "HI" on the radio (2026-11-12).
    PROVEN = bytes.fromhex(
        "0fce3d804f5fab00361b1632088c3deb26f44d26035d270220fe8e3fffbd3dd0c1a526f9bd3d"
        "d0f73f80bd3dd0f73f81bd3dd0f73f82bd3dd0f73f83bd3dd0f73f84ce3f803c3cc605bd8f1f"
        "383820cdbd3ddeb61074842027f6f61077398655b7103a86aab7103a39c6b2")

    def test_stub_assembles_to_verified_bytes(self):
        import asm6811
        a = asm6811.assemble_file(os.path.join(STUBS, "lcd_text.S"))
        self.assertEqual(a.org, 0x3D80)
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

    def test_standalone_stub_matches_firmware_on_the_wire(self):
        # lcd_raw.S (own SPI, no firmware calls) must produce the same SPCR / select / SPDR write
        # sequence as lcd_text.S (which calls firmware entry 5) and reply 06; the LED command must
        # match entry 10; a bad checksum must send nothing (15); a stalled SPI must reply 54.
        import asm6811
        import emu6811
        import lcd_text
        import sbep
        img = os.environ.get("GM600_FW")
        if not img:
            self.skipTest("set GM600_FW=/path/to/motofix.BIN (a flash dump) to run this")
        flash = open(img, "rb").read()
        here = STUBS

        def trace(stub, payload, spi_stalls=False):
            m = emu6811.Mem(flash=flash)
            if spi_stalls:
                wb = m.wb
                def stalled(ad, v):
                    wb(ad, v)
                    if ad == 0x102A:
                        m.reg[0x29] &= 0x7F
                m.wb = stalled
            c = emu6811.CPU(m)
            a = asm6811.assemble_file(os.path.join(here, stub))
            m.ram[a.org:a.org + len(a.code)] = a.code
            m.reg[0x28] = 0x5C
            m.pins[0x7E] = 0x00     # PORTG inputs low: compare only the select bits the code drives
            log = []
            m.watch = lambda ad, v: log.append((ad, v)) if ad in (0x1028, 0x107E, 0x102A) else None
            c.SP, c.PC = 0x3FFF, a.org
            c.run(max_insns=20000, stop_pc=a.symbols["selfok"])
            m.feed_rx(payload)
            c.run(max_insns=300000)
            return log, bytes(m.sci_out), m.reg[0x7E]

        def cmd(sync, body, good=True):
            frame = bytes([sync]) + body
            return frame + bytes([sbep.checksum(frame) ^ (0 if good else 1)])

        for text in ("322", "HI", "-9-"):
            st = bytes([0x00, 0x40]) + lcd_text.text_to_segs(text)
            fw, _, _ = trace("lcd_text.S", bytes([0xA5]) + st)
            raw, reply, _ = trace("lcd_raw.S", cmd(0xA5, bytes(5) + lcd_text.pack(st)))
            self.assertTrue(fw)
            self.assertEqual(raw, fw, text)
            self.assertEqual(reply, b"\x06")
        log, reply, _ = trace("lcd_raw.S", cmd(0x5A, b"\xc0"))
        self.assertEqual(log, [(0x1028, 0x50), (0x1028, 0x51), (0x102A, 0xC0), (0x107E, 0x21),
                               (0x107E, 0x00), (0x1028, 0x5D)])
        self.assertEqual(reply, b"\x06")
        log, reply, _ = trace("lcd_raw.S", cmd(0xA5, bytes(10), good=False))
        self.assertEqual((log, reply), ([], b"\x15"))
        log, reply, cs = trace("lcd_raw.S", cmd(0xA5, bytes(10)), spi_stalls=True)
        self.assertEqual(reply, b"\x54")
        self.assertEqual([v for ad, v in log if ad == 0x102A], [0])   # gave up on the first byte
        self.assertEqual(cs & 0x61, 0)                                # select released


class DisplayModelTest(unittest.TestCase):
    """gm600_display: the start-bit controller model, the SPI tap, and the SVG panel."""

    def test_controller_decodes_text_frames(self):
        import gm600_display
        import lcd_text
        c = gm600_display.Controller()
        c.feed(bytes(5) + lcd_text.pack(bytes([0x00, 0x40]) + lcd_text.text_to_segs("322")))
        want = {"b1.6"} | {f"d0.{s}" for s in "abcdg"} | {f"d{n}.{s}" for n in (1, 2) for s in "abdeg"}
        self.assertEqual(c.segments, want)

    def test_partial_frame_carries_over(self):
        # images/lcd_segments/framing run1: after AB80 left a partial frame, pattern A showed "t".
        import gm600_display
        c = gm600_display.Controller()
        c.feed(bytes.fromhex("01800003838001c0f000"))        # AB80
        self.assertEqual(c.segments, {"d0.a", "d0.b", "d0.f", "d1.g", "d2.a", "d2.b"})
        c.feed(bytes.fromhex("01800003830001c0f000"))        # AB
        self.assertEqual(c.segments, {"d0.f", "d0.g", "d0.e", "d0.d", "d2.a", "d2.f", "d2.g"})
        c.feed(bytes.fromhex("01800003830000000000"))        # A
        self.assertEqual(c.segments, {"d0.f", "d0.g", "d0.e", "d0.d"})

    def test_tap_decodes_the_standalone_stub(self):
        # lcd_raw.S only touches registers, so it runs in the emulator without a firmware image.
        import asm6811
        import emu6811
        import gm600_display
        import lcd_text
        import sbep
        m = emu6811.Mem()
        tap = gm600_display.SpiTap(m)
        c = emu6811.CPU(m)
        a = asm6811.assemble_file(os.path.join(STUBS, "lcd_raw.S"))
        m.ram[a.org:a.org + len(a.code)] = a.code
        c.SP, c.PC = 0x3FFF, a.org
        c.run(max_insns=20000, stop_pc=a.symbols["selfok"])   # past the self-check, as on the radio
        cmds = b""
        text = bytes(5) + lcd_text.pack(b"\x00\x00" + lcd_text.text_to_segs("HI"))
        for sync, body in ((0x5A, b"\x84"), (0xA5, text)):
            frame = bytes([sync]) + body
            cmds += frame + bytes([sbep.checksum(frame)])
        m.feed_rx(cmds)
        c.run(max_insns=400000)
        st = tap.state()
        self.assertEqual(st["leds"], 0x84)
        self.assertEqual(set(st["segments"]), gm600_display.segments_for_text("HI"))
        svg = gm600_display.render_svg(st["segments"], st["leds"])
        self.assertIn("lcdlit", svg)                      # backlight on
        self.assertIn("GM600", svg)


class BannerTest(unittest.TestCase):
    def test_banner_generator_reproduces_committed_banner(self):
        import importlib.util
        root = os.path.join(REPO, "assets")
        spec = importlib.util.spec_from_file_location("make_banner", os.path.join(root, "make_banner.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with open(os.path.join(root, "banner.svg")) as f:
            self.assertEqual(mod.panel_svg(), f.read())


class PageScriptTest(unittest.TestCase):
    """The web panel's JavaScript must parse (a raw newline in a JS string once broke the page)."""

    def test_page_js_parses(self):
        import re
        import shutil
        import subprocess
        import gm600_display
        if not shutil.which("node"):
            self.skipTest("node not installed")
        js = re.search(r"<script>(.*)</script>", gm600_display._PAGE, re.S).group(1)
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(js)
        try:
            r = subprocess.run(["node", "--check", f.name], capture_output=True, text=True)
        finally:
            os.unlink(f.name)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
