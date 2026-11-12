#!/usr/bin/env python3
"""In-circuit AT29C020 flash probe / read / program for the GM600 — ramdump's big sibling.

Where ramdump.py uploads a one-shot RAM stub that streams the flash out and halts, this uploads the
resident command stub flashtool.S (via SBEP 0x17 + the 0x26 backdoor) and then drives it over the
raw serial link: probe the chip, read a sector, program a sector. That's enough to apply (and revert)
the keypad-remote firmware patch (see ../FIRMWARE.md and ../FLASH_WRITE.md).

    python3 flashtool.py probe                      # SAFE: AT29C020 ID + boot-block lockout
    python3 flashtool.py read  0x1000 out.bin       # read the 256-byte sector at a device address
    python3 flashtool.py patch flash.bin            # apply the keypad patch (reads/writes 2 sectors)
    python3 flashtool.py revert flash.bin           # restore the original bytes of those 2 sectors

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
from gm600py.firmware.keypad_patch import patch_diff

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


BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools", "flash_backups")


def _backup_live(base, data):
    """Save a live sector's before-image to disk so any write is recoverable. The lack of this is
    exactly why the mis-banked 0xC000 write could not be undone in-circuit (see ../RECOVERY.md)."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    fn = os.path.join(BACKUP_DIR, f"sector_{base:05X}_{time.strftime('%Y%m%d_%H%M%S')}.bin")
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
    if len(argv) < 2 or argv[1] not in ("probe", "read", "patch", "revert"):
        sys.exit(__doc__)
    org, stub = build_stub()
    with sbep.Radio() as radio:
        print(f"serial {radio.id_string(1)}; uploading {len(stub)}-byte stub, launching...")
        upload_and_launch(radio, org, stub)
        cmd = argv[1]
        if cmd == "probe":
            mfr, dev, lo, up = probe(radio)
            print(f"mfr={mfr:02X} device={dev:02X} lower-lockout={lo:02X} upper-lockout={up:02X}  "
                  f"({'AT29C020' if (mfr,dev)==(0x1F,0xDA) else 'UNKNOWN'}; lower block "
                  f"{'programmable' if lo==0xFE else 'LOCKED' if lo==0xFF else '?'})")
        elif cmd == "read":
            if len(argv) < 4:
                sys.exit("usage: flashtool.py read <dev_addr_hex> <out.bin>")
            addr = int(argv[2], 0)
            data = read_sector(radio, addr)
            open(argv[3], "wb").write(data)
            print(f"read sector 0x{addr & ~0xFF:05X} ({SECTOR} B) -> {argv[3]}")
        else:
            _apply(radio, revert=(cmd == "revert"))
    print("power-cycle the radio now (the stub left it halted).")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
