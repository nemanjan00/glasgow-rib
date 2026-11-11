#!/usr/bin/env python3
"""On-hardware validation of the flashtool write path and the page<<2 bank fix.

This is the regression that proves, on a real radio, that a "page 13" write lands at page 13
(device 0x34000) and NOT at page 3 (device 0xC000) — the bug that bricked a radio (see
../RECOVERY.md). It is SAFE: it writes only to page 13, which is erased in the golden image,
`require_erased` enforces that it is erased live, every write is auto-backed-up, and the test
restores the sector to 0xFF (golden) at the end. It also reads page 3 before and after and asserts
it is unchanged.

PRECONDITIONS (same as any in-circuit flash write — see ../FLASH_WRITE.md):
  1. Radio booted normally (12 V OFF at reset, or you get bootstrap mode instead of firmware).
  2. THEN apply ~12 V on accessory pin 12 (releases flash /OE) and leave it on.
  3. Run this. The probe must read 1F DA FE FE; if it reads 0F CE... the 12 V isn't effective.
  4. Afterwards the radio is halted (the stub took over) — remove 12 V and power-cycle.

    python3 flash_write_test.py
"""
import sys
import flashtool
import sbep

TEST = 0x34000          # page 13 — erased in golden; require_erased enforces it live
PAGE3 = 0xC000          # the old bug's wrong target; must stay unchanged
PATTERN = bytes((i ^ 0x5A) & 0xFF for i in range(256))


def main():
    org, stub = flashtool.build_stub()
    with sbep.Radio() as radio:
        print("serial:", radio.id_string(1))
        flashtool.upload_and_launch(radio, org, stub)
        mfr, dev, lo, up = flashtool.probe(radio)
        print(f"probe: {mfr:02X} {dev:02X} {lo:02X} {up:02X}  (expect 1F DA FE FE)")
        if (mfr, dev) != (0x1F, 0xDA):
            sys.exit("ABORT: probe is not an AT29C020 — 12 V on pin 12 is not effective "
                     "(0F CE... = /OE still held low). No write attempted.")

        c3_before = flashtool.read_sector(radio, PAGE3)
        print("0x34000 live all-FF:", flashtool.read_sector(radio, TEST) == b"\xff" * 256)

        flashtool.write_sector(radio, TEST, PATTERN, require_erased=True)
        rb = flashtool.read_sector(radio, TEST)
        c3_after = flashtool.read_sector(radio, PAGE3)
        ok_write = rb == PATTERN
        ok_bank = c3_after == c3_before
        print("0x34000 write+readback == pattern :", ok_write)
        print("0xC000 (page 3) UNTOUCHED        :", ok_bank, "<-- proves the page<<2 bank fix")

        flashtool.write_sector(radio, TEST, b"\xff" * 256)      # restore to golden
        ok_restore = flashtool.read_sector(radio, TEST) == b"\xff" * 256
        print("0x34000 restored to FF           :", ok_restore)

        ok = ok_write and ok_bank and ok_restore
        print("\n*** HW BANK-FIX TEST:", "PASS" if ok else "FAIL", "***")
        print("radio is halted — remove 12 V and power-cycle.")
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
