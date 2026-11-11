# GM600 — live state (re-read this FIRST, especially after a compaction)

Keep this file short and current. Update the **Now** section before finishing each step.
Durable rig/tool details are in `CLAUDE.md`; host-specific access is in the git-ignored
`CLAUDE.local.md`.

## Now
- **Current task:** address review — consolidate FIRMWARE.md LCD section, repo hygiene, and the
  `.S`-drives-the-glass goal.
- **Last verified on hardware (2026-11-11):** displayed idle number = MPT1327 **Own Unit No.** =
  codeplug **0x0F0B–0x0F0C** (big-endian 16-bit), block checksum **0x0FF4**. Changed it 322→456 (DPS)
  and 322→712→322 (`tools/setunit.py`, direct SBEP). Radio left at **322**, responding.
- **Next step:** make a RAM stub (`.S`) push an arbitrary 21-byte 0x37AC buffer to the glass. Blocker:
  stub replicates SPCR/CS/0x9102 but the glass stays frozen — CS on 0x107E must be **BSET/BCLR**
  (read-modify-write), not a full-byte store; also check if the firmware's acquire/device-select
  step is required. Decompile 0x8F1F + display paint in IDA, or call the firmware's own refresh.

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
- `.S` push: is the firmware's acquire (0x5013/0x5032, dev 0x0245) required, or is BSET/BCLR CS the
  whole fix? (see Next step)
- Personality checksum byte range (0x0FF4) not fully pinned; network-block checksum byte not located.
