# GM600 — live state (re-read this FIRST, especially after a compaction)

Keep this file short and current. Update the **Now** section before finishing each step.
Durable rig/tool details are in `CLAUDE.md`; host-specific access is in the git-ignored
`CLAUDE.local.md`.

## Now
- **DONE (2026-11-11): a `.S` RAM stub writes arbitrary text to the glass** — `tools/lcd_text.S` +
  `tools/lcd_text.py`, cam-proven: pushed "322" over a 686 codeplug with the firmware halted (SBEP
  dead), then HI / HEL / LO / AbC / PSU / 147 / -9- ([images/lcd_text_stub.png](images/lcd_text_stub.png)).
  Radio left at **322** (power-cycled, Own Unit No. restored).
- **Why every earlier attempt failed:** the 3 main digits are **not** in the 21-byte `0x37AC` LCD
  shadow (that's icons/framing, unchanged between 322 and 686 — RAM-diffed). They live on a
  **separate 7-seg controller**: SPI-dispatcher **entry 5 (`0x9018`)**, SPCR `0x52`, CS `0x61` on
  `0x107E`, sends 5 zero bytes + 5 bytes packed from a 5-byte struct `[b0 b1 d0 d1 d2]` (idle
  b0/b1 = `00 40`, d = one 7-seg byte per digit) and caches the struct at **`0x3495`** (so
  `0x3497-99` = the digits). Firmware call: lock `ldd #$245; jsr $9FFB`, push struct ptr, push word,
  `ldd #5; jsr $8F1F`, pull, unlock `$A01A`. The stub skips the lock (pure RTOS semaphore).
- **7-seg font (cam-swept):** bit0 f, bit1 a, bit2 b, bit3 unused, bit4 e, bit5 g, bit6 d, bit7 c.
  Font table in `lcd_text.py`.
- **Next (optional):** a co-resident (non-halting) variant via the free OC pseudo-vectors, so the
  radio keeps running while showing text; drive the `0x37AC` icons with the same stub style.
- **Recover a hung stub with `tools/psu_reboot.py` (PSU), not 0x10.** On the lab host set
  `PYMP305_PATH=~/pymp305/python` if running the script from outside that directory.

## Invariants (never violate)
- **12 V on pin 12 only AFTER boot; off before any power-cycle.** 12 V at reset = CPU bootstrap mode.
- **Radio power = ISDT MP305B bench PSU (12 V / 0.5 A) via pymp305.** The rib applet's `-V 5` does
  NOT power the radio. Reboot = `tools/psu_reboot.py` (this is the ONLY recovery from a hung `sei`
  RAM-stub; `0x10` can't recover one).
- **Flash writes:** deployed stub must have the `page<<2` bank fix; probe must read **1F DA**; test
  writes only to sectors that read **FF LIVE**; **page 3 is never writable**. Always read+back up the
  live sector first.
- **Golden flash = `motofix.BIN`**, SHA-256 `db74c7c88911e9df1f7328e09975ae3a2db5d212be3df74bdeee0dd00aa530b9`
  — verify before use.
- **Display/behaviour claims: verify on HARDWARE.** The emulator has known gaps (does not render the
  Own Unit No. → sees the same 322 for any value).
- Applet `socket` on 127.0.0.1:2324 is **single-client**; kill the socat bridge before direct SBEP.
- Never send SBEP `17/19/20/22/26` to the radio without a reason and a backup (`20/22` touch tuning;
  `26` jumps to a request-supplied address).

## Open questions
- **Emulator 0x0F0B gap:** 0x0F0B/0x0F0C ARE read at boot (pc 9D91, 9E7D) but a real 456 codeplug
  renders identically to 322 in the VM. Where do real vs emulated boots diverge? Likely the
  trunking display-state (RF/synth/ASFIC not modelled) → firmware takes a different display branch.
  Matters because the keypad patch was "validated in the emulator".
