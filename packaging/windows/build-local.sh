#!/bin/bash
# Build the Windows program on this PC, the same steps as .github/workflows/windows-build.yml. Started by
# build-local.ps1 (which sets MSYSTEM=UCRT64 and the folder); needs MSYS2 with the packages listed in CONTRIBUTING.md.
# Everything it makes goes to $MPX_OUT (default D:\mpexplorer-build), not into the repo: a venv that sees MSYS2's
# GTK and adds esptool, the PyInstaller work folder, and dist/mpexplorer/ (the program).
set -euo pipefail

OUT=$(cygpath -u "${MPX_OUT:-D:/mpexplorer-build}")
VENV="$OUT/venv"
mkdir -p "$OUT"

if [ ! -d "$VENV" ]; then
    echo "== making $VENV (sees MSYS2's GTK and PyInstaller)"
    python -m venv --system-site-packages "$VENV"
fi
PY="$VENV/bin/python"
[ -x "$PY" ] || [ -x "$PY.exe" ] || PY="$VENV/Scripts/python"
"$PY" -m pip install -q --disable-pip-version-check "esptool>=5,<6" pytest-timeout pytest-xdist
"$PY" -c "import gi; gi.require_version('Gtk', '3.0'); from gi.repository import Gtk; import sys; \
print('Python', sys.version.split()[0], ' GTK', Gtk.get_major_version(), Gtk.get_minor_version(), Gtk.get_micro_version())"

HIDDEN="$PWD/packaging/windows/hidden_desktop.py"     # windows go to a hidden desktop, not the owner's screen
if [ "${MPX_TESTS:-1}" != "0" ]; then
    # every core (pytest-xdist): 750 tests in ~95 s instead of ~205 s on the owner's 4-core PC (2026-11-08)
    echo "== tests (GUI tests included, on a hidden desktop, ${MPX_WORKERS:-auto} workers)"
    "$PY" "$HIDDEN" -m pytest -q --timeout=60 -p no:cacheprovider -n "${MPX_WORKERS:-auto}"
fi

echo "== PyInstaller"
"$PY" -m PyInstaller --noconfirm --clean --log-level WARN \
    --distpath "$OUT/dist" --workpath "$OUT/work" packaging/windows/mpexplorer.spec

echo "== checking the built program"
cd "$OUT/dist/mpexplorer"
./mpexplorer-console.exe --version
"$PY" "$HIDDEN" "$(cygpath -w "$PWD/mpexplorer-console.exe")" --self-test
# whole output captured, then its first line: `| head -1` closes the pipe early, and esptool's next write then fails
out=$(./mpexplorer-console.exe --esptool version); echo "${out%%$'\n'*}"
out=$(./mpexplorer-console.exe --miniterm --help); echo "${out%%$'\n'*}"

if [ "${MPX_ZIP:-0}" != "0" ]; then
    cd "$OUT/dist"
    touch mpexplorer/portable.txt.example
    rm -f mpexplorer-windows.zip
    zip -qr mpexplorer-windows.zip mpexplorer
    echo "== zip: $(cygpath -w "$OUT/dist/mpexplorer-windows.zip")"
fi
echo "== built: $(cygpath -w "$OUT/dist/mpexplorer/mpexplorer.exe")"
