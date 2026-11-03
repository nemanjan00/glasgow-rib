# glasgow-rib: notes for agents

Glasgow applet that replaces a Motorola RIB on the radio side: a half-duplex, open-drain 8N1
UART on BUS+, with an optional BUSY line. The first target radio is the **GM600**. The owner is
also writing their own host software (CPS), so this applet is the PHY for that, not mainly an
RSS/DOSBox bridge.

Read first: `README.md` (applet), `CONNECTION.md` (GM600 wiring), `PROTOCOL.md` (GM600 SBEP
protocol, reverse engineered from DPS), `FIRMWARE.md` (GM600 firmware static analysis: CPU/memory
map, flash banking, the SBEP dispatch table, and answers to several PROTOCOL.md open questions).

## Repo conventions

- Commit on `master`; the remote branch is `main`. Push with `git push origin master:main`.
  Don't switch branches and don't change git config.
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

## GM600 hardware facts (schematic; the first three are confirmed on a radio)

- J0400 (rear 16-pin accessory connector): **pin 6 = BUS+** (through R0422, 270 Ω), **pin 7 =
  GND**. Pin 8 SCI_DATA_OUT is dead (Q0421 not fitted). Pin 13 ANALOG_GND/SW_B+ may carry
  battery voltage.
- **HOOK_RSS (J0400-14) needs about 9 V for programming** (lab PSU at 9 V, current limit set, draws
  about 1 mA). Above about 5.7 V, VR0102 (5.1 V zener) → Q0105 fires the CPU IRQ and, through
  Q0122, enables the radio's BUS+ transmitter. Without it the radio receives but never answers.
  Never wire HOOK to Glasgow. Keep it under 12 V (14 V zener VR0415).
- The control head is **not** on BUS+: it uses SPI (J0101 SPI_CLCK_BUF/SPI_DATA_BUF/LCD_CE_BUF/
  LED_CE) and analog key ladders. BUS+ is silent in normal use, so `sniff` on an idle radio shows
  nothing. BUS+ only passes through J0101-15.
- BUS+ pull-up is R0130, 10 kΩ.
- **There is no BUSY line.** Don't use `--busy`.
- Physical pin layout: probably 2×8 with odd/even rows, pins 1 and 2 at the same end (from a
  GM300/Maxtrac diagram). Not confirmed for the GM600; go by the moulded numbers.
- Only wires plus the 9 V HOOK supply are needed: no MAX232, LM311 or diode. Those parts in DIY
  RIBs exist for RS-232 levels or to make a push-pull TX behave like open drain.

## Status

Done: applet with pty, socket and sniff operations; BUSY modes (none, manual, auto, with
setup/hold); collision abort; transfer timeout; docs. 7 simulation tests and 2 synthesis tests
pass.

**Working on a real GM600 (2026-11-03)**, Glasgow revC3: TX/RX and echo are fine at 9600 baud,
the radio ACKs `10` with `50`, then resets, and after the reset it answers `23 01` (serial) and
`13`. Transcript and details are in `PROTOCOL.md` → "Confirmed on a GM600".

- The applet must use `from glasgow.support import logging`. Current Glasgow pipes call
  `logger.trace()`, and a stdlib logger crashes at startup.
- The V1 `analyzer` applet gave a false "line low" capture once. Use `control-gpio` (e.g.
  `A0=L A0`) to read a static level.

## Next steps

1. **Dump the codeplug (personality).** It is not in the 64 KB `11` view: it's paged in, most
   likely by `21`. The `21` values have to come from DPS. Another agent is reversing
   `WDPS_DP.EXE` statically, because the owner has nothing to run DPS on. Once the read sequence
   is known, run it read-only and check each region's checksum. Don't guess `21` values on the
   radio.
2. Still open on the radio (read-only): time the reset after `10`, and the meaning of the `82`
   reply. The `80` header and the `23` indexes are settled. Don't send `17`, `20`, `22` or `19`.
   The alignment block (`0D80`–`0FFF`) is already dumped; see `PROTOCOL.md`.
3. Possible: timestamped events from the gateware (byte, BUSY edge, collision, framing error),
   for a better sniffer. Today `sniff` timestamps come from the host and are only accurate to
   a few ms.
4. `motorola-cps` integration (the owner's JS CPS for CM/CP Commercial Series radios,
   `git@github.com:nemanjan00/motorola-cps.git`):
   - Same frame format as the GM600 (`F0|len`, op, data, `0xFF − sum` checksum), but a different
     command set.
   - Its `esbep.js` sends byte by byte and waits for each echo. That clashes with the 10-bit idle
     rule, so add a "write whole frame, then read the echo" mode.
   - It only parses short frames; GM600 block reads need the `FF` 16-bit-length form.
   - It expects ACK/NAK as `05`/`06`. The GM600 sends `50` (confirmed on a radio); CM is
     unknown.
   - Its transports are WebSerial and Node serialport. WebSerial can't open a pty, so the web app
     needs a WebSocket transport to `glasgow run rib … socket`.
   - CM radios need HOOK at 9.3 V through a resistor to enter programming mode.
   - Not started; ask the owner before working in that repo.

## Reference material (`docs/` is not in git; re-fetch on a new machine)

- GM600 UHF service manual (schematics): `motorola_gm600_uhf_sm.pdf`, supplied by the owner.
  Pages 5 and 7 are the controller and I/O schematics.
- GM Series (GM340 family) detailed service manual 6864115B62-C:
  https://manuals.repeater-builder.com/mo-files1/-GM%20Series/GM%20Series%20Detailed%20service%20manual%206864115B62-C.pdf
- GM600 user manual: `115e93.pdf`, supplied by the owner. Nothing about the bus.
- 16-pin connector diagram:
  https://www.repeater-builder.com/motorola/maxtrac/interfacing/maxtrac-16-pin-plug.gif
- RIB overview: https://www.repeater-builder.com/motorola/rss/rss-rib.html
- RIB from the PC side (DTR→BUSY, RI/CTS, echo timing):
  https://github.com/joncampbell123/dosbox-x/issues/6763 and pull 6765
- SB9600 protocol manual: https://vdoc.pub/documents/sb9600-serial-bus-protocol-manual-1bjs66853j88
- Not obtained: the RLN4008 RIB schematic (batlabs returns 403 to fetches; try a browser).
