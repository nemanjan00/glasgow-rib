"""GM600 tooling: SBEP link, codeplug, firmware emulator/assembler, RAM stubs, web UI, RF tests.

Layout:
  link/      SBEP framing + socket client (sbep), mock radio, small CLI client
  codeplug/  checksums, decoded summary, safe write path (apply), dump / sbep_write CLIs
  firmware/  68HC11 emulator, assembler, display/keypad model, keypad flash patch
  stubs/     RAM stubs (*.S) and their host tools (ramdump, flashtool, lcd_text, lcd_photos)
  radio/     radio actions: mode, key hold, Own Unit No., channel PL, PSU reboot
  ui/        web front panel (panel), live server for the radio, VM bridge, remote
  rf/        HackRF CTCSS transmitter + squelch test
  dpsvm/     helpers for driving the DPS Win3.1 VM (mouse, typing)
The scripts in ../tools/ are thin shims onto these modules (old names keep working).
"""
