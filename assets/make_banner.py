"""Generate assets/banner.svg: a vector redraw of the GM600 front panel (after the line drawing
in the GM600 user guide), with the LCD reading "rib"."""
import sys

W, H = 1400, 560          # canvas
OX, OY = 7, 30            # radio drawing offset (source drawing is ~1386 x 394)

out = []
def e(s): out.append(s)

# seven-segment digit; segments a..g; returns svg polygons
def seg_polys(x, y, w, h, t):
    m = y + h / 2
    k = t / 2
    def hseg(x0, x1, yc):
        return [(x0 + k, yc), (x0 + t, yc - k), (x1 - t, yc - k), (x1 - k, yc), (x1 - t, yc + k), (x0 + t, yc + k)]
    def vseg(xc, y0, y1):
        return [(xc, y0 + k), (xc + k, y0 + t), (xc + k, y1 - t), (xc, y1 - k), (xc - k, y1 - t), (xc - k, y0 + t)]
    sk = 6  # italic skew in px over full height
    def skew(pts):
        return [(px + sk * (1 - (py - y) / h), py) for px, py in pts]
    return {
        "a": skew(hseg(x, x + w, y + k)),
        "b": skew(vseg(x + w - k, y, m)),
        "c": skew(vseg(x + w - k, m, y + h)),
        "d": skew(hseg(x, x + w, y + h - k)),
        "e": skew(vseg(x + k, m, y + h)),
        "f": skew(vseg(x + k, y, m)),
        "g": skew(hseg(x, x + w, m)),
    }

def poly(pts, cls):
    return f'<polygon class="{cls}" points="{" ".join(f"{px:.1f},{py:.1f}" for px, py in pts)}"/>'

GLYPHS = {"r": "eg", "i": "c", "b": "cdefg"}

e(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" font-family="Helvetica, Arial, sans-serif">')
e('<title>glasgow-rib: a Motorola GM600 front panel with "rib" on its display</title>')
e('''<defs>
  <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#0d1117"/><stop offset="1" stop-color="#1b2433"/>
  </linearGradient>
  <linearGradient id="body" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#3a3d42"/><stop offset="0.55" stop-color="#26282c"/><stop offset="1" stop-color="#1a1b1e"/>
  </linearGradient>
  <linearGradient id="panel" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#2e3136"/><stop offset="1" stop-color="#1e2024"/>
  </linearGradient>
  <linearGradient id="btn" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#4a4e55"/><stop offset="1" stop-color="#2a2c31"/>
  </linearGradient>
  <linearGradient id="lcd" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#0f2a12"/><stop offset="1" stop-color="#081a0a"/>
  </linearGradient>
  <filter id="glow" x="-30%" y="-30%" width="160%" height="160%">
    <feGaussianBlur stdDeviation="5" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
  <filter id="soft" x="-10%" y="-10%" width="120%" height="140%">
    <feGaussianBlur in="SourceAlpha" stdDeviation="16"/><feOffset dy="14" result="o"/>
    <feComponentTransfer><feFuncA type="linear" slope="0.55"/></feComponentTransfer>
    <feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
</defs>
<style>
  .edge { fill: none; stroke: #55595f; stroke-width: 2; }
  .hl { fill: none; stroke: #6b7078; stroke-width: 1.5; opacity: 0.7; }
  .seg-on { fill: #7dff8f; }
  .seg-off { fill: #1d3a20; }
  .lbl { fill: #d7dbe0; font-weight: bold; }
  .slot { fill: #121315; stroke: #3d4046; stroke-width: 1.5; }
</style>''')

e(f'<rect width="{W}" height="{H}" rx="24" fill="url(#bg)"/>')
e(f'<g transform="translate({OX},{OY})">')

# radio body
e('<g filter="url(#soft)">')
e('<rect x="8" y="15" width="1370" height="370" rx="62" fill="url(#body)" stroke="#5c6067" stroke-width="3"/>')
e('</g>')
e('<rect x="20" y="26" width="1346" height="348" rx="54" class="hl"/>')
# left recess and section dividers
e('<path d="M 70 40 L 70 360" class="edge"/>')
e('<path d="M 858 26 L 858 374" class="edge"/>')

# volume rocker
e('<ellipse cx="135" cy="110" rx="80" ry="46" fill="url(#btn)" stroke="#6b7078" stroke-width="2.5"/>')
e('<line x1="135" y1="70" x2="135" y2="150" stroke="#1a1b1e" stroke-width="4"/>')
e('<path d="M 88 103 L 102 117 L 116 103" fill="none" stroke="#d7dbe0" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>')
e('<path d="M 154 117 L 168 103 L 182 117" fill="none" stroke="#d7dbe0" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>')

# mic connector (RJ-style)
e('<rect x="40" y="198" width="152" height="152" rx="22" fill="#1c1d20" stroke="#6b7078" stroke-width="2.5"/>')
e('<rect x="62" y="218" width="108" height="112" rx="8" fill="#0b0c0d" stroke="#45484e" stroke-width="2"/>')
for i in range(10):
    x = 74 + i * 9
    e(f'<rect x="{x}" y="232" width="5" height="26" rx="1" fill="#c9a227"/>')
e('<path d="M 92 288 L 92 312 L 140 312 L 140 288" fill="none" stroke="#45484e" stroke-width="3"/>')

# centre panel
e('<rect x="228" y="48" width="634" height="296" rx="44" fill="url(#panel)" stroke="#5c6067" stroke-width="2.5"/>')
e('<rect x="300" y="60" width="550" height="182" rx="30" fill="#1b1d21" stroke="#4a4e55" stroke-width="2"/>')

# LEDs (green / yellow / red)
for cy, col in ((88, "#3fb950"), (136, "#e3b341"), (184, "#f85149")):
    e(f'<rect x="246" y="{cy - 13}" width="44" height="26" rx="13" fill="{col}" opacity="0.9" filter="url(#glow)"/>')
    e(f'<rect x="246" y="{cy - 13}" width="44" height="26" rx="13" fill="none" stroke="#111" stroke-width="2"/>')

# scroll up/down
for y0, up in ((76, True), (166, False)):
    e(f'<rect x="318" y="{y0}" width="50" height="64" rx="22" fill="url(#btn)" stroke="#6b7078" stroke-width="2"/>')
    if up:
        e(f'<path d="M 333 {y0 + 40} L 343 {y0 + 24} L 353 {y0 + 40} Z" fill="#d7dbe0"/>')
    else:
        e(f'<path d="M 333 {y0 + 24} L 343 {y0 + 40} L 353 {y0 + 24} Z" fill="#d7dbe0"/>')

# LCD
e('<rect x="412" y="68" width="286" height="150" rx="14" fill="url(#lcd)" stroke="#0a0a0a" stroke-width="3"/>')
e('<rect x="418" y="74" width="274" height="138" rx="10" fill="none" stroke="#24502a" stroke-width="1.5"/>')
# side icon boxes and arrows (unlit, as on the drawing)
for y in (88, 110, 132, 154):
    e(f'<rect x="426" y="{y}" width="22" height="14" rx="2" fill="none" stroke="#2c5a31" stroke-width="1.5"/>')
    e(f'<rect x="660" y="{y}" width="22" height="14" rx="2" fill="none" stroke="#2c5a31" stroke-width="1.5"/>')
for x in (446, 548, 668):
    e(f'<path d="M {x - 9} 190 L {x + 9} 190 L {x} 202 Z" fill="none" stroke="#2c5a31" stroke-width="1.5"/>')
# digits
digits = []
for i, ch in enumerate("rib"):
    polys = seg_polys(458 + i * 66, 86, 52, 98, 11)
    for name, pts in polys.items():
        digits.append(poly(pts, "seg-on" if name in GLYPHS[ch] else "seg-off"))
e('<g>' + "".join(p for p in digits if "seg-off" in p) + '</g>')
e('<g filter="url(#glow)">' + "".join(p for p in digits if "seg-on" in p) + '</g>')
# home call icon on one arrow lit, for life
e('<path d="M 668 160 L 680 160 L 680 168 L 668 168 Z" fill="#7dff8f" opacity="0.0"/>')

# CLR and call buttons
e('<ellipse cx="790" cy="108" rx="42" ry="27" fill="url(#btn)" stroke="#6b7078" stroke-width="2.5"/>')
e('<text x="790" y="115" font-size="20" text-anchor="middle" class="lbl" font-family="Georgia, serif">CLR</text>')
e('<ellipse cx="790" cy="186" rx="42" ry="27" fill="url(#btn)" stroke="#6b7078" stroke-width="2.5"/>')
e('<path d="M 774 186 L 806 186 M 777 186 L 780 176 L 800 176 L 803 186 M 774 193 L 806 193" fill="none" stroke="#d7dbe0" stroke-width="2.5" stroke-linecap="round"/>')

# bottom row: power, divider, A-D
e('<circle cx="304" cy="294" r="26" fill="url(#btn)" stroke="#6b7078" stroke-width="2.5"/>')
e('<circle cx="304" cy="294" r="7" fill="none" stroke="#d7dbe0" stroke-width="2.5"/>')
e('<rect x="356" y="262" width="7" height="64" rx="3" fill="#121315"/>')
for i, ch in enumerate("ABCD"):
    cx = 440 + i * 120
    e(f'<rect x="{cx - 46}" y="270" width="92" height="50" rx="25" fill="url(#btn)" stroke="#6b7078" stroke-width="2.5"/>')
    e(f'<text x="{cx}" y="303" font-size="24" text-anchor="middle" class="lbl">{ch}</text>')

# speaker area with curved boundary
e('<path d="M 880 330 C 930 230, 960 160, 1060 150 L 1340 140" class="edge"/>')
e('<path d="M 890 26 C 890 150, 890 300, 880 374" class="hl"/>')
# badge (no logo: model name instead)
e('<rect x="1015" y="58" width="306" height="70" rx="35" fill="#2a2c31" stroke="#6b7078" stroke-width="2.5"/>')
e('<text x="1168" y="104" font-size="30" text-anchor="middle" fill="#e6edf3" font-weight="bold" letter-spacing="6">GM600</text>')
# grille slots
rows = [(165, [(1005, 1310)]), (190, [(970, 1050), (1085, 1312)]),
        (232, [(958, 1312)]), (256, [(920, 1005), (1035, 1314)]),
        (298, [(918, 1314)]), (322, [(886, 966), (995, 1314)])]
for y, segs in rows:
    for x0, x1 in segs:
        e(f'<rect x="{x0}" y="{y - 7}" width="{x1 - x0}" height="14" rx="7" class="slot"/>')

e('</g>')

# caption
e(f'<text x="{W / 2}" y="482" font-size="46" font-weight="bold" text-anchor="middle" fill="#e6edf3">glasgow-rib</text>')
e(f'<text x="{W / 2}" y="524" font-size="21" text-anchor="middle" fill="#9198a1">The Motorola RIB, rebuilt as Glasgow gateware. No RIB box, no RS-232, no DOS PC.</text>')
e('</svg>')

open(sys.argv[1], "w").write("\n".join(out) + "\n")
