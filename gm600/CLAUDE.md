# GM600: notes for agents

GM600-only facts. Generic applet notes are in the top-level `CLAUDE.md`. Start with `README.md`
in this folder; it indexes the docs and has the current status.

## Lab rig — what you have at your disposal (READ THIS FIRST after a compaction)

Everything is on lab host **`nemanjan00@10.69.1.91`** (passwordless sudo). This repo lives there as
`~/glasgow-rib` (= local `~/rib`, git-synced; push `git push origin master:main`). The bench radio
is GM600 serial **807IYA0903**. You can do the ENTIRE loop headless — power, program, and *see* the
screen — without the owner present:

1. **Radio POWER = ISDT MP305B bench PSU, driven by `pymp305`** — set to **12 V / 0.5 A** (radio
   draws ~0.25 A running). **This is the only thing that powers the radio; the applet's `-V 5` is
   just the rib logic reference and does NOT power it.** Reboot = `~/captures/psu_reboot.py`
   (output off→on, keeps V/I). Run with its venv: `cd ~/pymp305/python && ~/pymp305/.venv-hw/bin/python …`.
   API: `from pymp305 import MP305; with MP305.open() as psu: psu.output_off()/output_on()/read_state()`.
   **This is your reliable recovery** when a `sei` RAM-stub hangs the radio (see below).
2. **SBEP serial to the radio = Glasgow rib applet** on `127.0.0.1:2324` (tmux session `applet`:
   `glasgow run rib -V 5 --bus A0 --baud 9600 socket tcp:127.0.0.1:2324`). **Single client.**
   Restarting it (`tmux send-keys -t applet C-c` then re-run) glitches the rib but is NOT a true
   power cycle — use the PSU. Tools in `~/captures/`: `dump_codeplug.py` (read 640 B), `setunit.py <n>`
   (set Own Unit No. = displayed number), `idwrite.py`, `sbep_client.py`; `tools/sbep.py` has the
   `Radio` class (read_block/transact/id_string).
3. **Vendor programming = WDPS_DP / WDPS_NP in a Win3.1 QEMU VM** (tmux `qvm`; disk `~/win31/vm/msdos.raw`).
   DP is `C:\MRSS\MPT\DPSWDP\WDPS_DP.EXE` (R00.05.04, **English menus**), launched via Program-Manager
   Datei→Ausführen. Serial to the radio via a **socat bridge** `TCP-LISTEN:2323→2324` (tmux `bridge`);
   start it only for DPS, kill it for direct SBEP (QEMU re-grabs the single applet slot otherwise).
   Drive the GUI headless: `tools/mouse_ctl.py {click,dbl,move} X Y` (closed-loop mouse on the 640×480
   framebuffer; needs WIN.INI MouseSpeed=0), `/tmp/typestr.py "text"` / `--clear` (German QWERTZ map),
   and QEMU monitor `/tmp/qmon` (`sendkey`, `screendump /tmp/x.ppm`). `screendump` scale: resize PPM
   to 760w → multiply displayed coords by 0.842 for framebuffer coords. Owner can VNC `:5901` for
   clicks you can't land.
4. **See the screen = USB webcam `/dev/video2`** (MJPEG). Capture:
   `ffmpeg -f v4l2 -input_format mjpeg -video_size 1280x720 -i /dev/video2 -frames:v 20 -update 1 -y x.jpg`;
   the LCD crops to about `-crop 300x150+660+190`. Montage many shots with ImageMagick `magick montage`.
5. **Cycle-accurate emulator = `tools/emu6811.py`** — boots `motofix.BIN` + a 640-B codeplug, renders
   0x37AC. Great oracle for most things BUT **it does NOT model the Own-Unit-No.→LCD render** (changing
   0x0F0B/0x0F0C shows no change in the emulator yet changes the real display) — verify display-source
   claims on HARDWARE, not the emulator. `tools/mock_radio.py` for tool unit tests.
6. **RAM-stub toolchain** = `tools/asm6811.py` (tiny 68HC11 assembler; **no symbol arithmetic, no
   ldy** — use literal addresses), upload via `0x17`, launch via the `0x26` backdoor (`sei`+`jmp`).
   Dumpers/writers: `ramdump.py` (full flash), `flashtool.py` (AT29C020 in-circuit write — risky,
   bricked once, recovered via T48). **GOTCHA: a stub that does `sei` + a COP-kicking loop makes SBEP
   go dead and `0x10` CANNOT recover it (the stub never interprets `0x10`) — the only way out is a PSU
   power-cycle (#1).** So keep `psu_reboot.py` handy whenever testing `.S` stubs.

### Key solved facts you'll keep needing
- **Displayed idle number = MPT1327 Own Unit No. = codeplug 0x0F0B–0x0F0C, big-endian 16-bit**
  (322=0x0142), block checksum at **0x0FF4** = `(Σ(0x0F08..0x0FF3) − 0x55) & 0xFF`. Change it via
  `setunit.py` (direct SBEP + reset) or DPS. Cam-verified 322/456/712.
- **LCD push protocol** (traced, for a `.S` that drives the glass directly): command phase
  SPCR(0x1028)=0x57, CS(0x107E)=0x41, send bytes `02 50 3F` via firmware SPI primitive **0x9102**
  (X=buf, B=count), CS=0x00; data phase SPCR=0x50, CS=0x01, send the 21-B 0x37AC buffer via 0x9102,
  CS=0x00. (A stub using this still needs validation — see FIRMWARE.md.)

## Hardware facts (from the schematic; confirmed on a radio unless noted)

- J0400 (rear 16-pin accessory connector): **pin 6 = BUS+** (through R0422, 270 Ω), **pin 7 =
  GND**. Pin 8 SCI_DATA_OUT is dead (Q0421 and R0428 not fitted). Pin 13 ANALOG_GND/SW_B+ may carry
  battery voltage.
- **HOOK_RSS (J0400-14) needs about 9 V for programming** (lab PSU at 9 V, current limit set, draws
  about 1 mA). Above about 5.7 V, VR0102 (5.1 V zener) → Q0105 fires the CPU IRQ and, through
  Q0122, enables the radio's BUS+ transmitter. Without it the radio receives but never answers.
  Never wire HOOK to Glasgow. Keep it under 12 V (14 V zener VR0415).
- The control head is **not** on BUS+: it uses SPI (J0101 SPI_CLCK_BUF/SPI_DATA_BUF/LCD_CE_BUF/
  LED_CE) and analog key ladders. BUS+ is silent in normal use, so `sniff` on an idle radio shows
  nothing.
- BUS+ pull-up is R0130, 10 kΩ. **There is no BUSY line**: don't use `--busy`.
- Physical pin layout of J0400 isn't recorded; go by the moulded numbers.
- Only wires plus the 9 V HOOK supply are needed: no MAX232, LM311 or diode.

## Protocol and codeplug facts

- ACK is `50`, NAK `60`. A bare `80` in place of the ACK means the request was refused (e.g. a
  128-byte read; 64 bytes is the most that works).
- `10` (enter programming mode) ACKs, then **resets the radio**. After the reset it answers
  commands without another `10`.
- `11` reads the CPU's 64 KB logical space; the top address byte is ignored (checked for every
  value `00`–`FF`).
- **The codeplug is the 640-byte internal EEPROM, `0x0D80`–`0x0FFF`.** DPS reads it as 20 × 32
  bytes and writes only `0x0E13`–`0x0FFB` (15 × 32 bytes, then 2 and 7). It never sends `21` or
  `19` to a GM600.
- Checksums are solved: five per-block additive sums, `checksum = (Σ block_data − 0x55) & 0xFF`,
  firmware-verified (validator 0xB8E1/0xB900). Checksum bytes: 0x0D85, 0x0D93, 0x0E12 (Radio),
  0x0F07 (Network), 0x0FF4 (Personality); 0x0FF5–0x0FFF is in no block. See `tools/gm600_codeplug.py`.
- DPS blanks the factory field `0x0FF5`–`0x0FFB` (`20 00 00 00 00 11 DC` on the reference radio) on
  every write. The pre-write dump `docs/gm600-807IYA0903-2026-11-03-alignment-0D80.bin` is the
  only copy of the original.
- **Never send** `17`, `20`, `22`, `19` or `26` without a reason and a backup. `20`/`22` touch
  tuning; `26` jumps to an address taken from the request.

## Working with the radio

- The owner may be using Glasgow for DPS sessions (QEMU → `socat` → applet `socket` on
  `127.0.0.1:2324`). Check `ps` for a running `glasgow run rib` before using it, and ask.
- The applet's `socket` serves one client at a time.
- Test tool changes against `tools/mock_radio.py` (`cd tools && python3 -m unittest test_tools`)
  before pointing them at the radio.
- Capture logs from DPS sessions are in `~/captures/` (not in git).

## Next steps

1. Finish the in-circuit flash write: the mechanism is solved and VM-validated (12 V on pin-12 +
   PA6 release the /OE write-lock; probe returns `1F DA FE FE`); the open item is the 256-byte
   host→radio write burst dropping the applet link. See FLASH_WRITE.md.
2. Time the reset after `10`; capture what `20`/`22` do from DPS (don't guess).
3. Optional: bind the MPT1327 network block (`0x0E13`–`0x0F07`).

## Reference material (`docs/` is not in git; re-fetch on a new machine)

- GM600 UHF service manual (schematics): `motorola_gm600_uhf_sm.pdf`, supplied by the owner.
  Pages 5 and 7 are the controller and I/O schematics.
- GM Series (GM340 family) detailed service manual 6864115B62-C:
  https://manuals.repeater-builder.com/mo-files1/-GM%20Series/GM%20Series%20Detailed%20service%20manual%206864115B62-C.pdf
- GM600 user manual: `115e93.pdf`, supplied by the owner. Nothing about the bus.
- 16-pin connector diagram:
  https://www.repeater-builder.com/motorola/maxtrac/interfacing/maxtrac-16-pin-plug.gif
- Fuller reverse-engineering write-ups (DPS, file formats, field catalogs): the motorola-cps repo,
  `docs/DPS_EMEA_600SERIES_R00.03.02/`.
