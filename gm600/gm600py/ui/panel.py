"""GM600 web front panel: SVG (assets/make_banner.py) + live page + HTTP server.

Re-exports the display/keypad model (gm600py.firmware.display) so old `gm600_display` users keep
working."""
import argparse
import http.server
import json
import threading

from gm600py.firmware.display import *  # noqa: F401,F403 (model re-exported)
from gm600py.firmware.display import Controller, KEY_CODES, BUTTONS, segments_for_text

# --- SVG ---------------------------------------------------------------------------------------

def _panel():
    """assets/make_banner.py draws the GM600 front panel; its panel_svg() takes a live state."""
    import importlib.util
    import os
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "assets",
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


# --- web UI ------------------------------------------------------------------------------------

_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>GM600 display</title>
<style>body{background:#111;color:#ccc;font:14px monospace;display:flex;flex-direction:column;
align-items:center;margin-top:30px}#d svg{width:min(1100px,96vw);height:auto}
#d .hot:hover{fill-opacity:.12}#k button{background:#2a2c31;color:#ddd;border:1px solid #555;
border-radius:6px;margin:3px;padding:4px 8px;font:13px monospace;cursor:pointer}
#k button:hover,#x button:hover{background:#3a3d44}#s,#p{margin:6px}
#x button,#x a{background:#2a2c31;color:#ddd;border:1px solid #555;border-radius:6px;margin:3px;
padding:4px 8px;font:13px monospace;cursor:pointer;text-decoration:none}#cp{width:min(1100px,96vw);margin-top:10px}
.card{background:#1b1d22;border:1px solid #2f3238;border-radius:10px;padding:14px 16px;margin:10px 0}
.row{display:flex;gap:28px;align-items:flex-end;flex-wrap:wrap}.grow{flex:1}
.lbl{color:#7d828c;font-size:11px;text-transform:uppercase;letter-spacing:.08em}
.big{font-size:30px;color:#7dff8f;font-weight:bold}.mid{font-size:16px;color:#ddd}
.title{color:#ddd;font-size:14px;margin-bottom:8px;font-weight:bold}.dim{color:#6c717a;font-weight:normal}
.badge{display:inline-block;padding:3px 10px;border-radius:12px;font-size:13px;margin-top:4px}
.badge.good{background:#173d22;color:#7dff8f}.badge.bad{background:#4a1a1a;color:#ff7b72}
.good{color:#7dff8f}.bad{color:#ff7b72}.pl{color:#e3b341}.ch{color:#7dff8f;font-weight:bold}
table{border-collapse:collapse;width:100%;font-size:13px}th{color:#7d828c;text-align:left;font-weight:normal;
border-bottom:1px solid #2f3238;padding:5px 8px}td{padding:5px 8px;border-bottom:1px solid #24262b;color:#ccc}
.btn{background:#2a3b2d;color:#7dff8f;border:1px solid #3d6b45;border-radius:6px;padding:6px 12px;text-decoration:none}
.legend{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:8px;font-size:12px;color:#aaa}
.sw{display:inline-block;width:11px;height:11px;border-radius:3px;margin-right:6px;vertical-align:-1px}
.hex{font:12px monospace;line-height:19px}.hr{white-space:nowrap}.addr{color:#5d626b;margin-right:12px}
.asc{color:#5d626b;margin-left:14px}.b{display:inline-block;width:22px;text-align:center;border-radius:3px;color:#aab}
.b:hover{outline:1px solid #fff}.blk0{background:#1d2633}.blk1{background:#262033}.blk2{background:#1f2c25}
.blk3{background:#2e2a1c}.blk4{background:#2b1f24}.b.unit,.sw.unit{background:#1f6f34;color:#fff}
.b.chan,.sw.chan{background:#2b4f7d;color:#fff}.b.cks,.sw.cks{background:#8a6d1a;color:#fff;font-weight:bold}
.b.factory,.sw.factory{background:#3a3d44;color:#ddd}.sw.blk0{background:#33445c}.sw.blk1{background:#4a3c63}
.sw.blk2{background:#35543f}.sw.blk3{background:#5a5133}.sw.blk4{background:#553a45}</style></head>
<body><div id="d"></div><div id="s"></div><div id="p"></div>
<div id="k">raw key codes: CODES</div>EXTRAS<div id="cp"></div><script>
async function post(url){document.getElementById('p').textContent='working ...';
const r=await fetch(url,{method:'POST'});document.getElementById('p').textContent=await r.text()}
const hx=(v,n)=>v.toString(16).toUpperCase().padStart(n||2,'0');
async function preview(){const box=document.getElementById('cp');box.innerHTML='<div class="card">reading codeplug ...</div>';
const j=await (await fetch('/codeplug.json',{cache:'no-store'})).json();
const ok=j.blocks.every(b=>b.ok);
let h=`<div class="card"><div class="row"><div><div class="lbl">Own Unit No.</div><div class="big">${j.own_unit}</div></div>
<div><div class="lbl">Checksums</div><div class="badge ${ok?'good':'bad'}">${ok?'all 5 valid':'MISMATCH'}</div></div>
<div><div class="lbl">Size</div><div class="mid">${j.size} B</div></div>
<div><div class="lbl">EEPROM</div><div class="mid">0x${hx(j.base,4)}-0x${hx(j.base+j.size-1,4)}</div></div>
<div class="grow"></div><a class="btn" href="/codeplug.bin" download="gm600-codeplug.bin">Download .bin</a></div></div>`;
h+=`<div class="card"><div class="title">Conventional channels</div><table><tr><th>Ch</th><th>RX MHz</th><th>TX MHz</th><th>Mode</th><th>RX PL</th><th>TX PL</th><th>Options</th></tr>`;
for(const c of j.channels){const off=(c.tx_mhz-c.rx_mhz);const dup=Math.abs(off)>1e-6;
h+=`<tr><td class="ch">${String(c.channel).padStart(3,'0')}</td><td>${c.rx_mhz.toFixed(5)}</td><td>${c.tx_mhz.toFixed(5)}</td>
<td>${dup?'duplex '+(off>0?'+':'')+off.toFixed(3)+' MHz':'simplex'}</td><td class="${c.rx_pl=='Disabled'?'dim':'pl'}">${c.rx_pl}</td>
<td class="${c.tx_pl=='Disabled'?'dim':'pl'}">${c.tx_pl}</td><td class="dim">${c.options}</td></tr>`}
h+=`</table></div><div class="card"><div class="title">Checksum blocks <span class="dim">(stored = sum(data) - 0x55)</span></div><table>
<tr><th>Block</th><th>Data</th><th>Checksum @</th><th>Stored</th><th>Computed</th><th></th></tr>`;
j.blocks.forEach((b,i)=>{h+=`<tr><td><span class="sw blk${i}"></span>${b.name}</td><td>0x${hx(b.start,4)}-0x${hx(b.end-1,4)} <span class="dim">(${b.end-b.start} B)</span></td>
<td>0x${hx(b.checksum_addr,4)}</td><td>${hx(b.stored)}</td><td>${hx(b.computed)}</td><td>${b.ok?'<span class="good">OK</span>':'<span class="bad">BAD</span>'}</td></tr>`});
h+=`</table></div><div class="card"><div class="title">Hex <span class="dim">(hover a byte)</span></div><div class="legend">`;
const legend=[['unit','Own Unit No.'],['chan','Channel table'],['cks','Checksum byte'],['factory','Factory field']];
for(const [k,n] of legend)h+=`<span><span class="sw ${k}"></span>${n}</span>`;
h+=`</div><div class="hex">`;
const at=a=>{let r=[];for(const g of j.regions)if(a>=g.start&&a<g.end)r.push(g);return r};
for(let row=0;row<j.size;row+=16){h+=`<div class="hr"><span class="addr">${hx(j.base+row,4)}</span>`;
for(let k=0;k<16&&row+k<j.size;k++){const a=j.base+row+k,v=j.bytes[row+k],rs=at(a);
const cls=rs.map(r=>r.kind).join(' ');const tip='0x'+hx(a,4)+' = '+hx(v)+(rs.length?' | '+rs.map(r=>r.name).join(' | '):'');
h+=`<span class="b ${cls}" title="${tip}">${hx(v)}</span>`}
let asc='';for(let k=0;k<16&&row+k<j.size;k++){const v=j.bytes[row+k];asc+=(v>31&&v<127)?String.fromCharCode(v):'.'}
h+=`<span class="asc">${asc.replace(/&/g,'&amp;').replace(/</g,'&lt;')}</span></div>`}
h+='</div></div>';box.innerHTML=h}
if(location.hash=='#codeplug')preview();
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
                    from gm600py.codeplug import summary
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
