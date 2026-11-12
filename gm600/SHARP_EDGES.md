# GM600 — sharp edges (rig, link, emulator, tools)

The things that cost time on this radio and bench, so the next session doesn't pay again. Process
lessons are in [../METHODOLOGY.md](../METHODOLOGY.md); live state in [STATE.md](STATE.md).

## Power and link triage

Four separate power conditions: the MP305 PSU **output**, the MP305 **USB** being present (`no device
with VID 0x28E9` = unplugged), the radio's own **power switch**, and **~9 V on HOOK** (programming /
TX enable). Then read the symptom:

| Symptom | Means |
|--|--|
| ~5 mA at 12 V | radio not running (switch, or not booted) |
| no echo at all | PHY/power: radio unpowered, BUS+ or GND missing, line held |
| echo, then no status byte | HOOK missing, firmware not answering, or still booting (wait after power-on) |
| first read fine, then `len=0` / broken pipe | another client stole the single applet slot (`ss -tnp \| grep 2324`) |
| probe `0F CE …` instead of `1F DA FE FE` | 12 V on pin 12 not effective (sagging / dropped out) |
| LCD all segments + all icons (`888`) | the boot self-test — a reset happened (or a mode switch) |

- **Liveness = a real reply** (serial number, data), never "the socket accepted".
- "N frames dropped due to framing errors" from the applet is normal noise.
- HOOK dropping once was a **ground loop** through a second PSU's USB hub.
- **Never raise the PSU current limit** (0.5 A is deliberate: no accidental TX into an antenna).
- `MP305.output_off()` returns stale state on the next read; re-read after a moment.
- pymp305 needs `~/pymp305/.venv-hw/bin/python` and the module path (`cwd ~/pymp305/python` or
  `PYMP305_PATH`) — the system `hid` module is an incompatible package.

## SBEP / applet

- The applet socket is **single-client**: one persistent connection; never a socket per read; stop
  the live panel (`systemctl --user stop gm600live`) or socat bridges before other SBEP tools.
- `0x10` resets the radio (and DPS ends every write with it). A lost `0x84` doesn't mean the write
  failed — **read back**.
- Long-frame vs short-frame offsets differ: advance the parser by `1+n` / `2+n` / `4+n`; anchor reply
  parsing on the full header including the address (the `00` high byte makes off-by-one easy).
- RAM `0x17` writes ACK; very fast racing writes to firmware buffers are unreliable.
- Don't read side-effect registers over `0x11`: SPSR/SPDR `0x1029`–`0x102A`, the SCI block
  `0x1072`–`0x1077`.
- "`0x10` restricts `0x11` to the codeplug" ([DUMP_SEQUENCE.md](DUMP_SEQUENCE.md)) was observed during
  the faulty-flash period — re-verify before relying on it.
- A 256-byte host→stub burst dropped the applet link; send ≤32-byte chunks and read the echo.

## RAM stubs and flash

- Free RAM is `0x3D80`–`0x3FFF` only ([FIRMWARE.md](FIRMWARE.md) ownership table). `0x2000` is the
  firmware's live buffer pool. Internal RAM `0x0000`–`0x02FF` **survives power-off** (mode bytes live
  there) — don't park flags in it.
- Any reset (incl. a trunked↔conventional switch) re-runs the self-test and clears external RAM:
  resident stubs must be re-uploaded.
- After a `0x26` launch, clear the UART (the ACK's echo) and wait ~0.3 s before the first command.
- A hung `sei` stub can only be recovered by a PSU power-cycle (`radio/psu_reboot.py`), not `0x10`.
- CONFIG (`0x103F` = `0xD9`) is a non-volatile EEPROM byte and the firmware leaves it unprotected
  (BPROT `0x40`, PTCON = 0): an EEPROM-programming stub with a wrong address can rewrite it, and in
  bootstrap mode it's freely writable. Keep the recorded value ([FIRMWARE.md](FIRMWARE.md)).
- Accessory pin 12 is both the VPP/12 V write-enable and the GPO1 output (Q0432, ASFIC GCB3):
  **never turn GPO1 on while 12 V is applied** — it shorts the 12 V source. When writing ASFIC control
  bits, leave GCB2 (`B+_CONTROL`, the power latch) set or the radio switches itself off.
- Probing ASFIC image bits (`0x37AC` + `asfic_push.S`) can **reset the radio** (byte 1 bit 2 did, once;
  the ASFIC also makes the CPU clock). It recovered by itself; stop the live panel first and expect to
  restart it (`systemctl --user reset-failed gm600live` before re-running the unit).
- Flash: 12 V on pin 12 **only after boot** (at reset = bootstrap mode); hash the deployed stub against
  the repo; read + back up the live sector first; any unexpected before-image = stop
  ([FLASH_WRITE.md](FLASH_WRITE.md), [RECOVERY.md](RECOVERY.md)).

## Emulator (emu6811)

- 68HC11**K4**: SCI at `0x1072`–`0x1077` (not `0x102x`), TX and RX share SCDR (keep an RX latch);
  re-reading SCDR in one ISR returns the same byte; pace RX ~250 instructions/byte (OC5 inter-byte
  timeout); loop TX back to RX (replies are clocked off the echo); report TDRE always set.
- Preset CONFIG = `0xD9`; `reset()` must restore I/O registers but keep RAM.
- **Inputs**: port pins and both A/D groups default to the bench radio's measured idle levels —
  without them the VM never leaves the channel scan. Re-measure if the hardware changes.
- RX byte delivery stalls if a byte's timer expires while RDRF is set: in tests, run a stub to its
  ready label before feeding RX.
- ~80 k instructions/s, ~75 s boot: snapshot (deepcopy/pickle) instead of re-booting; a `threading.Lock`
  inside a snapshotted object needs `__getstate__`.
- Injected calls need a safe sentinel return address (`0x00F0`; `0x0040` collided with RTOS data).
- Display/behaviour claims from the VM still need hardware confirmation.

## IDA, r2, assembler

- r2 has no 6811 disassembler; IDA MCP disassembles 6811 but has no decompiler. IDA address map:
  CPU `0x8000`–`0xFFFF` = IDA `0x38000+`; a page-N window address = file `N·0x4000 + (cpu & 0x3FFF)`.
- IDA 9.4 Python lacks some helpers (`ida_bytes.create_insn`, `ida_ua.get_item_size`); `next_head`
  can raise at bank addresses. `execute_python` that creates/deletes items modifies the owner's IDB and
  can be blocked by the permission classifier — prefer raw-byte scans and emulator tracing.
- `asm6811`: no `ldy`/`ldd`/`tst`, no `SYM+1` arithmetic, labels need a space before the mnemonic;
  write `.S` files (inline heredoc assembly breaks on shell escaping).

## QEMU / DPS GUI driving

- QEMU HMP drops piped command bursts — pace them; count real QEMU processes with
  `ps -C qemu-system-i38` (two instances sent mouse and screendump to different VMs).
- Key name is `esc`; if the screendump file's mtime didn't change, the command didn't land.
- Hold mouse press/release with a delay; a stuck button or a leftover dialog swallows clicks; close MDI
  children with Ctrl+F4 and start from a screenshot.
- Typed paths in file dialogs lose characters — navigate the tree; DPS saves as `RADIO` (no extension).
- Menu accelerators collide (Alt-F opened the frequency plan); some fields only exist under
  View → Network → Radio Flags ("view only" in DP). Full menu inventory: [DPS_GUI_MAP.md](DPS_GUI_MAP.md).

## Shell, lab host, harness

- Processes started over ssh (nohup/setsid/&) die when the session ends → `systemd-run --user
  --unit=NAME …`; stopping a transient unit deletes it (recreate, don't `start`).
- `pkill -f PATTERN` over ssh kills your own shell when the pattern is in the command line → use
  `pkill -f "[g]m600_live"`, and don't kill + restart the same name in one remote command.
- `socat … fork` piles up children on every reconnect — use a non-forking bridge.
- zsh: an unmatched glob aborts the command (`rm -f x_*.jpg`), arrays are 1-indexed; for remote
  scripts prefer `bash -lc` or a script file. Inline `python3 -c` over ssh: write a file instead.
- Commit messages with `"` break shell quoting — `git commit -F file`.
- Background Python buffers stdout — `python3 -u`. The harness blocks foreground `sleep`; use
  background runs / until-loops.
- Before pushing, `git log origin/main..` — you may publish another session's commits; check for
  serials/PII. Deploy to the lab PC with git push + `git pull --ff-only`, never scp copies.
- Never search the whole home directory (it hangs).

## Web panel / JS

- Python strings carrying JS: `\n` in a non-raw string becomes a real newline inside a JS string
  literal and kills the page — `PageScriptTest` runs `node --check`.
- Every SBEP action costs ~0.5 s: the page decides tap vs hold client-side; a tap is press + explicit
  release (letting the next keypad scan release it stepped scroll keys the wrong way).

## RF (HackRF)

- `hackrf_transfer` takes ~1.5 s to start; a restart right after a stop can silently fail (device busy)
  — check the process is alive, retry, pause between phases.
- The bench HackRF sits ~+2.5 kHz off at 438–446 MHz (`ctcss_tx.py --cal-hz`); VGA 0 was too weak at
  bench distance, VGA 35 works. PMR446 channels carry real traffic — use ch 004 (70 cm, licensed).

## Webcam

- `/dev/video2`, force MJPEG, discard warm-up frames (`-frames:v 15 -update 1`); LCD crop
  `300x150+660+190`, LEDs + LCD `500x150+600+200`. Brighten shots taken with the backlight off.

## Documentation traps

- Match manuals to the exact variant (a 32×96 dot-matrix description was another control head).
- Web sources that block fetches (batlabs 403, motorola.dmz.ro gone, mwc.co.uk TLS) — ask the owner
  to download them.
- Kaitai: quote YAML-boolean enum names and `doc:` strings containing `: `.
