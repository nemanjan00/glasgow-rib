# GM600 — live state (re-read this FIRST, especially after a compaction)

Keep this file short and current. Update the **Now** section before finishing each step.
Durable rig/tool details are in `CLAUDE.md`; host-specific access is in the git-ignored
`CLAUDE.local.md`.

## Now
- **Radio: ON, trunked `322`, with `gm600_live.py` running on the lab PC** as the systemd user unit
  `gm600live` (from `~/glasgow-rib/gm600/tools`, `http://127.0.0.1:8600/` there; logs
  `journalctl --user -u gm600live`). It holds the single SBEP slot — `systemctl --user stop gm600live`
  before other SBEP tools. Long-running lab processes must be `systemd-run --user` units: plain
  `nohup`/`setsid` ones are killed when the ssh session ends. Power off: MP305 `output_off()`.
- **Deploy to the lab PC with git**, not scp: push here, `git pull --ff-only` in `~/glasgow-rib` there
  (now current; only lab-only `tools/lab/lcd_disp.S`, `lcd_e8.S`, `lcd_segstub.S` untracked).
- **Conventional mode survives power-off** (RAM retained across main power, EEPROM unchanged) —
  corrected in FAQ/FIRMWARE.
- **Latest (2026-11-12): remote conventional + tone squelch + RX flags — DONE, pushed (`953e4cc`).**
  - `tools/keyhold.py` = remote key hold over SBEP: pause keypad task (`0x11D8 |= 0x10`), post the
    event through firmware `0x4D8B` with the non-halting stub `keyevt.S` (launched by `0x26`, returns
    via `0x4CE6`). Default: long-hold A → conventional channel `1`. RAM only; conventional latch is RAM
    state (no EEPROM write), so a power-cycle returns to trunked.
  - **RX flags (RAM, input-state array `0x3460+event`): `0x347F` = carrier, `0x347E` = PL decoded /
    RX active.** Carrier without/with wrong PL: PORTH `0x107C` bit 3 high + red "busy" LED.
  - HackRF on the lab PC: `tools/ctcss_tx.py` (446.06875 MHz, PL 123.0, VGA 35, `--cal-hz 2500`) and
    `tools/squelch_test.py` (logs RX flags/LEDs/display). Link is marginal (~1 in 3 tone phases missed).
  - To repeat: power on → `keyhold.py` → `squelch_test.py`.
- **Also this session (2026-11-11/12):** arbitrary text on the glass (`lcd_text.S` via firmware,
  `lcd_raw.S` standalone; controller = start bit + 33 data bits; segment atlas in
  `images/lcd_segments/`); emulator boots to the real idle screen (port pins + A/D modelled) with a live
  web UI (`gm600_vm_bridge.py --ui`, clickable keys/LEDs); keypad decoded (3 A/D ladders, 11 keys).
- **Next (optional):** confirm the VM-derived key names on the bench (hold each button, read
  `0x33FC`–`0x33FE`); dump the codeplug before/after a hand long-hold of A to close the RECOVERY.md
  "3-byte" note; a stronger/cleaner RF link (amp, attenuator, cable) for the squelch test.

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
- **Display/behaviour claims: verify on HARDWARE.** The emulator now boots like the radio (port pins +
  A/D modelled, `VmBootTest`), but RF/synth/ASFIC are still not modelled.
- **RAM stubs load at `0x3D80`** (`lcd_raw.S` `0x3E40`, `keyevt.S` `0x3D80`), never `0x2000` (live
  firmware buffer pool); halting stubs self-check (`.cksum16`); launch via `sbep.launch_stub`.
- Applet `socket` on 127.0.0.1:2324 is **single-client**; kill the socat bridge before direct SBEP.
- Never send SBEP `17/19/20/22/26` to the radio without a reason and a backup (`20/22` touch tuning;
  `26` jumps to a request-supplied address).

## Open questions
- **Emulator 0x0F0B gap — CLOSED (2026-11-11).** The VM sat in a channel scan (cycling numbers, green
  LED flashing) because every CPU input pin read 0 and every A/D channel read 0xFF. `emu6811` now
  models PORTA/B/C/D/E/F/G/H input pins and both A/D groups with the bench radio's idle levels (read
  live over SBEP). The VM then boots to the idle own-ident screen exactly like the radio: 322 for the
  bench codeplug, 456 for a 456 codeplug, green LED flashing, backlight timing out. Test `VmBootTest`.
