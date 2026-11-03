# GM600 SBEP tooling

Host-side tools that talk to a GM600 through the applet's `socket` operation
(`glasgow run rib -V 5 --bus A0 socket tcp:127.0.0.1:2324`). They speak raw SBEP and account
for the applet's echo. Validated on a real GM600 (serial `807IYA0903`), 2026-11-03.

Prereqs: 9 V on HOOK (see [../CONNECTION.md](../CONNECTION.md)); the applet listening on `2324`;
**nothing else connected to `2324`** — it serves one client at a time (see "Capture rig" below).

## `sbep_client.py` — read-only

Minimal SBEP client. As shipped it queries the serial (`23 01`) and reads 32 bytes at `0x0F60`
(the channel region), printing echo + reply. Frame checksum = `(0xFF − sum(bytes)) & 0xFF`;
short frame `F0|n op data… cks`, long read reply `FF 80 …`. Use it as a library:

```python
r = Radio()
echo, reply = r.xfer(short_frame(0x11, bytes([0x20, 0x00, 0x0F, 0x60])), 4+3+0x20+1+2)  # read 32B @0x0F60
```

Opcodes confirmed live: `23` ID query, `11` block read, `13` max-block, `10` enter/reset,
`17` write (→ `84` OK / `85` fail). ACK = `50`.

## `sbep_write.py` — write-probe (DANGER: writes the radio)

Replays DPS's captured write stream (`sniff-write.log` from a capture) **verbatim**, optionally
patching a few bytes, so one session writes a complete, valid-framed codeplug with a controlled
change. Modes:

- *(default)* dry run — parse + print, send nothing.
- `--restore` — replay the stream unmodified → returns the radio to the **captured (post-DPS)
  baseline**, which is the safe undo for a probe session. Note it is *not* the factory original:
  DPS's captured write had already cleared `0x0FF5`–`0x0FFB` (`20 00 00 00 00 11 DC` → `FF`), so
  replaying it keeps those `FF`. The factory bytes live only in the pre-write dumps (see
  [../CODEPLUG.md](../CODEPLUG.md)).
- `--write` — send the patched stream. `PROBE_SET=<name>` picks the patch table (see the top of the
  file). Each `17` is checked for an `84` (OK) reply.

How the probes stay valid: each block has an **additive** checksum (see
[../CODEPLUG.md](../CODEPLUG.md)), so a changed byte is balanced by an equal, opposite change to
**another byte in the same block** (the probe sets do this with a spare byte or a Tx-PL index),
leaving the block's sum unchanged. This works *only* because the net change is zero — it does not
reproduce the actual checksum, so it can't be used to write an arbitrary (net-nonzero) change. A
non-zero net delta is rejected by DPS on read with a block checksum error; off-step frequencies or
an Inhibit-with-PL combo are rejected as invalid values.

The patch addresses in `sbep_write.py` are specific to this radio's layout and its captured
`sniff-write.log`; adapt both for another radio/codeplug.

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

**Headless limits:** the guest Win3.11 mouse does not track injected motion (QEMU-monitor
`mouse_move` and VNC absolute pointer both leave the cursor at 0,0), and the Conventional
Personality grid combos ignore injected keystrokes (the dropdown opens but the selection never
sticks). Menu/dialog keyboard nav works; editing a per-channel option to generate contrast needs a
real interactive display.
