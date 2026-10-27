import os
import shutil

import pytest


def micropython_binary():
    return os.environ.get("MICROPYTHON") or shutil.which("micropython")


@pytest.fixture
def mpy_serial(tmp_path):
    """A real MicroPython (unix port) on a pty. Skipped unless a binary is available; CI sets MPY_REQUIRED=1 so a
    missing binary fails there instead of silently skipping."""
    exe = micropython_binary()
    if not exe:
        if os.environ.get("MPY_REQUIRED"):
            pytest.fail("MPY_REQUIRED is set but no MicroPython binary was found (set MICROPYTHON)")
        pytest.skip("no MicroPython unix port; set MICROPYTHON=/path/to/micropython to run these")
    from ptyserial import PtySerial
    ser = PtySerial([exe], cwd=str(tmp_path))
    yield ser
    ser.close()
