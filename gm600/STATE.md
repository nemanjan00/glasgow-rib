# GM600 — live state (re-read this FIRST, especially after a compaction)

Keep this file short and current. Update the **Now** section before finishing each step.
Durable rig/tool details are in `CLAUDE.md`; host-specific access is in the git-ignored
`CLAUDE.local.md`.

## Now
- **2026-11-13 16:30: BENCH RADIO SWAPPED → 807IYA0298, firmware R03.03.10 (stock, NOT pl_patch; not the
  R03.03.21 all addresses/stubs were built for — no returning stubs / live panel on it).** [HW] Backed up before
  anything: EEPROM, full 256 KB flash (`full_0298_R03.03.10_20261113_164210.bin`, sha256 `4250be5f…`, matches an
  independent 0x11 fixed-region dump), CONFIG D9 / INIT2 00 / BPROT 40 / HPRIO 26 (same as 0903). Codeplug
  version 02.03 (same). Ch 1 programmed = RU726 (439.075/431.475, PL 123.0 TX+RX, options 0x08) [HW, read back];
  ch 4 = 438.625/431.025 TX PL 123.0. Booted in trunk mode. Purpose: find which of 0903/0298 has a broken TX
  (the good-TX one becomes the repeater TX radio, which needs no pl_patch). Optional later: update 0298 to
  R03.03.21+pl_patch keeping its EEPROM (needs a whitelist unlock in flashtool — not built yet).
  The 0903 notes below describe the radio that is now OFF the bench.
- **Repeater pair (2026-11-13) [HW]:** both radios ch 1 = RX 431.475 / TX 439.075, PL 123.0 TX+RX (repeater side of RU726). 0903 = RX station (pl_patch, pin 4 -> PTT), 0298 = TX station. 0298 TX timeout 10 -> 90 s via DPS (`0x0F29`). Both radios come up TRUNKED after a long power-off (~hours) and after a DPS write; seconds-long PSU cycles keep conventional.
- **Repeater WORKS end to end (2026-11-13) [HW]:** HackRF 431.475 + 123.0 Hz voice -> 0903 decodes -> pin 4 keys 0298 (pin 3) -> 0298 transmits 439.075 with the audio. Audio path: 0903 pin 16 (speaker +, 6.2 V DC) -> 10 uF (+ to pin 16) -> pot -> 0298 pin 2. 0903 ch 1 Data = No (Data = Yes makes pin 11 live but keeps the speaker amp muted, so the pl_patch pin 4 never switches). Open: CW ID, UPS / conventional-at-boot (both come up trunked after long power loss), optional PL-flag-driven pin 4 so Data = Yes + flat pin 11 can be used.
- **conv_patch ready, NOT flashed (2026-11-13):** cold start -> conventional ch 1 instead of trunked. Cause found: mode block 0x84-0x88 + check byte 0x89 (0xAA+sum); bad check -> defaults with 0x85 = 01 (trunked) at page 9 0x4D99. Patch = file 0x24D9A 01->FF + XOR compensation 0x24D31 FC->02, one sector 0x24D00, same in R03.03.21 and R03.03.10 [VM: cold boot '  1', retained trunked still trunked, stub whitelist]. Flash: `flashtool.py --full-backup patch conv <ref>` (0903 ref = motofix.BIN, 0298 ref = full_0298_R03.03.10_20261113_164210.bin), 12 V on pin 12 after boot (user), then `check conv`, power-cycle, and a cold-start test (the mode block must fail its check: e.g. write 0x89 wrong over SBEP + reset, or a long power-off).
Evidence tags: **[HW]** measured on the radio · **[VM]** seen in the emulator only · **[INF]** inferred /
unverified. Untagged = process or tooling, not a claim about the radio. Keep tagging new claims.

- **Radio: ON, conventional, channel `  1` = RU726, running the `pl_patch` firmware** (2026-11-13) [HW]: accessory pin 4
  = PL-decoded output, standalone (0.618 V while 123.0 Hz PL decoded, ~12 V otherwise; carrier without PL
  ignored). Flash = golden + pl_patch (FLASH_WRITE.md "Done"); full pre-write backup on the lab PC. Live panel
  running (`gm600live`, `http://127.0.0.1:8600/`). 12 V is OFF pin 12.
- **Channels (2026-11-13, `setchan.py`) [HW, read back]:** ch 1 = **RU726** RX 439.075 / TX 431.475, PL 123.0 TX+RX;
  ch 2 446.13125, ch 3 446.09375 (PMR, no PL); ch 4 438.625 / 431.025 PL 123.0 (HackRF test channel).
  Ch 1 HackRF-tested on 439.075 [HW]: carrier alone = busy only; +123.0 Hz = PL decoded + pin 4 0.614 V; off = 12 V.
- **Code layout:** `gm600/gm600py/` package; `tools/*.py` are compatibility shims; tests in
  `gm600/tests/`. Backups → `~/.local/share/gm600/` (or `$GM600_BACKUP_DIR`). Deploy to the lab PC with
  git (push here, `git pull --ff-only` in `~/glasgow-rib` there).
- **Mode = two internal-RAM bytes** `0x0085` (FF conv / 01 trunk) + `0x0089` (bit 1 conv / bit 2 trunk)
  [HW: both directions via writes + `0x10`; minimal set found by bisection in the VM]. Internal RAM
  `0x0000`–`0x02FF` survives a PSU power-off [HW: radio booted back conventional, EEPROM unchanged];
  the mechanism is the HC11 standby supply (VSTBY) [INF — not traced on the schematic]. External RAM is
  cleared at boot [HW: stubs at `0x3D80` gone after a reset].
- **Remote keys** (`keyhold.py`, live panel): pause keypad task `0x11D8 |= 0x10`, post the event via
  `0x4D8B` with the non-halting `keyevt.S` [HW: long-hold A → conventional; scroll taps step channels].
  Button names for the 11 key codes [VM — derived from what each code does; not yet checked by pressing
  the real buttons].
- **RX flags:** `0x347F` = carrier, `0x347E` = PL decoded / RX active [HW: RAM diff + live polls; ch 001
  and ch 004]. Carrier without/with wrong PL → PORTH `0x107C` bit 3 high + red "busy" LED [HW].
- **HackRF squelch test** on ch 004 (438.625 MHz, RX PL 123.0 set via `setpl.py`) [HW: 3/3 clean].
  The bench HackRF's +2.5 kHz offset [HW, measured indirectly: decode works at +2.5 kHz, marginal at 0].
- **Display:** digits = entry-5 controller, struct at `0x3495` [HW]; `0x37AC` is NOT icons — it is the ASFIC image (squelch-unmute bits follow PL decode [HW], PGMENAB = decoder Y2); controller
  = start bit + 33 data bits [HW: model reproduces 79 + 12 photos]; MC145453-class part [INF].
- **Open / next:**
  - confirm the VM-derived key names on the bench (hold each button, read `0x33FC`–`0x33FE`);
  - the RECOVERY.md "3-byte" EEPROM difference — which bytes, and why [unknown; not the mode switch, HW];
  - a channel once stepped by itself during real PMR traffic [HW observation; RF into the keypad ladders
    is a hypothesis, INF — test by logging AN0–AN2 while keying a transmitter nearby];
  - coax + attenuators for the RF link.
  - **PL-decoded output on accessory pin 4 — DONE, flashed, standalone [HW, 2026-11-13].** Pin 4 = ASFIC GCB1 =
    NOT GCB0 (speaker unmuted) via `pl_patch`. Note: Monitor / a carrier-squelch channel also unmute → pin 4 on.
    Possible follow-up: PL-strict variant. Image bits 1:2 and 4:1 reset the radio.

## Invariants (never violate)
- **Irreversible steps have a hard gate** (flash write, EEPROM write outside `codeplug.apply`, hot air,
  anything a reset can't undo): every assumption it depends on has [HW] evidence (not [VM]/[INF]),
  a verified backup exists, and a recovery route is known and available. Otherwise don't do it.
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
