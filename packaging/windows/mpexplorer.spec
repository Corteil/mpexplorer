# PyInstaller spec for the Windows build (docs/WINDOWS_PLAN.md step 4). Run inside MSYS2 UCRT64 from the repo root:
#     pyinstaller --noconfirm --clean packaging/windows/mpexplorer.spec
# Output: dist/mpexplorer/ with two programs sharing one _internal folder:
#   mpexplorer.exe          the window (no console)
#   mpexplorer-console.exe  the same code with a console: --self-test, --version, and the helpers the window starts
#                           (--miniterm for the external terminal, --esptool for the firmware tools), which need stdout.
import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))       # noqa: F821  (SPECPATH is set by PyInstaller)
ICON = os.path.join(ROOT, "mpexplorer", "icon.png")             # converted to .ico by PyInstaller (needs Pillow)

datas = collect_data_files("mpexplorer")

# the commit being built, for Help > About and --version: the exe has no .git folder to ask (mpexplorer/buildinfo.py)
import sys                                                      # noqa: E402
import tempfile                                                 # noqa: E402
sys.path.insert(0, ROOT)
from mpexplorer import buildinfo                                # noqa: E402
_build_dir = tempfile.mkdtemp(prefix="mpexplorer-build-")
print("build info:", buildinfo.write_build_file(os.path.join(_build_dir, buildinfo.BUILD_FILE), ROOT))
datas.append((os.path.join(_build_dir, buildinfo.BUILD_FILE), "mpexplorer"))
hidden = collect_submodules("mpexplorer") + collect_submodules("serial.tools")
try:                                                            # optional: the firmware tools' esptool
    hidden += collect_submodules("esptool") + collect_submodules("espefuse") + collect_submodules("espsecure")
    datas += collect_data_files("esptool")
except Exception as e:                                          # noqa: BLE001
    print("esptool not bundled:", e)

a = Analysis(                                                   # noqa: F821
    [os.path.join(SPECPATH, "launcher.py")],                    # noqa: F821
    pathex=[ROOT],
    datas=datas,
    hiddenimports=hidden,
    hooksconfig={"gi": {"icons": ["Adwaita", "hicolor"], "themes": ["Adwaita"], "languages": ["en_GB"],
                        "module-versions": {"Gtk": "3.0", "Gdk": "3.0", "GdkPixbuf": "2.0"}}},
    excludes=["tkinter", "gi.repository.Vte"],
)
pyz = PYZ(a.pure)                                               # noqa: F821
window = EXE(pyz, a.scripts, [], exclude_binaries=True, name="mpexplorer", console=False, icon=ICON)   # noqa: F821
console = EXE(pyz, a.scripts, [], exclude_binaries=True, name="mpexplorer-console", console=True,      # noqa: F821
              icon=ICON)
COLLECT(window, console, a.binaries, a.datas, name="mpexplorer")                                       # noqa: F821
