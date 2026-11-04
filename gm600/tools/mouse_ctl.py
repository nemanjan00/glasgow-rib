#!/usr/bin/env python3
"""Closed-loop headless mouse control for a QEMU guest via the monitor socket.

Win3.11 cursor is relative; with MouseSpeed=0 in WIN.INI it is 1:1. We still can't
set an absolute position, so we LOCATE the cursor from the framebuffer and converge:
move -> find -> move -> find ... until on target, then click.

find() nudges the cursor and diffs two framebuffers; a blinking text caret is masked
out by first diffing two *static* frames. The arrow hotspot is the top-left-most
changed pixel. PS/2 packets are limited, so moves are chunked to <=100 px/step.

Requires: socat, a running QEMU `-monitor unix:/tmp/qmon`, screendump to PPM (P6).

  mouse_ctl.py find
  mouse_ctl.py move  X Y
  mouse_ctl.py click X Y
  mouse_ctl.py dbl   X Y
"""
import sys, subprocess, time, os, socket

QMON = "/tmp/qmon"; TMP = "/tmp/mctl"; os.makedirs(TMP, exist_ok=True)
W = H = None

def mon(cmds, pace=0.06):
    """Send monitor commands PACED (QEMU's HMP drops bursts of piped lines)."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(QMON); s.settimeout(0.05)
    time.sleep(0.05)
    for c in cmds:
        s.sendall((c + "\n").encode())
        time.sleep(pace)
        try:
            while s.recv(4096): pass
        except socket.timeout:
            pass
    s.close()

def read_ppm(p):
    global W, H
    d = open(p, "rb").read()
    assert d[:2] == b"P6"
    # parse 3 ints after magic
    i = 2; vals = []
    while len(vals) < 3:
        while i < len(d) and d[i] in b" \t\r\n": i += 1
        j = i
        while j < len(d) and d[j] not in b" \t\r\n": j += 1
        vals.append(int(d[i:j])); i = j
    i += 1  # single whitespace after maxval
    W, H = vals[0], vals[1]
    return d[i:i + W * H * 3]

def shot(name):
    mon([f"screendump {TMP}/{name}.ppm"]); time.sleep(0.8)
    return read_ppm(f"{TMP}/{name}.ppm")

def diff_pixels(a, b, thr=90):
    s = set()
    for k in range(0, len(a), 3):
        if abs(a[k]-b[k]) + abs(a[k+1]-b[k+1]) + abs(a[k+2]-b[k+2]) > thr:
            px = (k // 3); s.add((px % W, px // W))
    return s

def moves(dx, dy, step=80):
    out = []
    while dx or dy:
        mx = max(-step, min(step, dx)); my = max(-step, min(step, dy))
        out.append(f"mouse_move {mx} {my}"); dx -= mx; dy -= my
    return out or ["mouse_move 0 0"]

def caret_mask():
    a = shot("c0"); time.sleep(0.6); b = shot("c1")
    return diff_pixels(a, b)              # pixels that change with no mouse motion

def find(mask, nud=24):
    a = shot("fa")
    mon(moves(0, -nud)); time.sleep(0.2)
    b = shot("fb")
    mon(moves(0, nud)); time.sleep(0.15)   # restore: find is non-destructive
    d = diff_pixels(a, b) - mask
    if not d:
        return None
    ymin = min(y for _, y in d)
    xs = [x for x, y in d if y <= ymin + 2]
    return (min(xs), ymin + nud)            # original (pre-nudge) cursor hotspot

def move_to(tx, ty, tol=2, tries=14):
    mask = caret_mask()
    for _ in range(tries):
        cur = find(mask)
        if cur is None:
            print("cursor not found (hidden? restart VM)"); return False
        cx, cy = cur
        dx, dy = tx - cx, ty - cy
        if abs(dx) <= tol and abs(dy) <= tol:
            return True
        mon(moves(dx, dy)); time.sleep(0.15)
    print("warn: did not fully converge; last", cur)
    return True

def press_release(dbl=False):
    mon(["mouse_button 1"]); time.sleep(0.12); mon(["mouse_button 0"]); time.sleep(0.15)
    if dbl:
        time.sleep(0.05); mon(["mouse_button 1"]); time.sleep(0.12); mon(["mouse_button 0"])
    time.sleep(0.3)

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "find"
    if cmd == "find":
        print(find(caret_mask()))
    elif cmd in ("move", "click", "dbl"):
        tx, ty = int(sys.argv[2]), int(sys.argv[3])
        ok = move_to(tx, ty)
        print("at target:", ok, find(set()))
        if ok and cmd == "click": press_release()
        if ok and cmd == "dbl": press_release(dbl=True)
