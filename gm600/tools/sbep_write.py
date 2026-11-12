#!/usr/bin/env python3
"""Compatibility shim (kept for existing commands; prefer `python3 -m gm600py.codeplug.sbep_write` from gm600/).

The code lives in gm600py.codeplug.sbep_write; `python3 tools/sbep_write.py ...` and `import sbep_write` keep working for now."""
import importlib
import os
import runpy
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if __name__ == "__main__":
    runpy.run_module("gm600py.codeplug.sbep_write", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = importlib.import_module("gm600py.codeplug.sbep_write")
