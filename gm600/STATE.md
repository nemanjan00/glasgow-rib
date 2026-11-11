# GM600 — live state (re-read this FIRST, especially after a compaction)

Keep this file short and current. Update the **Now** section before finishing each step.
Durable rig/tool details are in `CLAUDE.md`; host-specific access is in the git-ignored
`CLAUDE.local.md`.

## Now
- **Current task:** GOAL = make a RAM stub (`.S`) write ARBITRARY content to the glass (prove with a
  specific number like 686, NOT 888 — all-segments aliases the boot self-test). Radio left at **322**.
- **PROVEN (2026-11-11):** the `.S` push works. `tools/lab/lcd_disp2.S` (sei-stub, fill 0x37AC=FF,
  push) lit all segments ("888") with **SBEP confirmed dead = firmware halted**, so it was a real
  push, not the self-test. `lcd_disp3.S` pushed the "322" buffer → showed 322. **The missing pieces
  that made the push work: the control-head enable `0x107C` (=0x14 before, 0x10 after) + `0x1024`
  (=0x00/0x40), plus busy-wait delays replacing the firmware's OC4-timed delays. CS on 0x107E via RMW
  (oraa/anda).** The full push: enable 0x1024=0/0x107C=0x14 → dly → cmd (SPCR=0x57, CS|=0x41, send
  `02 50 3F` via 0x9102, CS&=~0x61) → dly → data (SPCR=0x50, CS|=0x01, send 21 bytes, CS&=~0x01) →
  0x107C=0x10/0x1024=0x40 → dly.
- **BLOCKERS to clean arbitrary content (next step):**
  1. The DIGIT segment buffer is **not 0x37AC** on the real radio (0x37AC reads identical for 686 and
     322 — it's icons/fixed; the emulator wrongly models it as the digits). The number source pipeline
     is **ASCII @0x34BD** ("686"/"322") → **per-digit 7-seg @0x3497** (686=`F3 F7 F3`, 322=`E6 76 76`)
     → multiplex → push. The multiplexed buffer pushed for digits is transient/elsewhere, so I can't
     just read a target pattern — need to reverse the 21-byte→segment map by sweeping.
  2. The **segment sweep** is **confounded and the webcam can't verify a push reliably**. Tried:
     own COP-kicking SPI send (`lcd_segstub2.S` `mysend`, replacing 0x9102) per the "make push
     reliable" plan — did NOT fix it. Key observation that breaks the whole approach: with the stub
     **confirmed running (SBEP dead)**, pushing a buffer with only byte8=FF still shows **322**, while
     all-FF showed 888. So either the push is **additive (OR-ing onto the controller's existing
     content, not replacing)** — in which case a partial buffer barely changes 322 and only all-FF
     reaches 888 — OR the all-FF "888" was itself a boot-self-test alias. Three states (self-test 888,
     firmware 322, blank) alias everything, and the SBEP-dead verify is itself flaky. **Conclusion:
     webcam + flaky stub cannot reliably prove arbitrary `.S`-driven content.** To make progress need
     either: a way to CLEAR the controller RAM before a partial push (if additive), or decompile the
     page-4 push (0x5384 caller) to learn the exact command that sets replace-mode/address, or accept
     a non-webcam verification. `lcd_disp2`-style all-FF push is the only semi-repeatable result.
- **Partial 7-seg bit map** (from 0x3497: 6=F3,8=F7,3=E6,2=76): bit7=seg c, bit4=seg e, bit2=seg b,
  bit0=seg f, bit3=unused; {bit6,bit5,bit1}={a,d,g} (need digits 1/4/7 to split). This is the
  per-digit font, NOT the controller multiplex.
- **Recover a hung stub with `tools/psu_reboot.py` (PSU), not 0x10.**

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
- `.S` push: RMW CS (oraa/anda on 0x107E) alone did NOT fix it — the push also writes 0x107C
  (=0x14/0x10), 0x1024, and OC4-timed delays. Decide: replicate all of that in a sei-stub, OR use a
  co-resident OC-ISR that keeps 0x37AC=my buffer and lets the firmware push (preferred — IRQs stay on).
