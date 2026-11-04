#!/usr/bin/env python3
"""Generate codeplug-map.svg and dps-session.svg in this folder.

Run from anywhere: python3 gm600/assets/make_diagrams.py
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))

STYLE = """<style>
  .bg { fill: #ffffff; stroke: #d0d7de; }
  .ink { fill: #1f2328; }
  .muted { fill: #59636e; }
  .line { stroke: #8c959f; fill: none; }
  .bound { fill: #2da44e; }
  .partial { fill: #d4a72c; }
  .readonly { fill: #6e7781; }
  .unknown { fill: #d0d7de; }
  .factory { fill: #cf222e; }
  .cks { fill: #8250df; }
  .host { fill: #ddf4ff; stroke: #0969da; }
  .radio { fill: #f6f8fa; stroke: #6e7781; }
  .tx { stroke: #0969da; }
  .txt { fill: #0969da; }
  .rx { stroke: #1a7f37; }
  .rxt { fill: #1a7f37; }
  .reset { fill: #fff8c5; stroke: #9a6700; }
  .resett { fill: #9a6700; }
  .wr { stroke: #cf222e; }
  .wrt { fill: #cf222e; }
  .sep { stroke: #ffffff; }
  @media (prefers-color-scheme: dark) {
    .bg { fill: #0d1117; stroke: #30363d; }
    .ink { fill: #e6edf3; }
    .muted { fill: #9198a1; }
    .line { stroke: #6e7681; }
    .bound { fill: #3fb950; }
    .partial { fill: #d29922; }
    .readonly { fill: #8b949e; }
    .unknown { fill: #30363d; }
    .factory { fill: #f85149; }
    .cks { fill: #a371f7; }
    .host { fill: #0c2d4a; stroke: #58a6ff; }
    .radio { fill: #161b22; stroke: #8b949e; }
    .tx { stroke: #58a6ff; }
    .txt { fill: #58a6ff; }
    .rx { stroke: #3fb950; }
    .rxt { fill: #3fb950; }
    .reset { fill: #3b2e0a; stroke: #d29922; }
    .resett { fill: #d29922; }
    .wr { stroke: #ff7b72; }
    .wrt { fill: #ff7b72; }
    .sep { stroke: #0d1117; }
  }
</style>"""


def codeplug_map():
    W, H = 1000, 520
    X0, X1 = 40, 960                  # bar spans radio 0x0D80..0x1000
    BY, BH = 150, 54                  # bar y, height
    px = lambda a: X0 + (a - 0x0D80) * (X1 - X0) / 0x280

    regions = [  # start, end (exclusive), class, label, sublabel, label side
        (0x0D80, 0x0E13, "readonly", "Radio / alignment", "serial, softpot, band grid", "up"),
        (0x0E13, 0x0F08, "partial", "Network (MPT1327)", "fields enumerated, few bound", "up"),
        (0x0F08, 0x0F2B, "bound", "MPT config", "own unit no., timers, power", "down"),
        (0x0F2B, 0x0F67, "bound", "Own groups", "15 × 4 B", "up"),
        (0x0F67, 0x0F83, "bound", "Channels", "4 × 7 B", "down"),
        (0x0F83, 0x0F9A, "bound", "Shortform", "BCD dial strings", "up"),
        (0x0F9A, 0x0FF4, "unknown", "Undecoded personality", "", "down"),
        (0x0FF4, 0x0FF5, "cks", "Checksum", "0x0FF4", "up"),
        (0x0FF5, 0x0FFC, "factory", "Factory field", "DPS blanks it", "down"),
        (0x0FFC, 0x1000, "unknown", "", "", None),
    ]
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" font-family="Helvetica, Arial, sans-serif">',
         '<title>GM600 codeplug: the 640-byte EEPROM at 0x0D80-0x0FFF, by block and decode status</title>', STYLE,
         f'<rect class="bg" x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="16"/>',
         '<text class="ink" x="40" y="44" font-size="20" font-weight="bold">The GM600 codeplug: 640 bytes of CPU EEPROM</text>',
         '<text class="muted" x="40" y="68" font-size="13.5">Radio addresses 0x0D80–0x0FFF, read as 20 × 32-byte blocks. Width is to scale.</text>']
    # DPS write bracket
    wx0, wx1 = px(0x0E13), px(0x0FFC)
    o.append(f'<path class="line" d="M {wx0:.1f} 128 L {wx0:.1f} 120 L {wx1:.1f} 120 L {wx1:.1f} 128" stroke-width="1.5"/>')
    o.append(f'<text class="muted" x="{(wx0 + wx1) / 2:.1f}" y="113" font-size="12" text-anchor="middle">DPS writes 0x0E13–0x0FFB</text>')
    rx1 = px(0x0E13)
    o.append(f'<path class="line" d="M {X0} 128 L {X0} 120 L {rx1 - 4:.1f} 120 L {rx1 - 4:.1f} 128" stroke-width="1.5"/>')
    o.append(f'<text class="muted" x="{(X0 + rx1) / 2:.1f}" y="113" font-size="12" text-anchor="middle">never written</text>')

    up_y, down_y = [96, 88], [262, 300]
    ups = downs = 0
    for a, b, cls, label, sub, side in regions:
        x, w = px(a), max(px(b) - px(a), 2.5)
        o.append(f'<rect class="{cls}" x="{x:.1f}" y="{BY}" width="{w:.1f}" height="{BH}"/>')
        if a != 0x0D80:
            o.append(f'<line class="sep" x1="{x:.1f}" y1="{BY}" x2="{x:.1f}" y2="{BY + BH}" stroke-width="1.5"/>')
        if label == "Undecoded personality":
            o.append(f'<text class="ink" x="{x + w / 2:.1f}" y="{BY + 31}" font-size="12.5" font-weight="bold" text-anchor="middle">undecoded</text>')
            continue
        if not side:
            continue
        cx = x + w / 2
        if side == "up":
            # labels for the wide blocks sit inside the bar; narrow ones get a leader line
            if w > 150:
                o.append(f'<text x="{cx:.1f}" y="{BY + 24}" font-size="13.5" font-weight="bold" text-anchor="middle" fill="#ffffff">{label}</text>')
                o.append(f'<text x="{cx:.1f}" y="{BY + 42}" font-size="11.5" text-anchor="middle" fill="#ffffff" opacity="0.9">{sub}</text>')
            else:
                ly = 232 if ups % 2 == 0 else 252
                ly = {"Own groups": 240, "Shortform": 240, "Checksum": 240}.get(label, ly)
                o.append(f'<line class="line" x1="{cx:.1f}" y1="{BY + BH}" x2="{cx:.1f}" y2="{ly - 14}" stroke-width="1.2"/>')
                o.append(f'<text class="ink" x="{cx:.1f}" y="{ly}" font-size="12.5" font-weight="bold" text-anchor="middle">{label}</text>')
                o.append(f'<text class="muted" x="{cx:.1f}" y="{ly + 15}" font-size="11" text-anchor="middle">{sub}</text>')
                ups += 1
        else:
            ly = {"MPT config": 300, "Channels": 300, "Factory field": 300}[label]
            off = {"MPT config": -10, "Channels": 0, "Factory field": -40}[label]
            o.append(f'<line class="line" x1="{cx:.1f}" y1="{BY + BH}" x2="{cx + off:.1f}" y2="{ly - 14}" stroke-width="1.2"/>')
            o.append(f'<text class="ink" x="{cx + off:.1f}" y="{ly}" font-size="12.5" font-weight="bold" text-anchor="middle">{label}</text>')
            if sub:
                o.append(f'<text class="muted" x="{cx + off:.1f}" y="{ly + 15}" font-size="11" text-anchor="middle">{sub}</text>')
            downs += 1
    # address ticks
    for a in (0x0D80, 0x0E13, 0x0F08, 0x0F67, 0x0FF4, 0x1000):
        x = px(a)
        lab = f"0x{a:04X}" if a != 0x1000 else "0x0FFF"
        anchor = "start" if a == 0x0D80 else "end" if a == 0x1000 else "middle"
        if a in (0x0F67, 0x0FF4):
            continue
        o.append(f'<text class="muted" x="{x:.1f}" y="{BY - 6}" font-size="10.5" text-anchor="{anchor}" font-family="Menlo, Consolas, monospace">{lab}</text>')

    # channel record zoom
    zy = 350
    o.append(f'<text class="ink" x="40" y="{zy}" font-size="14" font-weight="bold">One channel record (7 bytes, 4 of them from 0x0F67)</text>')
    fields = [("Rx freq", 2, "BE16"), ("Tx freq", 2, "BE16"), ("Tx PL", 1, "index"), ("Rx PL", 1, "index"), ("Options", 1, "bits")]
    x, cw = 40, 92
    for name, n, enc in fields:
        w = cw * n
        o.append(f'<rect class="bound" x="{x}" y="{zy + 14}" width="{w - 4}" height="44" rx="4"/>')
        o.append(f'<text x="{x + (w - 4) / 2}" y="{zy + 34}" font-size="13" font-weight="bold" text-anchor="middle" fill="#ffffff">{name}</text>')
        o.append(f'<text x="{x + (w - 4) / 2}" y="{zy + 50}" font-size="11" text-anchor="middle" fill="#ffffff" opacity="0.9">{enc}</text>')
        x += w
    o.append(f'<text class="muted" x="40" y="{zy + 80}" font-size="12">Frequency words: f = 375 MHz + (v &amp; 0x7FFF) × (bit 15 ? 6.25 : 5 kHz).   Options: bit 0 Data · bits 2:1 Tx inhibit · bit 3 Power · bit 4 Monitor</text>')

    # legend
    ly = 490
    items = [("bound", "decoded and bound"), ("partial", "enumerated, partly bound"), ("readonly", "factory, read-only"),
             ("unknown", "not decoded"), ("cks", "checksum"), ("factory", "factory field")]
    x = 40
    for cls, text in items:
        o.append(f'<rect class="{cls}" x="{x}" y="{ly - 11}" width="14" height="14" rx="3"/>')
        o.append(f'<text class="muted" x="{x + 20}" y="{ly}" font-size="12">{text}</text>')
        x += 20 + len(text) * 6.6 + 22
    o.append('</svg>')
    return "\n".join(o) + "\n"


def dps_session():
    W, H = 1000, 560
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" font-family="Helvetica, Arial, sans-serif">',
         '<title>DPS read and write sessions with a GM600, as captured through the applet</title>', STYLE,
         '<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
         '<path d="M0,0 L10,5 L0,10 z" fill="context-stroke"/></marker></defs>',
         f'<rect class="bg" x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="16"/>',
         '<text class="ink" x="40" y="44" font-size="20" font-weight="bold">A DPS session with a GM600, as captured on the wire</text>',
         '<text class="muted" x="40" y="68" font-size="13.5">Every frame is echoed back first (not drawn). ACK is 50. Write = a full read, then the write steps below.</text>']

    def column(x0, title, steps):
        hx, rx = x0 + 60, x0 + 330
        o.append(f'<text class="ink" x="{x0 + 195}" y="104" font-size="16" font-weight="bold" text-anchor="middle">{title}</text>')
        o.append(f'<rect class="host" x="{hx - 50}" y="116" width="100" height="32" rx="6" stroke-width="1.5"/>')
        o.append(f'<text class="txt" x="{hx}" y="137" font-size="13" font-weight="bold" text-anchor="middle">Host / DPS</text>')
        o.append(f'<rect class="radio" x="{rx - 50}" y="116" width="100" height="32" rx="6" stroke-width="1.5"/>')
        o.append(f'<text class="ink" x="{rx}" y="137" font-size="13" font-weight="bold" text-anchor="middle">GM600</text>')
        o.append(f'<line class="line" x1="{hx}" y1="148" x2="{hx}" y2="{H - 30}" stroke-width="1.2" stroke-dasharray="4 4"/>')
        o.append(f'<line class="line" x1="{rx}" y1="148" x2="{rx}" y2="{H - 30}" stroke-width="1.2" stroke-dasharray="4 4"/>')
        y = 178
        for kind, text, note in steps:
            if kind == "reset":
                o.append(f'<rect class="reset" x="{rx - 70}" y="{y - 16}" width="140" height="26" rx="5" stroke-width="1.2"/>')
                o.append(f'<text class="resett" x="{rx}" y="{y + 1}" font-size="12" font-weight="bold" text-anchor="middle">{text}</text>')
                if note:
                    o.append(f'<text class="muted" x="{hx + 10}" y="{y + 1}" font-size="11.5">{note}</text>')
                y += 38
                continue
            cls, tcls = {"tx": ("tx", "txt"), "wr": ("wr", "wrt"), "rx": ("rx", "rxt")}[kind]
            if kind == "rx":
                o.append(f'<line class="{cls}" x1="{rx - 4}" y1="{y}" x2="{hx + 4}" y2="{y}" stroke-width="2" marker-end="url(#ah)"/>')
            else:
                o.append(f'<line class="{cls}" x1="{hx + 4}" y1="{y}" x2="{rx - 4}" y2="{y}" stroke-width="2" marker-end="url(#ah)"/>')
            o.append(f'<text class="{tcls}" x="{(hx + rx) / 2}" y="{y - 6}" font-size="12.5" font-weight="bold" text-anchor="middle" font-family="Menlo, Consolas, monospace">{text}</text>')
            if note:
                o.append(f'<text class="muted" x="{rx + 14}" y="{y + 4}" font-size="11.5">{note}</text>')
            y += 34 if kind != "rx" else 40

    column(30, "Read", [
        ("tx", "10", "enter prog. mode"),
        ("rx", "50", ""),
        ("reset", "radio resets", "wait for it to boot"),
        ("tx", "23 01", "ID query"),
        ("rx", "50 · 8B 01 \"807IYA0903\"", ""),
        ("tx", "13", "block size"),
        ("rx", "50 · 82 00 20 00", ""),
        ("tx", "11 20 00 0D 80 …", "× 20, to 0x0FE0"),
        ("rx", "50 · 80 00 0D 80 + 32 B", "640 B in all"),
    ])
    column(505, "Write (after a full read)", [
        ("tx", "23 · 23 · 13", "queries again"),
        ("rx", "50 …", ""),
        ("wr", "17 00 0E 13 + 32 B", "× 15, to 0x0FD3"),
        ("rx", "50 · 84", "84 OK, 85 fail"),
        ("wr", "17 00 0F F3 + 2 B", "incl. checksum"),
        ("tx", "13", ""),
        ("wr", "17 00 0F F5 + 7 B", "factory field → FF"),
        ("tx", "10", "restart"),
        ("reset", "radio resets", ""),
    ])
    o.append('</svg>')
    return "\n".join(o) + "\n"


for name, fn in (("codeplug-map.svg", codeplug_map), ("dps-session.svg", dps_session)):
    with open(os.path.join(HERE, name), "w") as f:
        f.write(fn())
    print("wrote", name)
