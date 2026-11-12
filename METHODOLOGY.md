# Reverse-engineering a radio: methodology

How the GM600 was worked out, written generically so it transfers to the next radio. The applet
([README.md](README.md)) is the PHY; this is the process around it. The GM600 is the worked
example throughout — concrete facts live in [`gm600/`](gm600/), and the rig/tool gotchas in
[gm600/SHARP_EDGES.md](gm600/SHARP_EDGES.md).

The whole thing is one loop: **state a hypothesis, run the one cheapest experiment that can
disprove it, let the result decide, update, repeat.** Most steps below are just that loop with a
different instrument. Most lost days on the GM600 came from skipping the cheap instrument, or from
trusting a result that had no positive control (see §9).

## 0. Order of work — cheapest instrument first

1. **Read what already exists**: the project's own docs, the owner's/user manual (UI behaviour,
   button gestures), the service manual (schematic), the vendor tool's help/field list. A user
   manual answered in minutes what four days of codeplug edits didn't (GM600 conventional mode is a
   7-second hold of button A).
2. **Get the PHY working** and prove it with an unchanged read/write round-trip (§2).
3. **Back up every image as soon as you can read it, before any experiment that writes.** Back up
   all of these:
   - the codeplug / EEPROM, raw over the protocol and as the vendor tool's own file (`.rad`/`.per`);
   - the external flash, with a chip programmer off-board, or in-circuit with a read-only RAM stub
     once you have one;
   - CPU-internal memories you can reach (on the GM600, internal RAM `0x0000`–`0x02FF` survives
     power-off and holds state such as the mode).
   - non-volatile CPU configuration and the registers around it: on an HC11 that's CONFIG (an
     EEPROM byte that sets ROM, EEPROM and watchdog enables), plus INIT/INIT2/BPROT/HPRIO. They're
     especially worth recording before bootstrap mode, where they become writable.

   Hash each image, record what state the radio was in, and keep copies off the bench machine. Don't
   overwrite an old backup; a new one gets a new name. The GM600 brick was recoverable only because
   a golden flash image already existed (T48 + `motofix.BIN`). A dated EEPROM dump is also what lets
   you diff what a later experiment changed; an unrecorded one leaves a "3-byte difference" nobody
   can explain.
4. **Capture the vendor tool** reading and writing the radio, *before* deep static work on address
   maps. One sniffed "Read Radio" settled in minutes what three hours of static DPS reversing
   didn't (the codeplug is the 640-byte EEPROM, read as 32-byte blocks).
5. **Write-contrast through the vendor GUI** for anything it can edit (§3). One contrast (Own Unit
   No. 322 → 456) found in two bytes what hours of emulator searching didn't.
6. **Live RAM as an instrument** for behaviour the codeplug doesn't show (§4).
7. **Firmware static + emulator** only for what's left (§5, §6) — and confirm on hardware.
8. **Your own code on the radio** (RAM stubs, §7) when you need to *do* something the firmware
   won't let you.

Ask the owner for one observable ground-truth value early (what the display shows, a channel,
a label) and search for it — it anchors everything.

## 1. Protocol and file format — static, no radio

Get the vendor programming software. If it needs a dead OS, run it in a VM (the GM600 DPS is
16-bit Windows 3.1 NE under QEMU — Wine's WoW64-only builds can't run Win16). Disassemble it (IDA
for NE relocations/far pointers; IDA can't decompile 16-bit NE, r2 + Ghidra `pdg` can) and recover:

- **The serial framing** — opcodes, length/escape rules, checksum. (GM600: an SBEP variant,
  `F0|len` short frames / `FF` long frames, checksum `0xFF − sum`. See [gm600/PROTOCOL.md](gm600/PROTOCOL.md).)
- **The codeplug file format** — any obfuscation/crypto and the field layout. (GM600 `.net`/`.rad`
  use a fixed 7-byte XOR key; `.per` from DPS R00.05.04 is plaintext.)
- **The field dictionary** — vendor help topics and field registries name every field with ranges,
  enums and scope flags (GM600: `HLP_*` in `W_DEALER.EN`, a 40-byte-record registry in WDPS_DP).
  Use scope/flags to predict *where* a field lives before hunting for it.

Then:

- **Known-plaintext pairing**: save the vendor file for the radio and dump the same radio's native
  bytes; solve encodings from the pairs (the frequency formula fell out at once: `AC6B` ↔ 446.06875).
  Don't grep a native image with a file-format map — the radio stores native packed values.
- **Sanity-check encodings against fixed constants** (band edges, calibration tables): a candidate
  step must represent them exactly (a ×10 kHz decode died because 403.625 MHz isn't a multiple).
- **Encoders must round-trip vendor data byte-for-byte**: decode then re-encode what the vendor tool
  wrote (DPS prefers the 5 kHz grid when both fit — a "nicer" choice broke compatibility).
- Print opcodes and addresses **in hex**, always (a decimal `Counter` once invented an opcode `0x19`).

Output: enough to speak the wire and read/write the vendor's own files offline.

## 2. A RIB-less PHY — Glasgow

Implement the wire protocol as Glasgow gateware, not a bit-banged USB-serial dongle. The timing is
then deterministic (see the open-drain / echo / atomic-burst notes in [README.md](README.md)):
messages go out back to back and nothing depends on host or USB timing. **Match the reference
device's behaviour before adding cleverness**: a gateware collision-abort was built and removed —
the RIB sends the whole frame and the host compares the echo. Prove it live by reading the codeplug
and writing it back **unchanged** — a clean round-trip is the first real milestone.

Before the first transmit, **find every gate on the radio's TX path** on the schematic (programming
/ HOOK sense, zeners, enable transistors) and check whether the bus you're on is actually shared
with what you want to watch. Two early wrong calls came from skipping this ("HOOK can stay open" —
the radio then hears but never answers; "sniff shows control-head traffic" — the head is on SPI).

## 3. Capture, then decode fields by write-contrast — the workhorse

**Capture first**: sniff the vendor tool's Read/Write (`socat -x`, or the applet's `sniff`). Then
build a **mock radio** and prove it faithful by replaying the capture byte-for-byte (GM600: 996/996)
— the vendor GUI then runs against the mock with no hardware.

The technique that did most of the work:

1. Change **exactly one** field in the vendor tool.
2. Write Radio (or Save file, if the format is already mapped — no radio needed).
3. Re-dump and `diff` against a baseline dump.

The bytes that moved are that field. Keep the experiment clean:

- **Isolate one variable.** Two edits at once are unreadable.
- **Filter the noise.** Every write also perturbs save/header metadata and a checksum byte or two;
  those deltas are not your field. (GM600: DPS blanks a trailing factory field on every write, and
  NPS re-serialises ~40 bytes on save — vendor edits aren't surgical; diff them.)
- **Checksums:** a net-zero edit (one byte +1, another −1) proves the checksum is *additive* — not its
  range or offset. Get the algorithm from the firmware's validator (GM600: five per-block sums,
  `(Σ − 0x55) & 0xFF`). The vendor tool's *per-block* error messages tell you which block a byte is in.
- **Vendor writes to the mock, then delta-write to the radio**: when the vendor tool can't talk to the
  radio, let it write to the mock, capture, diff against a **fresh live read**, and write only the
  delta. Never blind-replay an old capture — it reverts newer edits.
- The reverse oracle (poke native bits, let the vendor tool read them) is weak: one cross-field
  constraint rejects the whole block, so you can't tell which bit was wrong.

Capture results as a **runnable spec** (Kaitai `.ksy`) plus prose, so "the format" is something a
machine parses, not just English. (GM600: [gm600/gm600_codeplug.ksy](gm600/gm600_codeplug.ksy).)
Once the format is mapped, edit through one safe write path (GM600: `gm600py.codeplug.apply` —
backup, refuse bad checksums, fix checksums, write changed chunks only, read back, reset).

## 4. Live RAM as an instrument

Most radios let the programming protocol read RAM while the firmware runs (GM600: SBEP `0x11` reads
the whole 64 KB logical space). That makes RAM an instrument:

- **RAM-diff two known states.** Dump all of RAM twice in state A and twice in state B; keep bytes
  that are stable within each state and differ between them. GM600 results: the real digit cache
  (display 322 vs 686), the receive flags (no signal / carrier / carrier + correct PL), and the
  trunked/conventional mode bytes (internal RAM, both modes). It ended days of chasing the wrong
  display buffer in one step — **do this before decoding any buffer by hand.**
- **Volatile vs stable**: read 3× in one session, change the UI, read again; then find the firmware
  code that copies it in at boot. Beware commands that reset the radio (GM600 `0x10`) — they confound
  "survives reboot".
- **Check uniqueness over the whole image**, not one region: `322` was unique in EEPROM but also in
  RAM and as BCD in flash.
- **Don't read registers that have read side effects** (status/data registers that clear flags the
  firmware is waiting on — GM600: SPSR/SPDR, the SCI block).
- Turn it into a **live panel** (screen, LEDs, flags) — later experiments become quick to observe.

## 5. Firmware — static

- Get the firmware: read the external flash with a programmer (T48), or dump it in-circuit with a RAM
  stub (§7), and keep a hashed **golden image**.
- **Raw-byte xref scans** of the image for call/load operands (`BD hh ll`, `CE hh ll`, …) find
  dispatchers and buffer users fast — no IDA needed. Banked code makes static xrefs and return
  addresses unreliable: get callers dynamically (§6).
- **Use the exact MCU variant's register map** (the 68HC11**K4** puts the SCI at `0x1070`, not the
  textbook `0x102x`; mislabelled registers produced two wrong conclusions).
- **Identify a table's real consumer** before giving it meaning (a "field→offset map" was GUI layout).
- The firmware's own **self-test** is a pass/fail oracle for any patch or emulator change.

## 6. The emulator as an oracle — and its limits

A cycle-level emulator of the radio CPU that boots the real firmware (GM600: `emu6811`) is the force
multiplier: tracing, snapshots, safe experiments. Rules:

- **Model the inputs from hardware measurements.** Read the real radio's port and A/D registers at
  idle and make them the emulator's defaults. With every input pin reading 0, the GM600 VM sat in a
  channel scan forever and produced a week of invented explanations ("needs RF registration", "number
  is runtime-derived"). **An emulator-negative result is not a firmware fact — suspect unmodelled
  inputs first, and confirm on hardware.**
- **Validate the CPU core** against reference behaviour; one swapped opcode pair (LSRD/ASLD) looked
  like a missing peripheral. Preset non-volatile config registers the real chip has.
- **Lockstep diff** two runs (good vs patched input) to the first divergent PC — found the checksum
  validator in one step. Same PCs ≠ same behaviour: diff SPI output and RAM too.
- **Debugger hooks**: write watchpoints (writer PC + bank), read tracing of a code range, a trace
  callback at a PC to dump args/stack. Faster than reading disassembly.
- **Snapshots + bisection**: deepcopy/pickle after boot; to find which state matters, copy halves of
  the RAM difference between two VM states and warm-reset (GM600 mode = two bytes, either alone does
  nothing — single-byte guesses failed).
- **Port a routine and cross-check it** against the firmware running in the emulator on thousands of
  random inputs before trusting the port.
- **Wire-trace equality**: log every write to the relevant registers for the firmware path and for
  your replacement; identical traces = proof your code reproduces it.
- **Reconstruct a hardware failure from bytes you know you wrote** (the brick: SWI → `BRA *` → COP
  loop) — it proves the recovery before you touch the chip.
- Time: emulated seconds are expensive (GM600 ~80 k instr/s, a 75 s boot). Don't sample a "steady
  state" too early; take snapshots instead of re-booting.
- The emulator is the means; **the real radio is the goal** — deliver and prove on hardware.

## 7. Running your own code on the radio

GM600 route: upload a RAM stub with the write opcode, launch it with the firmware's jump-to-address
backdoor (`0x26`). Rules learned the hard way:

- **Know your free RAM.** The obvious area was the firmware's live message-buffer pool — a 255-byte
  upload crashed the radio. Find RAM that reads zero in every dump and nothing references, keep **one
  ownership table** ([gm600/FIRMWARE.md](gm600/FIRMWARE.md) "Free RAM"), and know which RAM the boot
  clears and which **survives power-off** (internal RAM on a standby supply).
- **The firmware runs between your verify and your jump**: re-verify immediately before launching and
  give every stub a **self-check** (16-bit Fletcher; damaged → spin → watchdog reset).
- **Tier the validation**: a trivial stub first (counter over the serial line proves upload + launch +
  TX + watchdog kick), then the real one, verified against an independent source (dump SHA = T48 read).
- **Launch races**: the radio's own ACK echo can sit in the UART; clear it in the stub and wait a
  moment on the host before the first command.
- **Use the firmware's own routines** from a *non-halting* stub that returns through the handler's
  epilogue — the radio keeps running. GM600: key events posted through the keypad routine, with the
  keypad task **paused by a TCB flag bit** so no real scan reports the release (found empirically in
  the emulator). This beat every "inject the key state" attempt, which failed because keys are a
  stateful, mode-dependent sequence.
- **If you must patch, patch at the input source** (the A/D read), and validate injected == real on
  the same patched binary. Executes-in-place-from-flash CPUs can't be patched in RAM.
- **Flash writes** (bricked once, recovered via T48):
  - trace **all** control pins of the memory (/CE, /OE, /WE) on the schematic before theorising from
    datasheets (the write-protect was `/OE = AND(PA6, …)` released by 12 V on an accessory pin);
  - separate "applied at reset" from "applied while running" for any mode/programming pin (12 V at
    reset = bootstrap mode; after boot = write unlock);
  - **an unexpected before-image means stop** (an "erased" sector read code — the bank math was wrong);
  - verify through an **independent read path** (a buggy stub mis-banks both write and read-back);
  - test the bank math with a **non-zero page** (`0 << 2 == 0` hides the bug);
  - **hash the deployed stub** against the repo before launching — a stale copy nearly re-bricked it;
  - back up the live sector first; get an **independent review** before the first write.

## 8. Hardware signals and RF

For anything analog the codeplug can't give you (repeater COR/PL, flat audio, programming/boot
pins), read the service-manual schematic and find the pins on the audio/signalling IC and the MCU.
(GM600: squelch/PL/flat-audio tap points in [gm600/HARDWARE.md](gm600/HARDWARE.md).) Confirm by
probing for the signal, not by trusting a pin number off a scan. Match documentation to the exact
board/head variant (a "dot-matrix display" came from another head's manual), and watch for
not-fitted (NU) parts that make a connector pin dead.

RF tests: use a band you're licensed for (GM600: ch 004 in 70 cm, not PMR446), verify generated IQ
by demodulating it in software, measure the SDR's frequency offset, and read the radio's receive
state from RAM (§4) rather than eyeballing LEDs. Coax + attenuators beat over-the-air.

## 9. Evidence rules

- **Positive control**: before trusting a negative, show the method detects a known change.
- **Distinctive test patterns**: all-on / all-off alias the boot self-test (the GM600's "888" fooled
  the model about six times). Prove with something asymmetric (e.g. `686`, `HI`) and confirm the code
  is still running *after* the capture.
- **A lost ACK ≠ not written** — the radio may program and then crash. Read back before deciding.
- **Persistence needs a real power-off test** — "a power-cycle reverts it" was wrong (standby RAM).
- **Recompute every quoted number**; spot-check sub-agents' "proven" claims in the disassembly.
- **Liveness = a real reply** (a serial number, data) — never "the socket is up".
- When the owner insists on something they observed, **re-test it on hardware** instead of arguing
  from the emulator. Say "impossible *without X*" and offer X, never just "impossible".
- Don't copy numbers from pasted chats into docs without a source.
- **Tag every claim with its evidence level** wherever it's written down — docs, state file, commit
  messages: **[HW]** measured on the hardware, **[VM]** seen in the emulator only, **[INF]** inferred.
  Several wrong turns came from an inferred claim later being read as established ("the display
  number is runtime-derived", "the mode doesn't survive power-off"). Upgrade a tag only with new
  evidence; when an [INF] or [VM] claim matters for a decision, test it on hardware first.
- **Irreversible steps get a hard gate.** Before anything a reset can't undo (flash write, EEPROM
  write outside the safe write path, hot air, cutting a trace): (1) every assumption it depends on has
  hardware evidence — not emulator, not inference; (2) a verified backup of what you're about to change
  exists (read back and compared); (3) a recovery route is known and available (e.g. off-board
  programmer + golden image). The GM600 brick came from exactly one unchecked assumption ("this sector
  is erased") before an irreversible write.
- **Docs ported from another project start as [INF]**, however confident they read. Re-check each
  claim against files or hardware you have. Porting the DPS file-format notes
  ([gm600/DPS_FILES.md](gm600/DPS_FILES.md)) confirmed the container on 5/5 files and the conventional
  table against the EEPROM, but disproved a "packed config byte" decode and the claim that `.per` is
  encrypted.

## 10. Working with the owner and the tools

- Do GUI driving, web research and bench control yourself (closed-loop mouse tooling, PSU control,
  webcam) — design experiments that need no human eyes; the owner is usually not at the bench.
- Switch instruments early (decompiler, RAM dump, emulator, capture) instead of grinding one.
- Docs record **findings**, not troubleshooting narrative. Update the state file and push as results
  land; deploy to the lab host with git, not file copies.
- Ask before writing to the real radio; re-read the state file after every context compaction.

## Toolbox

| Tool | Use |
|---|---|
| Glasgow | the RIB-less PHY (gateware, deterministic timing) |
| IDA / r2 + Ghidra | disassemble vendor software (NE: decompile with r2 `pdg`) and the radio firmware |
| Kaitai Struct | the codeplug as a runnable, browser-viewable spec |
| QEMU | run dead vendor software (Win3.1 / DOS) |
| `emu6811` | boots the radio firmware: tracing, snapshots, safe experiments (§6) |
| `asm6811` | `.S` RAM stubs with `.cksum16` self-check (§7) |
| Bench PSU with USB control | power-cycles = recovery from a hung stub |
| Webcam | eyes-free proof of display/LED state |
| HackRF | RF test signals (CTCSS) |
| Logic analyzer | passively trace an external bus (ROM, SPI, the serial line) |
| Chip programmer (XGecu T48/T76) | read/write the external flash off-board (golden image, recovery) |
| USBDM | debug access for BDM-era CPUs (HC12/HCS08/…), **not** the HC11 |

## Untested approaches — ideas, not done here

- **Bootstrap mode** (MOD pins at reset → a loader over the SCI): attempted on the GM600, no echo at
  any baud — the CPU clock comes from the audio IC and the boot ROM never enables the TX path; needs a
  scope on the clock pin before retrying. Send single bytes (back-to-back echoes collide on open drain).
- **Reverse the file→radio transform** to pin the trunking/network-block byte offsets.
- **Differential firmware RE** — diff two model variants to find and lift a feature limit.
- **Newer radios** — with BDM (HC12/HCS08 and later), USBDM gives direct memory/debug access.
- **Automate the write-contrast** — script the vendor GUI and the dump+diff in bulk.

## Pitfalls hit along the way

- **Single-client PHY vs an auto-reconnecting client.** The vendor tool (or its VM serial chardev)
  can re-grab a single-client socket between your reads. Symptom: the first read is fine, the next
  fails. Give yourself exclusive access while you read.
- **Kaitai enum names that are YAML booleans** (`yes`/`no`/`on`/`off`), and `doc:` strings with `: `
  — quote them.
- **Coincidental terminators.** A `0xFFFF` after a table was padding from the next structure, not an
  end marker. Prefer a known count over a sentinel.
- **Checksum ranges hidden by padding.** A run of `0xFF` means many candidate range-ends give the
  same sum; settle it with a contrast write just inside vs just outside — or read the validator.
- **Pin numbers off a scanned schematic.** Grid designators are easy to misread by one; verify, and
  tap the accessible end of a net.
- **One variable per experiment.** Treat header, save-metadata and checksum deltas as noise.
- More rig- and tool-specific gotchas: [gm600/SHARP_EDGES.md](gm600/SHARP_EDGES.md).
