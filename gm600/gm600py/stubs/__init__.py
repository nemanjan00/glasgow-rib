"""GM600 RAM stubs (*.S, assembled by gm600py.firmware.asm6811) and their host-side tools.

See ../../FIRMWARE.md "Free RAM 0x3D80-0x3FFF" for who owns which bytes."""
import os

STUB_DIR = os.path.dirname(os.path.abspath(__file__))


def path(name):
    """Absolute path of a stub source, e.g. path("keyevt.S")."""
    return os.path.join(STUB_DIR, name)
