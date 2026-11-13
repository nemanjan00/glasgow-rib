#!/usr/bin/env python3
"""In-circuit AT29C020 flash probe / read / program for the GM600 — ramdump's big sibling.

Where ramdump.py uploads a one-shot RAM stub that streams the flash out and halts, this uploads the
resident command stub flashtool.S (via SBEP 0x17 + the 0x26 backdoor) and then drives it over the
raw serial link: probe the chip, read a sector, program a sector. That's enough to apply (and revert)
the keypad-remote firmware patch (see ../FIRMWARE.md and ../FLASH_WRITE.md).

    python3 flashtool.py probe                      # SAFE: AT29C020 ID + boot-block lockout
    python3 flashtool.py read  0x1000 out.bin       # read the 256-byte sector at a device address
    python3 flashtool.py echo                       # SAFE: 256-byte host->stub->host transport test
    python3 flashtool.py check pl flash.bin         # SAFE: read the pl_patch sectors through the fixed
                                                    #   region (SBEP 0x11) AND the bank window (stub 'R')
    python3 flashtool.py patch [keypad|pl] flash.bin   # apply a patch (keypad = default)
    python3 flashtool.py revert [keypad|pl] flash.bin  # restore the original bytes of its sectors
    python3 flashtool.py check|patch|revert conv ref.bin  # cold start = conventional (1 page-9 sector);
                                                    #   ref.bin = THIS radio's own full flash dump

    --full-backup (patch/revert pl): after the probe, read + save the whole chip in the same stub session
    and abort unless it equals the golden image — the fresh backup without an extra power cycle.

    --attach (any command but check): don't upload/launch — talk to a flashtool stub that is STILL
    RUNNING from an earlier session (the firmware is halted then, so SBEP can't upload anything). Use it
    to re-run or revert after a failed write, before any power-cycle. Liveness = an 'E' echo first.

    keypad: the read_adc override (page 0, 3 sectors). pl: accessory pin 4 = "speaker unmuted"
    (gm600py.firmware.pl_patch; 2 fixed-region sectors 0x38F00 + 0x3DE00). flash.bin = the golden image;
    for pl every live sector must equal the golden's (or already the patched) bytes before a write.
    conv: gm600py.firmware.conv_patch, sector 0x24D00; works on R03.03.21 (0903, ref = motofix.BIN —
    pl_patch doesn't touch page 9) and R03.03.10 (0298, ref = its own full dump). With --full-backup the
    live chip must equal ref except sectors that are a known patch's original-or-patched version.

DANGER — UNTESTED on hardware. No flash write has been done on this radio. `probe` is read-only and
safe; `read` only reads; `patch`/`revert` ERASE AND PROGRAM flash sectors. `patch`/`revert` first
run `probe` and REFUSE unless the chip is an AT29C020 (1F/DA) with the lower boot block UNLOCKED
(0xFE) — because `read_adc` and the patch both live in that block. They also read every sector back
and verify. A botched page-0 write is usually recoverable (boot/SBEP live in the fixed region and
page 1, so the radio still answers and can be re-flashed), but do this deliberately and keep a dump.
"""
import os
import sys
import time

import gm600py.firmware.asm6811 as asm6811
import gm600py.link.sbep as sbep
from gm600py.firmware.keypad_patch import patch_diff, STUB_SITE
import gm600py.firmware.conv_patch as conv_patch
import gm600py.firmware.pl_patch as pl_patch

STUB_S = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flashtool.S")
SECTOR = 256


def build_stub():
    a = asm6811.assemble_file(STUB_S)
    return a.org, a.code


def upload_and_launch(radio, org, code, chunk=32):
    """Upload the stub with 0x17 (verified), then launch it with the 0x26 backdoor."""
    for off in range(0, len(code), chunk):
        addr = org + off
        status, _, _ = radio.transact(sbep.encode(0x17, addr.to_bytes(3, "big") + code[off:off + chunk]))
        if status != sbep.ACK:
            raise sbep.SBEPError(f"stub write @0x{addr:04X} not ACKed ({status:02X})")
    back = bytearray()
    for off in range(0, len(code), 64):
        back += radio.read_block(org + off, min(64, len(code) - off))
    if bytes(back) != code:
        raise sbep.SBEPError("stub read-back mismatch — upload failed")
    frame = sbep.launch_stub(radio, org, code)      # re-verify right before the jump
    _expect(radio, len(frame) + 1, 2.0)             # echo of the 26 frame + one launch ACK
    # Let the stub finish its self-check and clear the ACK's echo from the SCI before the first
    # command: a byte sent too early overruns the receiver and is lost (the probe then times out).
    time.sleep(0.3)
    radio.drain()
    # the stub is now in its command loop


def _expect(radio, n, timeout):
    """Read exactly n raw bytes from the link (used for echoes and fixed-size replies)."""
    buf = bytearray()
    deadline = time.monotonic() + timeout
    radio.sock.settimeout(timeout)
    while len(buf) < n:
        left = deadline - time.monotonic()
        if left <= 0:
            raise sbep.SBEPError(f"timeout: wanted {n} bytes, got {bytes(buf).hex()}")
        radio.sock.settimeout(left)
        try:
            chunk = radio.sock.recv(n - len(buf))
        except OSError:
            raise sbep.SBEPError("link closed")
        if not chunk:
            raise sbep.SBEPError("link closed")
        buf += chunk
    return bytes(buf)


CHUNK = 32          # send in small bursts: the single-wire echoes every TX byte, so a 256-byte blast
#                     fills the applet RX FIFO faster than we drain it and the link drops. Interleaving
#                     send + echo-read in small chunks keeps the FIFO drained.


def _cmd(radio, out, nresp, timeout=3.0):
    """Send raw command bytes in small chunks, swallow each chunk's single-wire echo, return nresp
    response bytes. Chunking avoids the applet link drop on a large (e.g. 256-byte) host->radio burst."""
    for i in range(0, len(out), CHUNK):
        part = out[i:i + CHUNK]
        radio.sock.sendall(part)
        _expect(radio, len(part), timeout)          # drain this chunk's echo before sending more
    return _expect(radio, nresp, timeout) if nresp else b""


def _win(dev_addr):
    """Device address -> (page, window_hi, window_lo) for the stub's R/W commands."""
    page = dev_addr >> 14
    window = 0x4000 | (dev_addr & 0x3FFF)
    return page, (window >> 8) & 0xFF, window & 0xFF


def probe(radio):
    """Return (mfr, device, lower_lockout, upper_lockout)."""
    r = _cmd(radio, b"I", 4)
    return r[0], r[1], r[2], r[3]


def read_sector(radio, dev_addr):
    page, wh, wl = _win(dev_addr & ~0xFF)
    return _cmd(radio, bytes([ord("R"), page, wh, wl]), SECTOR, timeout=4.0)




def echo_test(radio, pattern=None):
    """Send 256 bytes with 'E' and return what the stub sends back (no flash access)."""
    pattern = pattern if pattern is not None else bytes((i * 37 + 11) & 0xFF for i in range(SECTOR))
    return pattern, _cmd(radio, b"E" + pattern, SECTOR, timeout=4.0)


def _backup_live(base, data):
    """Save a live sector's before-image to disk so any write is recoverable. The lack of this is
    exactly why the mis-banked 0xC000 write could not be undone in-circuit (see ../RECOVERY.md)."""
    import gm600py.paths as paths
    fn = os.path.join(paths.backup_dir("flash"), f"sector_{base:05X}_{time.strftime('%Y%m%d_%H%M%S')}.bin")
    with open(fn, "wb") as f:
        f.write(data)
    print(f"  backed up live sector 0x{base:05X} -> {fn}")


def write_sector(radio, dev_addr, data, *, require_erased=False):
    assert len(data) == SECTOR
    base = dev_addr & ~0xFF
    live = read_sector(radio, base)          # ALWAYS read + back up the before-image first
    _backup_live(base, live)
    if require_erased and live != b"\xff" * SECTOR:
        raise sbep.SBEPError(
            f"refusing test write @0x{base:05X}: the LIVE sector is NOT erased "
            f"(starts {live[:8].hex()}); back-up saved. Never trust a file's idea of 'erased' — "
            "this is the mistake that bricked the radio. Use patch/revert for live firmware data.")
    page, wh, wl = _win(base)
    r = _cmd(radio, bytes([ord("W"), page, wh, wl]) + data, 1, timeout=4.0)
    if r != b"\x84":
        raise sbep.SBEPError(f"program @0x{dev_addr:05X}: status {r.hex()}")


def _require_writable(radio):
    mfr, dev, lo, up = probe(radio)
    print(f"probe: mfr={mfr:02X} device={dev:02X} lower-lockout={lo:02X} upper-lockout={up:02X}")
    if (mfr, dev) != (0x1F, 0xDA):
        sys.exit(f"ABORT: not an AT29C020 (expected 1F/DA, got {mfr:02X}/{dev:02X}).")
    if lo != 0xFE:
        sys.exit("ABORT: lower boot block is LOCKED (lockout != FE); the read_adc patch lives there "
                 "and cannot be flashed. Use the hardware keypad-line drive instead.")
    print("lower boot block is programmable.")


def full_backup(radio, golden=None, tolerate=None):
    """Read the whole 256 KB chip through the bank window (1024 sector reads), save it to the backup
    dir, and return it. With golden given, abort unless it matches byte for byte — except sectors in
    tolerate ({base: set of acceptable 256-byte contents})."""
    import gm600py.paths as paths
    img = bytearray()
    t0 = time.time()
    for base in range(0, 0x40000, SECTOR):
        img += read_sector(radio, base)
        if base % 0x4000 == 0x3F00:
            print(f"  page {base >> 14:2d} read ({time.time() - t0:5.0f} s)", flush=True)
    fn = os.path.join(paths.backup_dir("flash"), f"full_{time.strftime('%Y%m%d_%H%M%S')}.bin")
    with open(fn, "wb") as f:
        f.write(img)
    print(f"full chip backup -> {fn}")
    tolerate = tolerate or {}
    bad = [] if golden is None else [b for b in range(0, 0x40000, SECTOR)
                                     if img[b:b + SECTOR] != golden[b:b + SECTOR]
                                     and bytes(img[b:b + SECTOR]) not in tolerate.get(b, ())]
    if bad:
        sys.exit(f"ABORT: the live flash differs from the golden image in {len(bad)} sector(s), first "
                 f"0x{bad[0]:05X}; not writing. Backup kept: {fn}")
    return bytes(img)


def _known_patch_sectors(ref):
    """{sector base: {original, patched}} for every patch that fits ref — what a live chip may differ
    from ref by in a --full-backup comparison (e.g. 0903 = golden + pl_patch)."""
    tol = {}
    for mod in (pl_patch, conv_patch):
        try:
            diff = mod.patch_diff(ref)
        except ValueError:
            continue
        for base, orig, new in diff:
            tol.setdefault(base, set()).update((orig, new))
    return tol


def _apply_pl(radio, revert, golden, backup_first=False, mod=pl_patch):
    """Apply (or revert) a sector patch (pl_patch, or conv_patch with mod=): whole-sector writes in
    the module's safe order (pl: cave sector first on apply, jsr sector first on revert). Refuses
    unless each live sector is exactly the reference original or the patched version.
    backup_first: read + save the whole chip and require it to equal the reference first (sectors of
    other known patches may be in either state)."""
    mfr, dev, lo, up = probe(radio)
    print(f"probe: mfr={mfr:02X} device={dev:02X} lower-lockout={lo:02X} upper-lockout={up:02X}")
    if (mfr, dev) != (0x1F, 0xDA):
        sys.exit(f"ABORT: not an AT29C020 (expected 1F/DA, got {mfr:02X}/{dev:02X}) — is the 12 V on "
                 "pin 12 effective?")
    if backup_first:
        full_backup(radio, golden, _known_patch_sectors(golden))
    steps = mod.patch_diff(golden)                    # safe apply order (pl: cave, then hook)
    if revert:
        steps = steps[::-1]                           # revert: hook first, then the cave
    diff = {base: (orig, new) for base, orig, new in steps}
    order = [base for base, _, _ in steps]
    for base in order:
        orig, new = diff[base]
        want = orig if revert else new
        cur = read_sector(radio, base)
        if cur == want:
            print(f"sector 0x{base:05X}: already correct, skipping")
            continue
        if cur not in (orig, new):
            sys.exit(f"ABORT: live sector 0x{base:05X} is neither the golden original nor the patched "
                     f"version (starts {cur[:8].hex()}); not writing.")
        for attempt in (1, 2):                        # one in-session retry
            print(f"sector 0x{base:05X}: programming (attempt {attempt})...")
            try:
                write_sector(radio, base, want)
                back = read_sector(radio, base)
            except sbep.SBEPError as e:
                back = None
                print(f"  link error: {e}")
                time.sleep(1.5)                       # > the stub's ~0.7 s receive timeout: it drops
                radio.drain()                         # a cut-off command and is back in its loop
            if back == want:
                break
            print(f"  read-back {'mismatch' if back is not None else 'missing'}")
        else:
            verb = "revert" if revert else "patch"
            name = mod.__name__.rsplit(".", 1)[-1].replace("_patch", "")
            sys.exit(f"VERIFY FAILED @0x{base:05X} twice. DO NOT power-cycle: the stub is still running. "
                     f"Retry with `flashtool.py --attach {verb} {name} <ref>`, or undo with "
                     f"`flashtool.py --attach {'patch' if revert else 'revert'} {name} <ref>`; "
                     "if the link is gone for good, the T48 + golden image recover it.")
        print(f"sector 0x{base:05X}: verified.")
    print("done ('revert')." if revert else "done — patch applied; power-cycle (12 V off first).")


def _apply(radio, revert):
    """Apply (or revert) the keypad patch's sector edits, with read-back verify."""
    _require_writable(radio)
    # group patch_diff edits by their 256-byte sector
    sectors = {}
    for off, orig, new in patch_diff():
        for i in range(len(new)):
            a = off + i
            sectors.setdefault(a & ~0xFF, {})[a & 0xFF] = (orig[i] if revert else new[i])
    # Order so the jsr in read_adc never points at a stub that isn't there yet: apply = stub sector
    # (STUB_SITE) first, then the read_adc jsr sites; revert = jsr sites first, then clear the stub.
    stub_sector = STUB_SITE & ~0xFF
    nonstub = sorted(s for s in sectors if s != stub_sector)
    order = (nonstub + [stub_sector]) if revert else ([stub_sector] + nonstub)
    # Safety whitelist: only ever touch the known patch sectors.
    allowed = {stub_sector} | set(nonstub)
    for base in order:
        if base not in allowed:
            sys.exit(f"refusing to program unexpected sector 0x{base:05X}")
        edits = sectors[base]
        cur = bytearray(read_sector(radio, base))
        want = bytearray(cur)
        for boff, val in edits.items():
            want[boff] = val
        if cur == want:
            print(f"sector 0x{base:05X}: already correct, skipping")
            continue
        print(f"sector 0x{base:05X}: programming {len(edits)} byte(s)...")
        write_sector(radio, base, bytes(want))
        back = read_sector(radio, base)
        if bytes(back) != bytes(want):
            sys.exit(f"VERIFY FAILED @0x{base:05X}: read back {bytes(back)[:8].hex()}...; "
                     "the radio may be partly written — re-run after fixing the cause.")
        print(f"sector 0x{base:05X}: verified.")
    print("done ('revert')." if revert else "done — keypad patch applied; press keys with "
          "gm600_keypad_patch.arm()/press()/release().")


def main(argv):
    attach = "--attach" in argv
    backup_first = "--full-backup" in argv
    argv = [a for a in argv if a not in ("--attach", "--full-backup")]
    if attach and len(argv) > 1 and argv[1] == "check":
        sys.exit("check needs the running firmware (SBEP 0x11 reads); it can't --attach")
    if len(argv) < 2 or argv[1] not in ("probe", "read", "echo", "check", "patch", "revert"):
        sys.exit(__doc__)
    which, golden = "keypad", None
    if argv[1] in ("patch", "revert", "check"):
        rest = argv[2:]
        if rest and rest[0] in ("keypad", "pl", "conv"):
            which, rest = rest[0], rest[1:]
        if which == "pl":
            if not rest:
                sys.exit("usage: flashtool.py patch|revert pl <golden flash.bin>")
            import hashlib
            golden = open(rest[0], "rb").read()
            if hashlib.sha256(golden).hexdigest() != pl_patch.GOLDEN_SHA256:
                sys.exit("ABORT: the image given is not the golden motofix.BIN (SHA-256 mismatch).")
        if which == "conv":
            if not rest:
                sys.exit("usage: flashtool.py check|patch|revert conv <this radio's full flash dump>")
            import hashlib
            golden = open(rest[0], "rb").read()
            h = hashlib.sha256(golden).hexdigest()
            if not any(h.startswith(k) for k in conv_patch.KNOWN):
                sys.exit("ABORT: the reference image is not one conv_patch was checked against "
                         f"({', '.join(conv_patch.KNOWN.values())}).")
            try:
                if conv_patch.state(golden) != "original":
                    sys.exit("ABORT: the reference image is already patched; give the unpatched dump.")
            except ValueError as e:
                sys.exit(f"ABORT: {e}")
    org, stub = build_stub()
    with sbep.Radio() as radio:
        fixed = {}
        if argv[1] == "check" and which == "pl":    # fixed-region view, while the firmware still runs
            for base, _, _ in pl_patch.patch_diff(golden):
                cpu = 0x8000 + (base - 0x38000)
                fixed[base] = b"".join(radio.read_block(cpu + o, 64) for o in range(0, SECTOR, 64))
        if attach:
            time.sleep(1.5)                           # let a cut-off command time out in the stub (~0.7 s)
            radio.drain()
            sent, got = echo_test(radio)              # liveness, no flash access
            if got != sent:
                sys.exit("--attach: the stub didn't echo 256 bytes back; not continuing")
            print("attached to the running flashtool stub (echo OK)")
        else:
            print(f"serial {radio.id_string(1)}; uploading {len(stub)}-byte stub, launching...")
            upload_and_launch(radio, org, stub)
        cmd = argv[1]
        if cmd == "probe":
            mfr, dev, lo, up = probe(radio)
            print(f"mfr={mfr:02X} device={dev:02X} lower-lockout={lo:02X} upper-lockout={up:02X}  "
                  f"({'AT29C020' if (mfr,dev)==(0x1F,0xDA) else 'UNKNOWN'}; lower block "
                  f"{'programmable' if lo==0xFE else 'LOCKED' if lo==0xFF else '?'})")
        elif cmd == "echo":
            sent, got = echo_test(radio)
            bad = [i for i in range(SECTOR) if sent[i] != got[i]]
            print(f"echo: {SECTOR - len(bad)}/{SECTOR} bytes back intact"
                  + (f"; first mismatch at {bad[0]}: sent {sent[bad[0]]:02X} got {got[bad[0]]:02X}" if bad else ""))
            if bad:
                raise SystemExit(1)
        elif cmd == "check" and which == "conv":
            (base, orig, new), = conv_patch.patch_diff(golden)
            win = read_sector(radio, base)
            st = "unpatched (ref original)" if win == orig else "PATCHED" if win == new else "UNEXPECTED"
            print(f"sector 0x{base:05X}: bank window page {base >> 14} (stub R) = {st}")
            if st == "UNEXPECTED":
                raise SystemExit(1)
        elif cmd == "check":
            ok = True
            for base, orig, new in pl_patch.patch_diff(golden):
                win = read_sector(radio, base)
                state = lambda b: "golden original" if b == orig else "PATCHED" if b == new else "UNEXPECTED"
                print(f"sector 0x{base:05X}: fixed region (0x11) = {state(fixed[base])}, "
                      f"bank window page {base >> 14} (stub R) = {state(win)}, "
                      f"views {'identical' if win == fixed[base] else 'DIFFER'}")
                ok &= win == fixed[base] and state(win) != "UNEXPECTED"
            if not ok:
                raise SystemExit(1)
        elif cmd == "read":
            if len(argv) < 4:
                sys.exit("usage: flashtool.py read <dev_addr_hex> <out.bin>")
            addr = int(argv[2], 0)
            data = read_sector(radio, addr)
            open(argv[3], "wb").write(data)
            print(f"read sector 0x{addr & ~0xFF:05X} ({SECTOR} B) -> {argv[3]}")
        elif which == "pl":
            _apply_pl(radio, revert=(cmd == "revert"), golden=golden, backup_first=backup_first)
        elif which == "conv":
            _apply_pl(radio, revert=(cmd == "revert"), golden=golden, backup_first=backup_first,
                      mod=conv_patch)
        else:
            _apply(radio, revert=(cmd == "revert"))
    print("power-cycle the radio now (the stub left it halted).")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
