#!/usr/bin/env python3
"""Press GM600 front-panel keys over the wire, by a tiny firmware patch that overrides the keypad A/D.

The keypad is a 6-key analog resistor ladder read through the A/D. There is no clean software way to
synthesise a key event (the firmware re-samples the pin every poll and dispatches keys as RTOS
messages through a mode-dependent state machine — see docs/FIRMWARE.md). So instead of faking the
event, this overrides its SOURCE: the one instruction in `read_adc` that reads the keypad channel.

`read_adc` (CPU 0x50DF, flash page 0 = file 0x10DF) reads ADR1 at CPU 0x50FE with `ldab ADR1`
(`F6 10 31`). We replace those 3 bytes with `jsr 0x5E25` and drop a 13-byte stub into free flash at
CPU 0x5E25 (file 0x1E25, an 8.6 KB run of 0xFF in page 0, reachable by a direct jsr since read_adc
runs in page 0). The stub returns a host-controlled value from RAM when armed, else the real ADR1:

    0x5E25  ldaa FORCE_EN; cmpa #$A5; bne real   ; armed only when both magic bytes match
            ldaa FORCE_M2; cmpa #$5A; bne real
            ldab FORCE_VAL; rts                  -> armed: ignore the real button, use our value
    real:   ldab ADR1; rts                       -> disarmed: the real keypad, patch is transparent

With the patch in place the WHOLE real keypad path runs for a host-supplied level, so a press behaves
exactly like a finger (validated in the emulator: a FORCE-injected key is byte-identical to a physical
press on the patched firmware). While armed the stub ignores the real line, so a real finger and the
remote can't fight. Press / release are just RAM writes (SBEP 0x17); reverting the flash bytes gives
the buttons back.

  * FORCE_EN  = 0x3FF0   armed only when == 0xA5 (magic 1); anything else = real buttons
  * FORCE_VAL = 0x3FF1   the A/D level for the key to inject (same value the key's ladder gives)
  * FORCE_M2  = 0x3FF2   armed only when == 0x5A (magic 2)

The override engages only when BOTH magic bytes match, so power-up garbage RAM arms it ~1-in-65536
instead of a coin flip (a patched radio with no host attached boots and keys work normally). The
host calls arm() after connecting to clear the control bytes, then press()/release(), and must disarm
when it exits (even on errors). The bytes are in external RAM that the boot RAM test clears, so a
reset or power cycle always disarms; internal RAM (0x0000-0x02FF) is NOT safe for this — it is
retained across power-off. If this is ever flashed: test arm -> power-cycle -> real keys work.

Applying the patch to a real radio needs an AT29C020 in-system sector write (apply + revert); this
module produces the patched image and the exact byte diff for a flash writer. The press/release
helpers work against whatever is running the patched firmware (real radio or the VM bridge).
"""
import sys

PATCH_SITE = 0x10FE                       # flash file offset of `ldab ADR1` in read_adc
PATCH_ORIG = bytes.fromhex("F61031")      # ldab ADR1
PATCH_NEW = bytes.fromhex("BD5E25")       # jsr  0x5E25
STUB_SITE = 0x1E25                        # flash file offset for the stub (CPU 0x5E25, page 0)
# Magic-gated override (asm6811 of kstub.S): return FORCE_VAL only when BOTH magic bytes match,
# else the real ADR1. The two magics make power-up garbage RAM arm the patch ~1-in-65536 instead of
# a coin flip, so a patched radio with no host attached boots and keys work normally.
#   ldaa FORCE_EN; cmpa #$A5; bne real; ldaa FORCE_M2; cmpa #$5A; bne real; ldab FORCE_VAL; rts
#   real: ldab ADR1; rts
STUB = bytes.fromhex("b63ff081a5260bb63ff2815a2604f63ff139f6103139")

# RAM control bytes in EXTERNAL RAM (0x3FF0-0x3FF2): unreferenced by the firmware, zero in every RAM
# dump, and cleared by the boot RAM test - so an armed override can never survive a reset or a power
# cycle. (They used to be at 0x028B-0x028D, in the CPU's internal RAM, which is retained across power-off
# by the standby supply; and 0x028C turned out to be firmware state that changes with the radio mode.)
FORCE_EN, FORCE_VAL, FORCE_M2 = 0x3FF0, 0x3FF1, 0x3FF2
MAGIC1, MAGIC2 = 0xA5, 0x5A
RELEASED = 0xFF                           # idle A/D level (no key)


def patch_image(flash):
    """Return a patched copy of a 256 KB flash image. Raises if the sites don't look right."""
    buf = bytearray(flash)
    if bytes(buf[PATCH_SITE:PATCH_SITE + 3]) != PATCH_ORIG:
        raise ValueError("read_adc site 0x%05X is not `ldab ADR1` (got %s); wrong image?"
                         % (PATCH_SITE, bytes(buf[PATCH_SITE:PATCH_SITE + 3]).hex()))
    if any(b != 0xFF for b in buf[STUB_SITE:STUB_SITE + len(STUB)]):
        raise ValueError("stub area 0x%05X is not free (0xFF); wrong image?" % STUB_SITE)
    buf[PATCH_SITE:PATCH_SITE + 3] = PATCH_NEW
    buf[STUB_SITE:STUB_SITE + len(STUB)] = STUB
    return bytes(buf)


def patch_diff():
    """The flash byte changes to apply the patch, as (file_offset, original, patched) tuples.
    Reverting is the same tuples with original/patched swapped."""
    return [(PATCH_SITE, PATCH_ORIG, PATCH_NEW),
            (STUB_SITE, b"\xFF" * len(STUB), STUB)]


# ---- press/release against a running patched radio (SBEP 0x17 writes) ----

def _w(radio, addr, value):
    from gm600py.link.sbep import encode, ACK
    status, op, _ = radio.transact(encode(0x17, addr.to_bytes(3, "big") + bytes([value])))
    if status != ACK or op != 0x84:
        raise IOError("write 0x%04X=%02X: status %02X op %s" % (addr, value, status, op))


def arm(radio):
    """Put the patched radio in a known released state (call once after connecting, to clear any
    garbage the control bytes powered up with)."""
    _w(radio, FORCE_EN, 0)
    _w(radio, FORCE_M2, 0)
    _w(radio, FORCE_VAL, RELEASED)


def press(radio, level):
    """Hold a key: `level` is the A/D value that key's ladder produces (characterise per radio).
    Both magic bytes must be set for the override to engage; write FORCE_EN last so it's atomic."""
    _w(radio, FORCE_VAL, level)
    _w(radio, FORCE_M2, MAGIC2)
    _w(radio, FORCE_EN, MAGIC1)


def release(radio):
    _w(radio, FORCE_EN, 0)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--patch":
        src = open(sys.argv[2], "rb").read()
        out = sys.argv[3] if len(sys.argv) > 3 else sys.argv[2] + ".patched"
        open(out, "wb").write(patch_image(src))
        print("patched image -> %s" % out)
        for off, a, b in patch_diff():
            print("  flash 0x%05X: %s -> %s" % (off, a.hex(), b.hex()))
    else:
        print("usage: gm600_keypad_patch.py --patch <flash.bin> [out.bin]", file=sys.stderr)
        print("       (import for arm()/press()/release() against a running patched radio)",
              file=sys.stderr)
        sys.exit(2)
