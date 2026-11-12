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
LED_STROBE = 0x21            # 0x107E bits raised together to latch the LED shift register
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

    Display bytes: written to SPDR while 0x107E (PORTG) has all of 0x61 asserted. LEDs: the last SPDR
    byte sent with no select asserted is latched when 0x21 rises (firmware entry 10)."""

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
            elif self._cs & DISPLAY_SELECT == 0:      # LED byte: shifted out with nothing selected
                self._last_other = value
        elif addr == CSPORT:
            rising = value & ~self._cs
            # LED latch = 0x20 and 0x01 rising together after the byte (entry 10). Entry 2 raises
            # 0x20 alone *before* its own 13-byte transfer, so a lone 0x20 is not a latch.
            if rising & 0x21 == 0x21 and not value & 0x40 and self._last_other is not None:
                with self.lock:
                    self.leds = self._last_other
            self._cs = value

    def __getstate__(self):                 # snapshots (copy.deepcopy of a booted VM): no lock
        st = dict(self.__dict__)
        del st["lock"]
        return st

    def __setstate__(self, st):
        self.__dict__.update(st)
        self.lock = threading.Lock()

    def state(self):
        with self.lock:
            return {"segments": sorted(self.display.segments), "leds": self.leds,
                    "frames": self.display.frames}


# --- keypad --------------------------------------------------------------------------------------

# The keypad is three analog ladders on A/D channels ADR1-ADR3. The firmware's decoders (page 0, CPU
# 0x4FBB / 0x4F79 / 0x4FFD) turn a reading into a key code by window: <=0x29, 0x39-0x48, 0x79-0x88,
# 0xB9-0xC8 (ADR3 has no 0x39-0x48 key); anything else (idle reads 0xFF) is "no key". A press has to
# read the same code twice in a row (debounce). code -> (A/D channel, level in the middle of its window):
KEY_CODES = {
    0x10: (0, 0x10), 0x11: (0, 0x40), 0x12: (0, 0x80), 0x13: (0, 0xC0),     # ADR1 -> 0x4FBB
    0x14: (1, 0x10), 0x15: (1, 0x40), 0x16: (1, 0x80), 0x17: (1, 0xC0),     # ADR2 -> 0x4F79
    0x08: (2, 0x10), 0x06: (2, 0x80), 0x07: (2, 0xC0),                      # ADR3 -> 0x4FFD
}

# Physical button -> key code, identified in the emulator from the radio's idle (own-ident) state by
# what each code does — not yet checked by pressing the bench radio's buttons:
#   07 / 06  scroll through shortform entries up (04, 06, 11 ...) / down (976, 975, 969 ...)
#   11 / 10  volume up / down (stepped values to the audio chip over SPI, rising / falling)
#   12       clear (key click only, nothing to clear)          13  base call (shows "306")
#   14       select A (shows "---": the trunked/conventional mode change)
#   15       select B (call-in-absence; nothing to show)       16  select C (toggles alarm icons)
#   17       select D (dedicated call, shows "400")            08  on/off (no effect on a short press)
BUTTONS = {
    "A": 0x14, "B": 0x15, "C": 0x16, "D": 0x17,
    "scroll_up": 0x07, "scroll_down": 0x06,
    "vol_down": 0x10, "vol_up": 0x11, "clear": 0x12, "call": 0x13, "power": 0x08,
}


def adc_for(code):
    """AN0-AN3 readings that make the firmware decode ``code`` (the others at their idle level)."""
    import emu6811
    adc = list(emu6811.IDLE_ADC)
    line, level = KEY_CODES[code]
    adc[line] = level
    return adc


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


def render_svg(segments, leds=0, hotspots=False):
    """SVG of the front panel showing ``segments`` lit and the LED byte ``leds`` (bit 0 amber,
    1 red, 2 green, 7 backlight), drawn with the banner's panel (assets/make_banner.py)."""
    global _PANEL
    if _PANEL is None:
        _PANEL = _panel()
    return _PANEL.panel_svg(set(segments), leds, caption=False,
                            title="<title>GM600 display (live)</title>", hotspots=hotspots)


def segments_for_text(text, b0=0x00, b1=0x00):
    """Segments lit by lcd_text-style text (font from lcd_text.FONT) plus icon bytes."""
    from lcd_text import pack, text_to_segs
    ctrl = Controller()
    ctrl.feed(bytes(5) + pack(bytes([b0, b1]) + text_to_segs(text)))
    return ctrl.segments


# --- web UI ------------------------------------------------------------------------------------

_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>GM600 display</title>
<style>body{background:#111;color:#ccc;font:14px monospace;display:flex;flex-direction:column;
align-items:center;margin-top:30px}#d svg{width:min(1100px,96vw);height:auto}
#d .hot:hover{fill-opacity:.12}#k button{background:#2a2c31;color:#ddd;border:1px solid #555;
border-radius:6px;margin:3px;padding:4px 8px;font:13px monospace;cursor:pointer}
#k button:hover,#x button:hover{background:#3a3d44}#s,#p{margin:6px}
#x button,#x a{background:#2a2c31;color:#ddd;border:1px solid #555;border-radius:6px;margin:3px;
padding:4px 8px;font:13px monospace;cursor:pointer;text-decoration:none}#cp{font:12px monospace;color:#bbb}</style></head>
<body><div id="d"></div><div id="s"></div><div id="p"></div>
<div id="k">raw key codes: CODES</div>EXTRAS<pre id="cp"></pre><script>
async function post(url){document.getElementById('p').textContent='working ...';
const r=await fetch(url,{method:'POST'});document.getElementById('p').textContent=await r.text()}
async function preview(){const pre=document.getElementById('cp');pre.textContent='reading codeplug ...';
const j=await (await fetch('/codeplug.json',{cache:'no-store'})).json();
let t='Own Unit No. '+j.own_unit+'   checksums: '+j.checksums+'\n\nch  rx MHz      tx MHz      rx PL            tx PL            opt\n';
for(const c of j.channels)t+=String(c.channel).padEnd(4)+String(c.rx_mhz.toFixed(5)).padEnd(12)+String(c.tx_mhz.toFixed(5)).padEnd(12)+c.rx_pl.padEnd(17)+c.tx_pl.padEnd(17)+c.options+'\n';
t+='\n'+j.hex;pre.textContent=t}
async function press(q){document.getElementById('p').textContent='pressing '+q+' ...';
const r=await fetch('/press?'+q,{method:'POST'});document.getElementById('p').textContent=await r.text()}
// A quick click is a single tap; only a press held past HOLD_MS becomes a hold (down ... up), so a
// click never turns into a long press (and auto-repeat) through request latency.
const HOLD_MS=400;let pending=null,timer=null,holding=null;
document.getElementById('d').addEventListener('pointerdown',e=>{const k=e.target.closest('[data-key]');
if(!k)return;e.preventDefault();pending=k.dataset.key;
timer=setTimeout(()=>{holding=pending;pending=null;press('key='+holding+'&action=down')},HOLD_MS)});
function release(){clearTimeout(timer);
if(pending){press('key='+pending+'&action=tap');pending=null}
if(holding){press('key='+holding+'&action=up');holding=null}}
document.addEventListener('pointerup',release);document.addEventListener('pointercancel',release);
async function tick(){try{const r=await fetch('/display.svg',{cache:'no-store'});
document.getElementById('d').innerHTML=await r.text();
const j=await (await fetch('/state.json',{cache:'no-store'})).json();
document.getElementById('s').textContent=(j.rx?'RX: '+j.rx+' \u00b7 ':'')+'frames '+j.frames+' \u00b7 '+(j.segments.join(' ')||'(blank)');
}catch(e){document.getElementById('s').textContent='disconnected'}setTimeout(tick,200)}tick();
</script></body></html>"""


def _hexdump(data, base=0x0D80):
    return "\n".join(f"{base + i:04X}  " + " ".join(f"{b:02X}" for b in data[i:i + 16])
                     for i in range(0, len(data), 16))


def serve_ui(get_state, port=8600, press=None, host="127.0.0.1", codeplug=None, set_mode=None):
    """Serve the live display at http://127.0.0.1:<port>/. ``get_state`` returns a dict with
    ``segments``, ``leds`` and ``frames``; ``press(code, action)`` (optional; action = tap / down / up)
    makes the panel's buttons pressable: pointer-down holds the key, pointer-up releases it. Runs in a daemon thread; returns the bound port."""
    import urllib.parse

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            url = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(url.query)
            if url.path == "/mode" and set_mode is not None:
                to = q.get("to", [""])[0]
                if to not in ("trunked", "conventional"):
                    self.send_error(400, "to must be trunked or conventional")
                    return
                try:
                    now = set_mode(to)
                except Exception as e:
                    self.send_error(503, f"mode change failed: {e}")
                    return
                body = f"mode now: {now}".encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(body)
                return
            if url.path != "/press" or press is None:
                self.send_error(404)
                return
            try:
                if "key" in q:
                    name = q["key"][0]
                    code = BUTTONS[name]
                    label = f"{name} (code {code:02X})"
                else:
                    code = int(q["code"][0], 16)
                    label = f"code {code:02X}"
                if code not in KEY_CODES:
                    raise KeyError(code)
            except (KeyError, ValueError, IndexError):
                self.send_error(400, "unknown key")
                return
            action = q.get("action", ["tap"])[0]
            if action not in ("tap", "down", "up"):
                self.send_error(400, "action must be tap, down or up")
                return
            try:
                press(code, action)
            except Exception as e:                    # radio busy/resetting: report, keep serving
                self.send_error(503, f"{action} {label} failed: {e}")
                return
            body = {"tap": f"tapped {label}", "down": f"holding {label}",
                    "up": f"released {label}"}[action].encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            st = get_state()
            if self.path.startswith("/display.svg"):
                body = render_svg(st["segments"], st["leds"], hotspots=press is not None).encode()
                ctype = "image/svg+xml"
            elif self.path.startswith("/state.json"):
                body, ctype = json.dumps(st).encode(), "application/json"
            elif self.path.startswith("/codeplug") and codeplug is not None:
                try:
                    cp = bytes(codeplug())
                except Exception as e:
                    self.send_error(503, f"codeplug read failed: {e}")
                    return
                if self.path.startswith("/codeplug.bin"):
                    body, ctype = cp, "application/octet-stream"
                else:
                    from gm600_codeplug import summary
                    body = json.dumps(dict(summary(cp), hex=_hexdump(cp))).encode()
                    ctype = "application/json"
            elif self.path in ("/", "/index.html"):
                codes = " ".join(f'<button onclick="press(\'code={c:02X}\')">{c:02X}</button>'
                                 for c in sorted(KEY_CODES)) if press else "(read-only)"
                extras = []
                if codeplug is not None:
                    extras += ['<a href="/codeplug.bin" download="gm600-codeplug.bin">Download codeplug</a>',
                               '<button onclick="preview()">Preview codeplug</button>']
                if set_mode is not None:
                    extras += ["<button onclick=\"post('/mode?to=trunked')\">Mode: trunked</button>",
                               "<button onclick=\"post('/mode?to=conventional')\">Mode: conventional</button>"]
                extra = f'<div id="x">{" ".join(extras)}</div>' if extras else ""
                body = _PAGE.replace("CODES", codes).replace("EXTRAS", extra).encode()
                ctype = "text/html; charset=utf-8"
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

    srv = http.server.ThreadingHTTPServer((host, port), Handler)
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
