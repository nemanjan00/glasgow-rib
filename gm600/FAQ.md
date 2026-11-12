# GM600 FAQ — operating the radio from the front panel

Practical answers for driving a GM600 (number-only control head) on the bench, especially getting
it onto a **conventional** channel to receive/transmit a plain carrier + PL. For wiring and
programming see [CONNECTION.md](CONNECTION.md) and [PROTOCOL.md](PROTOCOL.md); for the codeplug see
[CODEPLUG.md](CODEPLUG.md).

## The display shows a 3-digit number (e.g. `322`). Which channel is that?

It isn't a channel. In **trunked** mode the number-only head shows the radio's **MPT1327 identity**
(its home unit number). `322` on the reference radio (`807IYA0903`) is its Own Unit identity, not a
frequency. Scrolling it just changes the call/identity number, which snaps back to home.

## How do I switch the radio to a conventional channel?

**Long-hold Button A for ~7 seconds** until it latches into conventional. This is the gate that
matters — not any codeplug flag. On this radio Conventional Mode is already `All` and four
conventional channels are programmed, yet the radio still boots trunked and only enters conventional
on the long-hold.

- A **short** press of A is *momentary* only: the radio shows `---` (a prompt) and drops straight
  back to trunked (`322`) with a short error beep when you release. That's expected — it isn't a
  rejection, it's just that a brief press doesn't latch.
- The **~7 s long-hold** toggles conventional on (and off again). Switching mode re-runs the
  self-test (all segments, `888`). **The mode survives a power-off** (seen 2026-11-12: PSU output off,
  then on — the radio booted straight back into conventional `1`), yet the EEPROM codeplug is
  byte-identical in both modes. So it's kept in RAM that outlives the main supply (most likely the
  HC11's standby RAM supply, VSTBY); older notes saying "a power-cycle returns to trunked" were wrong
  or saw the backup run down.
- **Fastest, over SBEP: `tools/gm600_mode.py conv|trunk`** — the mode is two bytes in the CPU's
  internal RAM (kept by the standby supply): `0x0085` (`FF` conventional / `01` trunked) and `0x0089`
  bit 1 (conventional) / bit 2 (trunked). Write both and reset with `0x10`; ~10 s, verified both ways.
  Also a button in the live web panel (`tools/gm600_live.py`).
- **Remotely, over SBEP:** `tools/keyhold.py` (default: long-hold A for 9 s). It pauses the keypad
  task and posts the key event through the firmware's own routine — no flash patch, nothing written to
  the codeplug; the radio keeps running. Verified on the bench radio (`322` → `---` → `  1`).

## Once in conventional, how do I pick the channel?

The channel selector then cycles the conventional personality's channels, shown as `001`…`004`.
On the reference radio those are:

| Shown | Rx / Tx (MHz) | Notes |
|------|---------------|-------|
| `001` | 446.068750 / 446.068750 | simplex; RxPL set to **3Z / 123.0 Hz** |
| `002` | 446.131250 / 446.131250 | simplex |
| `003` | 446.093750 / 446.093750 | simplex |
| `004` | 438.625000 / 431.025000 | duplex (−7.6 MHz), Tx PL 3Z/123.0 |

(Your radio's four channels come from *its* conventional personality — read the codeplug to see
them; the channel table is at `0x0F67`, see [CODEPLUG.md](CODEPLUG.md).)

## It jumps back to `322` (trunked) after a few seconds. Why?

You selected conventional *momentarily* (short press) rather than latching it. Use the ~7 s
long-hold. A blinking LED means the radio is still hunting a trunked control channel; a latched
conventional channel stops the hunt.

## Does the long-hold write anything to the codeplug?

No — confirmed on the radio: a 640-byte codeplug dump in conventional mode is byte-identical to the
trunked one. The mode lives in RAM that survives power-off (see above). In the emulator, which now boots
like the real radio: Holding A latches conventional channel
`  1` with **no EEPROM write**: the firmware's EEPROM-write routine (page 0 `0x5259`–`0x5293`) never
runs, during the hold or for 5 M instructions after it. Booting with every value of the
Conventional Mode field (`0x0E45` bits 6:5 — Inhibit/Dealer/All/RegioNet43, checksums fixed) still
comes up trunked (`322`). So the mode is RAM state; the emulator starts with cleared RAM, which is
why it always boots trunked. [RECOVERY.md](RECOVERY.md) attributes a 3-byte EEPROM difference to the
mode switch, but its addresses were never recorded; to settle it on hardware, dump the codeplug
(`dump_codeplug.py`) in trunked mode, long-hold A, dump again, and diff.

## How do I tell, over SBEP, that the radio is receiving?

Read RAM `0x347E`–`0x347F` (a 2-byte `0x11` read; works while the radio runs normally):
`0x347F` = 1 on a carrier, `0x347E` = 1 when the channel's PL is decoded, i.e. **RX active, squelch
open**. A carrier with no or the wrong PL gives `0x347F`=1, `0x347E`=0 and the red LED flashes "busy".
See [FIRMWARE.md](FIRMWARE.md) "Keypad, key events and the receive flags".

## Squelch won't open on a conventional channel.

The channel's **RxPL** must match the PL (CTCSS) on the incoming signal, or squelch stays closed.
With a HackRF: `tools/ctcss_tx.py` transmits narrowband FM on 446.06875 MHz with 123.0 Hz PL and
1 kHz audio (`--no-tone` for the same signal without PL), and `tools/squelch_test.py` runs no-signal /
no-PL / PL / off phases while logging the receive flags, LEDs and display over SBEP. On the bench
(antennas close): TX VGA 0 was too weak; VGA 35 with a +2.5 kHz frequency correction (`--cal-hz`, the
bench HackRF's offset) decodes the PL. The link is still marginal — about 1 tone phase in 3 saw no
carrier at all (HackRF start-up) — so repeat a phase rather than trusting a single miss. Measured:

| signal | `0x347F` carrier | `0x347E` RX/PL | red LED |
|--|--|--|--|
| none | 0 | 0 | off |
| carrier, no PL | 1 | 0 | flashing (busy) |
| carrier + 88.5 Hz (wrong) | 1 | 0 | flashing (busy) |
| carrier + 123.0 Hz | 1 | **1** | (flashes in some runs) |
`001` on the reference radio expects **123.0 Hz (3Z)**. Feed a carrier on the channel frequency
with 123.0 Hz PL and squelch opens. (The recovered sub-audible PL tone is present in the receive
audio path whenever that carrier is received, independent of the squelch decision — handy for
probing; see [HARDWARE.md](HARDWARE.md) for tap points.)

## What do I need powered for the radio to talk over BUS+ / be programmed?

Main supply (≈12 V for a mobile) **and 9 V on HOOK (J0400 pin 14)** — without HOOK the radio never
transmits on BUS+ and every read comes back as echo-with-no-reply. See
[CONNECTION.md](CONNECTION.md).

## How do I leave it between bench sessions?

Power the radio off. The **codeplug is the CPU's internal EEPROM and persists** with no power — any
PL/channel edits you wrote stay. Note which mode/channel you left it on (the head boots back to
trunked `322` regardless). Keep your pre-write codeplug dump; see the warning in the
[README](README.md#status-2026-11-04).

## DPS service / alignment menus ask for a password.

The Service → Transmitter/Receiver Alignment menus in the Dealer Programmer are password-gated
(checked client-side in `WDPS_DP.EXE`, `password.cpp`). This is a per-EXE detail, not a universal
one: **on our copy of `WDPS_DP.EXE`** the stored password reads as unset (WSERIAL `-P` was never
run), so an **empty password (just OK)** gets in — a copy serialized with a password will reject
that. These menus let you key/receive on a test frequency, which is another way to put the radio on
a known frequency without the front panel.
