#!/usr/bin/env python3
"""Compatibility shim (kept for existing commands; prefer `python3 -m gm600py.dpsvm.mouse_ctl` from gm600/).

The code lives in gm600py.dpsvm.mouse_ctl; `python3 tools/mouse_ctl.py ...` and `import mouse_ctl` keep working for now."""
import importlib
import os
import runpy
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if __name__ == "__main__":
    runpy.run_module("gm600py.dpsvm.mouse_ctl", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = importlib.import_module("gm600py.dpsvm.mouse_ctl")
