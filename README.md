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

## GM600 wiring

From the GM600 UHF service manual (controller schematic 8402626Y07):

| Signal | Where |
|---|---|
| BUS+ | accessory connector J0400 pin 6 (`SCI_DATA_IN`), or control head connector J0101 pin 15 |
| GND | J0400 pin 7 |

The GM600 has no BUSY line, so leave `--busy` off. J0400 pin 8 (`SCI_DATA_OUT`) runs through
Q0421, which is marked not fitted (NU) on this board revision, so use pin 6. The pull-up on the
J0400 side (R0429) is also NU. If edges look slow on a scope, add 4.7 kΩ from BUS+ to 5 V; the
Glasgow's internal pull-up is weak.

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
echo = await iface.transfer(bytes([0x05, 0x01, 0x00, 0x1f, 0x9a]))  # write + read back the echo
reply = await iface.read(5)
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
