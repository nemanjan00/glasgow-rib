#!/usr/bin/env python3
"""GM600 control-head display model + SVG renderer + live web UI.

The glass is one start-bit-framed serial controller (see images/lcd_segments/README.md): it ignores 0
bits until a 1 (start bit), latches the next 33 data bits, then waits again; a partial frame survives
chip-select. 33 data bits = 21 digit segments + 9 icons + 3 unused. The front-panel LEDs and the
LCD/keypad backlight are a separate 1-byte shift register (bit 0 amber, 1 red, 2 green, 7 backlight).

  Controller   bit-level model of the display controller (feed it the bytes clocked out on SPI)
  SpiTap       attaches to an emu6811.Mem and decodes the firmware's (or a stub's) SPI writes
  render_svg   draws the front panel (assets/make_banner.py) with the live segments + LEDs
  serve_ui     tiny HTTP server: / = live page, /display.svg, /state.json

  gm600_display.py --text 53X out.svg [--backlight] [--icons 0040] [--leds 05]   render a preview
"""
import argparse
import http.server
import json
import threading

# data bit index (after the start bit) -> segment name; dN.x = digit N (left->right) segment x,
# bA.k = icon driven by struct byte bA bit k. Indices 5, 18, 22 are unused.
DATA_BITS = {
    0: "d0.f", 1: "d0.g", 2: "b0.2", 3: "b0.1", 4: "b0.0", 6: "b1.2", 7: "d0.e", 8: "d0.d",
    9: "d0.c", 10: "d1.e", 11: "b1.1", 12: "d1.d", 13: "d1.c", 14: "d2.e", 15: "d2.d", 16: "d2.c",
    17: "b1.0", 19: "b1.4", 20: "b1.5", 21: "b1.6", 23: "d2.b", 24: "d2.a", 25: "d2.f", 26: "d2.g",
    27: "d1.b", 28: "d1.a", 29: "d1.f", 30: "d1.g", 31: "d0.b", 32: "d0.a",
}
FRAME_BITS = 33
DISPLAY_SELECT = 0x61        # 0x107E bits asserted while the display controller is clocked
LED_STROBE = 0x20            # 0x107E bit pulsed (with 0x01) to latch the LED shift register
SPCR, SPDR, CSPORT = 0x1028, 0x102A, 0x107E


class Controller:
    """Start-bit-framed 33-bit display controller. ``segments`` is the latched set."""

    def __init__(self):
        self.segments = set()
        self.frames = 0
        self._collecting = False
        self._bits = []

    def feed_bit(self, bit):
        if not self._collecting:
            if bit:
                self._collecting, self._bits = True, []
            return
        self._bits.append(bit)
        if len(self._bits) == FRAME_BITS:
            self.segments = {DATA_BITS[i] for i, b in enumerate(self._bits) if b and i in DATA_BITS}
            self.frames += 1
            self._collecting = False

    def feed(self, data):
        for byte in bytes(data):
            for k in range(7, -1, -1):          # SPI is MSB-first
                self.feed_bit((byte >> k) & 1)


class SpiTap:
    """Watches an emu6811.Mem's register writes and keeps the display + LED state current.

    Display bytes: written to SPDR while 0x107E has all of 0x61 asserted. LEDs: the last SPDR byte
    sent with no display select is latched when the 0x20 strobe rises (firmware entry 10)."""

    def __init__(self, mem):
        self.display = Controller()
        self.leds = 0
        self.lock = threading.Lock()
        self._cs = mem.reg[CSPORT - 0x1000]
        self._last_other = None
        self._chain = mem.watch
        mem.watch = self._watch

    def _watch(self, addr, value):
        if self._chain:
            self._chain(addr, value)
        if addr == SPDR:
            if self._cs & DISPLAY_SELECT == DISPLAY_SELECT:
                with self.lock:
                    self.display.feed([value])
            else:
                self._last_other = value
        elif addr == CSPORT:
            rising = value & ~self._cs
            if rising & LED_STROBE and not value & 0x40 and self._last_other is not None:
                with self.lock:
                    self.leds = self._last_other
            self._cs = value

    def state(self):
        with self.lock:
            return {"segments": sorted(self.display.segments), "leds": self.leds,
                    "frames": self.display.frames}


# --- SVG ---------------------------------------------------------------------------------------

def _panel():
    """assets/make_banner.py draws the GM600 front panel; its panel_svg() takes a live state."""
    import importlib.util
    import os
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "assets",
                        "make_banner.py")
    spec = importlib.util.spec_from_file_location("make_banner", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_PANEL = None


def render_svg(segments, leds=0):
    """SVG of the front panel showing ``segments`` lit and the LED byte ``leds`` (bit 0 amber,
    1 red, 2 green, 7 backlight), drawn with the banner's panel (assets/make_banner.py)."""
    global _PANEL
    if _PANEL is None:
        _PANEL = _panel()
    return _PANEL.panel_svg(set(segments), leds, caption=False,
                            title="<title>GM600 display (live)</title>")


def segments_for_text(text, b0=0x00, b1=0x00):
    """Segments lit by lcd_text-style text (font from lcd_text.FONT) plus icon bytes."""
    from lcd_text import pack, text_to_segs
    ctrl = Controller()
    ctrl.feed(bytes(5) + pack(bytes([b0, b1]) + text_to_segs(text)))
    return ctrl.segments


# --- web UI ------------------------------------------------------------------------------------

_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>GM600 display</title>
<style>body{background:#111;color:#ccc;font:14px monospace;display:flex;flex-direction:column;
align-items:center;margin-top:30px}#d svg{width:min(1100px,96vw);height:auto}</style></head>
<body><div id="d"></div><div id="s"></div><script>
async function tick(){try{const r=await fetch('/display.svg',{cache:'no-store'});
document.getElementById('d').innerHTML=await r.text();
const j=await (await fetch('/state.json',{cache:'no-store'})).json();
document.getElementById('s').textContent='frames '+j.frames+' · '+(j.segments.join(' ')||'(blank)');
}catch(e){document.getElementById('s').textContent='disconnected'}setTimeout(tick,200)}tick();
</script></body></html>"""


def serve_ui(get_state, port=8600):
    """Serve the live display at http://127.0.0.1:<port>/. ``get_state`` returns a dict with
    ``segments``, ``leds`` and ``frames``. Runs in a daemon thread; returns the bound port."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            st = get_state()
            if self.path.startswith("/display.svg"):
                body, ctype = render_svg(st["segments"], st["leds"]).encode(), "image/svg+xml"
            elif self.path.startswith("/state.json"):
                body, ctype = json.dumps(st).encode(), "application/json"
            elif self.path in ("/", "/index.html"):
                body, ctype = _PAGE.encode(), "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1]


def main():
    ap = argparse.ArgumentParser(description="Render a GM600 display preview as SVG.")
    ap.add_argument("--text", required=True)
    ap.add_argument("out")
    ap.add_argument("--icons", default="0000", help="struct icon bytes b0b1 in hex")
    ap.add_argument("--leds", default="00", help="LED byte in hex (bit 7 = backlight)")
    ap.add_argument("--backlight", action="store_true")
    args = ap.parse_args()
    b0, b1 = bytes.fromhex(args.icons)
    leds = int(args.leds, 16) | (0x80 if args.backlight else 0)
    with open(args.out, "w") as f:
        f.write(render_svg(segments_for_text(args.text, b0, b1), leds))


if __name__ == "__main__":
    main()
