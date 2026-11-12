#!/usr/bin/env python3
"""Shim: the code lives in gm600py.stubs.flash_write_test. Keeps `python3 tools/flash_write_test.py ...` and `import flash_write_test` working."""
import importlib
import os
import runpy
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if __name__ == "__main__":
    runpy.run_module("gm600py.stubs.flash_write_test", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = importlib.import_module("gm600py.stubs.flash_write_test")
