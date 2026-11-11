# tools/lab — one-off experiments (not maintained)

Scratch scripts from hardware/VM reverse-engineering sessions. Conclusions that matter are written up
in `../../*.md` (esp. `FIRMWARE.md` "## LCD — settled facts"); these are kept only for reference/redo.
They run on import, shell out, and write to /tmp — treat as throwaway, not an API.

- `lcd_arb.S` / `lcd_arb.py`, `lcd_fftest.S` — WIP RAM-stub attempts to drive 0x37AC directly (not
  working yet; see FIRMWARE.md "Direct stub driving ... NOT working yet").
- `lcd_cmd.S`, `lcd_replay.S` / `lcd_replay.py` — earlier LCD-takeover/phase-replay stubs (superseded).
- `lcd_drive.py`, `lcd_sweep.py`, `lcd_segsweep.py` — the `0x37AC` "race" + segment-sweep experiments
  (the race is unreliable — RAM writes don't ACK).
- `led_sweep.S`, `led_demo.py`, `led_run.py` — LED-bit-map sweeps (result is the table in FIRMWARE.md).

## .S LCD-push experiments (2026-11-11)
The `.S` push to the control-head LCD controller WORKS but is timing-marginal:
- `lcd_disp2.S` — sei-stub, fill 0x37AC=FF, push → lit all segments ("888") with SBEP verified dead
  (firmware halted ⇒ a real push, not the boot self-test). The key that made pushes land: the
  control-head **enable 0x107C (=0x14/0x10) + 0x1024 (=0x00/0x40)** and **busy-wait delays** replacing
  the firmware's OC4-timed delays; CS on 0x107E via RMW (oraa/anda). Full sequence in STATE.md.
- `lcd_disp3.S` — same push but receives the 21-byte buffer over SCI (receive proved unreliable).
- `lcd_disp4.S` / `lcd_segstub.S` + `batch_sweep.py`/`batch_sweep2.py` — attempts to sweep the
  21-byte→segment map (push one byte=FF per launch). **Confounded:** the stub crashes inconsistently
  (0x9102 SPIF wait likely hangs → COP-reset → boot self-test "888"), so frames alias. Needs the push
  made reliable (SPI timing) before the sweep is trustworthy.
- `isr_lcd.S`-style OC3 co-resident attempt stormed (pseudo-vector/flag assumptions off).
Digit source pipeline (real HW): ASCII @0x34BD → per-digit 7-seg @0x3497 → multiplex → push; 0x37AC is
NOT the live digit buffer (fixed at the 322 pattern regardless of the displayed number).
