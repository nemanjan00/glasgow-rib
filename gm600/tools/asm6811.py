#!/usr/bin/env python3
"""Shim: the code lives in gm600py.firmware.asm6811. Keeps `python3 tools/asm6811.py ...` and `import asm6811` working."""
import importlib
import os
import runpy
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if __name__ == "__main__":
    runpy.run_module("gm600py.firmware.asm6811", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = importlib.import_module("gm600py.firmware.asm6811")
