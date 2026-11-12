"""Shared setup for the GM600 tests (no hardware needed; some tests need GM600_FW / GM600_CP / node).

Run from the repo root: python3 -m unittest discover -s gm600/tests -t gm600/tests
"""
import os
import subprocess
import sys
import tempfile
import threading
import types
import unittest

GM600 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
TOOLS = os.path.join(GM600, "tools")
sys.path.insert(0, GM600)        # the gm600py package
sys.path.insert(0, TOOLS)        # the old module names (shims onto gm600py), used by these tests

from mock_radio import MockRadio
from sbep import Radio, SBEPError, encode, decode, frame_length
from gm600_codeplug import verify_checksums, fix_checksums, blocks, CHECKSUM_ADDRS, RADIO_BASE

HERE = TOOLS                     # CLIs are run from tools/ (their shims), as a user would
STUBS = os.path.join(GM600, "gm600py", "stubs")
REPO = os.path.join(GM600, "..")
IMAGE = bytes((i * 7 + 3) & 0xFF for i in range(640))


def run_tool(radio, *args):
    env = dict(os.environ, SBEP_ADDR=f"127.0.0.1:{radio.port}")
    return subprocess.run([sys.executable, *args], cwd=HERE, env=env,
                          capture_output=True, text=True, timeout=60)


def write_file(path, text):
    with open(path, "w") as f:
        f.write(text)


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def capture_log(frames):
    """A socat -v style log of the host sending ``frames``."""
    lines = []
    for f in frames:
        lines.append(f"> 2026/11/03 21:01:27.952106  length={len(f)} from=0 to={len(f) - 1}")
        lines.append(" " + " ".join(f"{b:02x}" for b in f))
    return "\n".join(lines) + "\n"

