import os
import shutil
from pathlib import Path

import pytest

# The GUI tests must never open windows on the owner's desktop. GTK prefers a Wayland socket over DISPLAY, so even
# under xvfb-run the windows went to the real desktop: use X11 only, and treat the desktop's own display (:0, :1) as
# "no display" (the GUI tests then skip). Set MPEXPLORER_GUI_ON_DESKTOP=1 to watch them on purpose.
if not os.environ.get("MPEXPLORER_GUI_ON_DESKTOP"):
    os.environ.pop("WAYLAND_DISPLAY", None)
    os.environ["GDK_BACKEND"] = "x11"
    if os.environ.get("DISPLAY", "").split(".")[0] in (":0", ":1"):
        os.environ.pop("DISPLAY")

SHIM = str(Path(__file__).with_name("rawrepl_shim.py"))


def micropython_binary():
    exe = os.environ.get("MICROPYTHON") or shutil.which("micropython")
    if not exe:
        if os.environ.get("MPY_REQUIRED"):      # CI: a missing binary must fail, not silently skip
            pytest.fail("MPY_REQUIRED is set but no MicroPython binary was found (set MICROPYTHON)")
        pytest.skip("no MicroPython unix port; set MICROPYTHON=/path/to/micropython to run these")
    return exe


@pytest.fixture
def mpy_shim_serial(tmp_path):
    """Real MicroPython running tests/rawrepl_shim.py (raw-REPL framing in front of a real interpreter)."""
    from ptyserial import PtySerial
    ser = PtySerial([micropython_binary(), SHIM], cwd=str(tmp_path), banner=b"READY\r\n")
    yield ser
    print("pty transcript (tail):", bytes(ser.transcript[-1500:]), "alive:", ser.alive())
    ser.close()


@pytest.fixture
def mpy_serial(tmp_path):
    """Real MicroPython's own friendly REPL (readline editing, history, paste mode) on a pty."""
    from ptyserial import PtySerial
    ser = PtySerial([micropython_binary()], cwd=str(tmp_path))
    yield ser
    print("pty transcript (tail):", bytes(ser.transcript[-1500:]), "alive:", ser.alive())
    ser.close()
