# GM600 SBEP tooling

Host-side tools that talk to a GM600 through the applet's `socket` operation
(`glasgow run rib -V 5 --bus A0 socket tcp:127.0.0.1:2324`). They speak raw SBEP and account
for the applet's echo. Validated on a real GM600 (serial `807IYA0903`), 2026-11-03.

Prereqs: 9 V on HOOK (see [../CONNECTION.md](../CONNECTION.md)); the applet listening on `2324`;
**nothing else connected to `2324`** — it serves one client at a time (see "Capture rig" below).

## `sbep.py` — shared framing and client

The SBEP codec from [../PROTOCOL.md](../PROTOCOL.md) (`encode`, `decode`, `frame_length`,
`checksum`) and a `Radio` socket client that every tool uses. `Radio.transact(frame)` sends one
frame and checks it end to end: the echo must match what was sent (a mismatch means a collision),
then the status byte (`50` ACK, `60` NAK, bare `80` refused), then the reply frame's checksum. It
raises `SBEPError` on any problem instead of returning partial data. The echo and the reply each
get their own deadline, by default DPS's write-result timeout (`1 s + 12 ms × frame length`);
pass `timeout=` for anything slower.

```python
from sbep import Radio, encode
with Radio() as r:                         # SBEP_ADDR=host:port, default 127.0.0.1:2324
    print(r.id_string(1))                  # "807IYA0903"
    data = r.read_block(0x0F60, 0x20)      # checks the echoed address too
    status, op, data = r.transact(encode(0x13))
```

## `sbep_client.py` — read-only

Queries the serial (`23 01`) and reads 32 bytes at `0x0F60` (the channel region). A starting point
for your own scripts.

Opcodes confirmed live: `23` ID query, `11` block read, `13` max-block, `10` enter/reset,
`17` write (→ `84` OK / `85` fail). ACK = `50`.

## `sbep_write.py` — write-probe (DANGER: writes the radio)

Replays DPS's captured write stream (a socat sniff log, default `sniff-write.log` in the current
directory, or the path given after the mode) **verbatim**, optionally patching a few bytes, so one
session writes a complete, valid-framed codeplug with a controlled change. Modes:

- *(default)* dry run — parse + print, send nothing.
- `--restore` — replay the stream unmodified → returns the radio to the **captured (post-DPS)
  baseline**, which is the safe undo for a probe session. Note it is *not* the factory original:
  DPS's captured write had already cleared `0x0FF5`–`0x0FFB` (`20 00 00 00 00 11 DC` → `FF`), so
  replaying it keeps those `FF`. The factory bytes live only in the pre-write dumps (see
  [../CODEPLUG.md](../CODEPLUG.md)).
- `--write` — send the patched stream. `PROBE_SET=<name>` picks the patch table (see the top of the
  file); an unknown name is an error.

Every frame is checked like DPS would expect: matching echo, ACK, and for each `17` a result frame
`84` for the same address. **The stream aborts on the first failure**, so a failed or unanswered
block is never followed by more writes; the radio may then be partly written, and the message says
to run `--restore` once the cause is fixed. `--write` also refuses to send anything if the capture
doesn't contain every address in the chosen `PROBE_SET`.

How the probes stay valid: each block has an **additive** checksum (see
[../CODEPLUG.md](../CODEPLUG.md)), so a changed byte is balanced by an equal, opposite change to
**another byte in the same block** (the probe sets do this with a spare byte or a Tx-PL index),
leaving the block's sum unchanged. This works *only* because the net change is zero — it does not
reproduce the actual checksum, so it can't be used to write an arbitrary (net-nonzero) change. A
non-zero net delta is rejected by DPS on read with a block checksum error; off-step frequencies or
an Inhibit-with-PL combo are rejected as invalid values.

The patch addresses in `sbep_write.py` are specific to this radio's layout and its captured
`sniff-write.log`; adapt both for another radio/codeplug.

## `dump_codeplug.py` — full read-back

Reads all 640 B (`0x0D80`–`0x0FFF`) via `0x11` and writes a .bin. Diff two dumps (baseline vs a
DPS-written change) to bind fields: `cmp`/Python diff shows exactly which native bytes a GUI option
moved. This is how Monitor was bound (ch1 Monitor=Yes → only `0x0F6D 08→18` and checksum `0x0FF4`
changed). Reply header is `FF 80 00 24 <ah><am><al>` then 32 data bytes and the checksum.
`00 24` is the 16-bit length; the next byte is the high address byte, which is also `00` here, so
it's easy to be off by one.

## `mock_radio.py` and `test_tools.py` — no hardware needed

`mock_radio.py` is a fake GM600 behind a fake applet socket: it echoes, ACKs, and answers `23`,
`13`, `11` (from a 640-byte image), `17` and `10` like the real radio, and refuses reads over
64 bytes. Run it standalone (`python3 mock_radio.py dump.bin 2399`, then `SBEP_ADDR=127.0.0.1:2399`)
to try a tool safely. `test_tools.py` uses it to test the codec and every tool, including a write
stream aborting on a failed block: `python3 -m unittest test_tools`.

## `mouse_ctl.py` — closed-loop headless mouse (now working)

Drives the guest cursor via the QEMU monitor: `find` locates it by diffing framebuffers after a
nudge (masking the blinking caret), `move`/`click`/`dbl` converge to a target. Two things made it
reliable: (1) `WIN.INI [windows] MouseSpeed=0` in the guest (disables acceleration → ~1:1 relative
motion); (2) **pacing** monitor commands (QEMU's HMP silently drops bursts of piped lines — send
one per ~60 ms). With both, `click X Y` lands within a couple of px. This is what finally let us
toggle DPS grid combos (open Monitor dropdown, click "Yes") headlessly.

## Capture rig (driving real DPS headless through the applet)

To capture the exact DPS read/write protocol or to generate codeplug contrast, run the 16-bit
Dealer Programmer under QEMU with COM1 bridged to the applet:

1. **Win3.11 image** with DPS injected (`WDPS_DP.EXE` → `C:\DPSWDP`, auto-launched via
   `WIN.INI [windows] run=`).
2. **QEMU**, serial as a reconnecting TCP client, monitor on a unix socket, headless VNC:
   ```
   qemu-system-i386 -m 64 -drive file=msdos.raw,format=raw -vga std -display none \
     -vnc 127.0.0.1:1 -monitor unix:/tmp/qmon,server,nowait \
     -chardev socket,id=ser0,host=127.0.0.1,port=2323,reconnect-ms=2000 -serial chardev:ser0
   ```
3. **Bridge** QEMU's serial to the applet (and sniff it):
   `socat -x -v TCP-LISTEN:2323,reuseaddr,fork TCP:127.0.0.1:2324 2>sniff.log`
4. **Drive DPS** via the QEMU monitor: `echo "sendkey alt-f" | socat - UNIX-CONNECT:/tmp/qmon`,
   `screendump /tmp/s.ppm` for state. `Read Radio` = Alt+F, R, All, Alt+R; `Edit → Conventional
   Personality` = Alt+E, C.

**Single-client socket:** the applet's `socket` accepts one consumer at a time. The bridge (for
DPS) and `sbep_*.py` (direct) can't both hold `2324`. To write with the scripts while DPS is
bridged, stop the bridge first (free `2324`), run the script, then restart the bridge — QEMU's
`reconnect-ms` re-attaches on its own. Keep QEMU and the bridge in `tmux` (or a user service) so
they survive your SSH session; a bare `nohup`'d QEMU is killed by `systemd` session cleanup on
logout.

**Headless input (solved):** keyboard menu/dialog nav works via `sendkey`. The mouse works too once
you (1) set `MouseSpeed=0` in the guest `WIN.INI` and (2) **pace** monitor commands (~60 ms apart —
HMP drops bursts). Grid combos won't change via injected *keystrokes*, but a real mouse *click*
(`mouse_ctl.py`) does. So per-channel options can be edited headlessly to generate write-contrast.

If the guest cursor seems stuck at 0,0, check for two QEMU instances running at once (commands go
to the wrong one) and for unpaced monitor commands. Both looked exactly like a dead mouse.
