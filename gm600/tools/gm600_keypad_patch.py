#!/usr/bin/env python3
"""Compatibility shim (kept for existing commands; prefer `python3 -m gm600py.firmware.keypad_patch` from gm600/).

The code lives in gm600py.firmware.keypad_patch; `python3 tools/gm600_keypad_patch.py ...` and `import gm600_keypad_patch` keep working for now."""
import importlib
import os
import runpy
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if __name__ == "__main__":
    runpy.run_module("gm600py.firmware.keypad_patch", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = importlib.import_module("gm600py.firmware.keypad_patch")
