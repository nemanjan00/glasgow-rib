#!/usr/bin/env python3
"""Shim: the code lives in gm600py.link.sbep. Keeps `python3 tools/sbep.py ...` and `import sbep` working."""
import importlib
import os
import runpy
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if __name__ == "__main__":
    runpy.run_module("gm600py.link.sbep", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = importlib.import_module("gm600py.link.sbep")
