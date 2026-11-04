# glasgow-rib: notes for agents

Glasgow applet that replaces a Motorola RIB on the radio side: a half-duplex, open-drain 8N1
UART on BUS+, with an optional BUSY line. The first target radio is the **GM600**. The owner is
also writing their own host software (CPS), so this applet is the PHY for that, not mainly an
RSS/DOSBox bridge.

Read first: `README.md` (the applet, generic). Everything radio-specific lives in its own folder:
**`gm600/`** for the GM600 (wiring, protocol, codeplug, firmware, tools), with its own
`gm600/CLAUDE.md`. Keep radio-specific facts out of the top level; add a new folder per radio.

## Repo conventions

- Commit on `main` and push with `git push origin main`. Don't switch branches.
- The remote uses the deploy-key alias: `git@github.com-glasgow-rib:nemanjan00/glasgow-rib.git`
  (key `~/.ssh/glasgow-rib_deploy`). Plain `git@github.com:` picks up another repo's deploy key
  and is refused; HTTPS has no credentials.
- `docs/` is gitignored and holds local reference material only (see below).
- JavaScript work (e.g. in `motorola-cps`) goes through the `/implement-js` skill.

## Dev setup

```sh
python3 -m venv .venv
.venv/bin/pip install -e "<path to glasgow checkout>/software[builtin-toolchain]"
.venv/bin/pip install --no-deps -e .
export GLASGOW_OUT_OF_TREE_APPLETS=I-am-okay-with-breaking-changes
.venv/bin/glasgow run rib --help
.venv/bin/python -m unittest glasgow_rib.test   # ~45 s simulation, ~15 s synthesis
```

- Build against current Glasgow main (applet API V2: `GlasgowAppletV2`, `assembly.add_*`). Older
  applet examples online use a different API.
- The tests write `*.vcd` files into the working directory. Delete them; don't commit them.

## Test gotchas (already hit once)

- The simulation clock is 1 MHz. Tests run at 100 kbaud (`BIT_CYC = 10`) to stay fast; 9600 baud
  sims take minutes.
- In simulation, `pipe.flush()` blocks until the gateware has consumed every byte. A test that
  writes with `flush=True` while the gateware is deliberately holding off (BUSY asserted,
  collision) deadlocks. Write without a flush in those tests.
- `asyncio.wait_for` doesn't work inside the Amaranth testbench, so `transfer(timeout=...)` has
  no simulation test yet.

## Design decisions

- **Open drain:** BUS+ is only ever pulled low (`o=0`, toggling `oe`). BUSY only drives its
  asserted level; polarity is the build option `--busy-active`, and the pull direction follows it.
- **Echo:** everything on the line, our own bytes included, goes to the host. The GM600 DPS and
  the CM CPS both verify the echo.
- **Atomic bursts:** a burst starts only after 10 bit times of idle (line high, BUSY inactive).
  Bytes from one `write()` go out back to back. **Send one whole frame per write**; byte-at-a-time
  writes each wait for the 10-bit idle.
- **Collisions:** checked at mid-bit on released bits. On a collision the gateware releases the
  line, discards the rest of the queued burst and counts it. `transfer(frame, timeout=…)` then
  returns a short echo, which the caller treats as a mismatch and retries.
- **No IN flush:** the FX2 sends a short packet 125 µs after the last byte, which is fast enough
  at 9600 baud.

## Status

Done: applet with pty, socket and sniff operations; BUSY modes (none, manual, auto, with
setup/hold); collision abort; transfer timeout. 7 simulation tests and 2 synthesis tests pass.
**Tested on a real radio: GM600** (Glasgow revC3, 9600 baud, read and write); see
`gm600/README.md`. BUSY handling is still simulation-only, because the GM600 has no BUSY line.

- The applet must use `from glasgow.support import logging`. Current Glasgow pipes call
  `logger.trace()`, and a stdlib logger crashes at startup.
- The V1 `analyzer` applet gave a false "line low" capture once. Use `control-gpio` (e.g.
  `A0=L A0`) to read a static level.

## Next steps

1. Radio-specific work: see that radio's folder (`gm600/CLAUDE.md`).
2. Possible: timestamped events from the gateware (byte, BUSY edge, collision, framing error),
   for a better sniffer. Today `sniff` timestamps come from the host and are only accurate to
   a few ms.
3. `motorola-cps` integration (the owner's JS CPS for CM/CP Commercial Series radios,
   `git@github.com:nemanjan00/motorola-cps.git`):
   - Same frame format as the GM600 (`F0|len`, op, data, `0xFF − sum` checksum), but a different
     command set.
   - Its `esbep.js` sends byte by byte and waits for each echo. That clashes with the 10-bit idle
     rule, so add a "write whole frame, then read the echo" mode.
   - It only parses short frames; block reads need the `FF` 16-bit-length form.
   - It expects ACK/NAK as `05`/`06`. The GM600 sends `50`; CM is unknown.
   - Its transports are WebSerial and Node serialport. WebSerial can't open a pty, so the web app
     needs a WebSocket transport to `glasgow run rib … socket`.
   - CM radios need HOOK at 9.3 V through a resistor to enter programming mode.
   - Ask the owner before working in that repo.

## Reference material (`docs/` is not in git; re-fetch on a new machine)

Radio-specific manuals are listed in each radio's `CLAUDE.md`. Generic:

- RIB overview: https://www.repeater-builder.com/motorola/rss/rss-rib.html
- RIB from the PC side (DTR→BUSY, RI/CTS, echo timing):
  https://github.com/joncampbell123/dosbox-x/issues/6763 and pull 6765
- SB9600 protocol manual: https://vdoc.pub/documents/sb9600-serial-bus-protocol-manual-1bjs66853j88
- Not obtained: the RLN4008 RIB schematic (batlabs returns 403 to fetches; try a browser).
