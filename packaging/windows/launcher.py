"""Entry point of the Windows build (PyInstaller runs this, not the package's __main__ directly).

It gives the process an explicit AppUserModelID first, so the taskbar groups and names every mpexplorer window as one
program (the icon is built into the exe). Then it is the same as `python -m mpexplorer`."""
import sys

if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Corteil.mpexplorer")
    except (AttributeError, OSError):
        pass

from mpexplorer.__main__ import main  # noqa: E402

main()
