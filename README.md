# glasgow-rib

A [Glasgow](https://glasgow-embedded.org/) applet for the single-wire serial bus of Motorola
radios (SB9600 / SBEP: GM600, GM300, Maxtrac, …). It is a half-duplex, open-drain 8N1 UART with
an optional BUSY line, with all bus timing kept in gateware.

It is a replacement for the RLN4008 RIB (Radio Interface Box) on the radio side. The Glasgow I/O
banks run at 5 V, so no level shifter is needed.

## What the gateware does

- **BUS+** is open-drain. The applet only pulls it low or releases it, and reads back the
  wired-AND of everyone on the bus.
- **Echo**: every byte on the line goes to the host, including the applet's own, as with a real
  RIB.
- **Atomic bursts**: a transmission starts only after the bus has been idle (line high, BUSY
  inactive) for one packet time, which is 10 bit times. Bytes queued together then go out back to
  back. The radio ignores a message whose bytes are more than ~2 ms apart, so this timing can't be
  left to USB.
- **Collision detection**: every released (1) bit is checked at mid-bit. If the line reads 0, the
  applet releases the bus, discards the rest of the queued burst and counts a collision.
- **BUSY** (optional) is open-drain and active low by default; `--busy-active high` switches the
  polarity. The input is glitch filtered over 1/8 of a bit time. BUSY has three modes:
  - `auto` (default): asserted `--busy-setup` bit times before the first start bit, and released
    `--busy-hold` bit times after the burst.
  - `manual`: driven from a register.
  - `none`: not driven.

## Supported radios

The applet speaks the **single-wire** variant of the bus: BUS+ only, plus BUSY if the radio has
one. Nothing has been tested against a radio yet, so this is what should work from the
schematics and RIB documentation, not a tested list.

- **Should work (BUS+ only):** GM600 (the target), and the radios that RIBless cables are sold
  for: GM300 / GM338, Maxtrac, GTX, Radius mobiles, GP300 / GP350. Most of these don't use BUSY.
- **Not supported: differential BUS+ / BUS− radios.** These are the Systems 9000 family: Syntor
  X9000 / X9000E, Spectra, Spectra II and Astro Spectra. They use both BUS+ and BUS− at the
  radio. The RIB handles the pair (homebrew RIBs use an LM311 comparator for it), and for
  BUS+-only radios a jumper in the cable's DB-25 shell stands in for BUS−. This applet has no
  BUS− pin.
- **Check the service manual first:** for other SB9600 radios (MCS2000, MTS2000, Astro 25 /
  XTL), see whether the programming port carries BUS− as well as BUS+.

Supporting the differential radios would take a second pin driving BUS− as the inverse of BUS+,
and the radio's BUS− voltage levels have to be checked first. Nothing in the gateware rules it
out.

## GM600 wiring

Connect BUS+ to accessory connector J0400 pin 6 and ground to pin 7, set the Glasgow I/O voltage
to 5 V, and leave `--busy` off (the GM600 has no BUSY line). [CONNECTION.md](CONNECTION.md) has
the full pinout, the circuit behind pin 6, meter checks before connecting, and the control head
connector alternative.

The programming protocol (SBEP framing, checksums, commands) is in [PROTOCOL.md](PROTOCOL.md).

## Install

```sh
pip install -e .            # into the same environment as glasgow
export GLASGOW_OUT_OF_TREE_APPLETS=I-am-okay-with-breaking-changes
glasgow run rib --help
```

## Usage

```sh
# GM600: BUS+ on A0, 5 V
glasgow run rib -V 5 --bus A0 sniff           # passive, timestamped hex per message
glasgow run rib -V 5 --bus A0 pty             # prints /dev/pts/N
glasgow run rib -V 5 --bus A0 socket tcp::4000

# SB9600 radio with BUSY on A1
glasgow run rib -V 5 --bus A0 --busy A1 --busy-setup 1 --busy-hold 10 sniff
```

From Python (`glasgow repl rib …` or your own app), via the applet's `RIBInterface`:

```python
iface = applet.rib_iface
frame = bytes.fromhex("F1 10 FE")
echo = await iface.transfer(frame, timeout=0.5)  # write + read back the echo
if echo != frame: ...                            # collision (short echo or wrong bytes): retry
ack = await iface.read(1)                        # 50 = ACK, 60 = NAK
await iface.get_busy()
```

Every byte written in one `write()` is one burst on the bus. Send one message per call.

## Limitations

- The applet has not been tested against a radio yet. The tests are simulation only.
- A pty carries no modem control lines. RSS versions that toggle DTR and expect BUSY back on
  RI/CTS won't see it. The applet targets a native host app that uses this API, not running RSS
  unmodified.
- `sniff` timestamps come from the host, so they are only accurate to a few milliseconds.

## Tests

```sh
python -m unittest glasgow_rib.test
```

Simulation tests cover echo, gap-free bursts, waiting for an idle bus, BUSY in auto mode,
deferring to an external BUSY, and collision abort. The synthesis tests run when a toolchain is
available (`glasgow[builtin-toolchain]`).

Reference material (service manuals, schematics) goes in `docs/`, which is gitignored.
