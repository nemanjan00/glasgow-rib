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
