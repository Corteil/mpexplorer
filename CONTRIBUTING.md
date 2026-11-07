# Contributing
- `pip install -e '.[dev]'`, then `ruff check . && pytest` before opening a PR.
- Core logic must be testable without hardware: extend `tests/fakedevice.py` rather than skipping tests.
- If you tested on a board, add a row to `docs/HARDWARE.md`.
- Keep the default mode free of any code installed on the target.
- The suite also runs on Windows (CI job `windows`, Python 3.11 and 3.13). A test that cannot work there (a pty, a shell
  script, POSIX file modes) is skipped with `pytest.mark.skipif(os.name == "nt", reason=...)` or guards just that assertion;
  never skip a test only because it fails on Windows without finding out why.

## Tests against real MicroPython
`pytest` always runs the fast tests against a fake board. A second layer runs the same behaviours against the
real MicroPython unix port (`tests/test_real_micropython.py`); it is skipped unless a binary is available:
```
git clone --depth 1 --branch v1.26.0 https://github.com/micropython/micropython
make -C micropython/mpy-cross && make -C micropython/ports/unix submodules && make -C micropython/ports/unix
MICROPYTHON=$PWD/micropython/ports/unix/build-standard/micropython pytest tests/test_real_micropython.py -rsP
```
The unix port has no raw REPL, so file / console / sync tests run real MicroPython behind `tests/rawrepl_shim.py`
(a small script that speaks the raw-REPL framing); terminal tests use the unix port's own REPL. CI builds and
runs this on every push (the `micropython` job). Behaviour that differs from the fake board should be fixed in
the code or, if the fake is wrong, in `tests/fakedevice.py`.

## Building the Windows version
The **Windows build** workflow (`.github/workflows/windows-build.yml`) does it on `windows-latest`; run it from the Actions tab,
and it runs by itself on every push that changes `mpexplorer/`, `packaging/windows/`, the workflow or `pyproject.toml`. By hand, in an MSYS2 UCRT64 shell:
```
pacman -S --needed mingw-w64-ucrt-x86_64-{gtk3,adwaita-icon-theme,python,python-gobject,python-pip,python-setuptools,python-pyserial,python-pillow,python-cryptography,python-yaml,pyinstaller,pyinstaller-hooks-contrib}
python -m pip install --break-system-packages --no-build-isolation . "esptool>=5,<6"
pyinstaller --noconfirm --clean packaging/windows/mpexplorer.spec
dist/mpexplorer/mpexplorer-console.exe --self-test
```
`dist/mpexplorer/` holds `mpexplorer.exe` (the window) and `mpexplorer-console.exe` (the same program with a console:
`--self-test`, and the helpers the window starts, `--miniterm` and `--esptool`, which need stdout). MSYS2's Python
uses `/` as its path separator while `MSYSTEM` is set, so path code must not assume `\` on Windows.

### Build on a Windows PC (the script)
`packaging/windows/build-local.ps1` does what the workflow does, from PowerShell in the repo, and leaves the repo alone:
```
.\packaging\windows\build-local.ps1             # all tests (GUI ones too), build, check the exe: about 5 minutes
.\packaging\windows\build-local.ps1 -NoTests    # build and check only: about 2 minutes
.\packaging\windows\build-local.ps1 -Zip        # also mpexplorer-windows.zip, like the CI artifact
```
It needs MSYS2 in `D:\msys64` (else `-Msys <folder>`) with the packages above plus `zip` and `mingw-w64-ucrt-x86_64-python-pytest`;
to set that up, unpack `msys2-x86_64-latest.sfx.exe` from repo.msys2.org (`msys2.sfx.exe -y -oD:\`), then in PowerShell with
`$env:MSYSTEM = "UCRT64"`: `D:\msys64\usr\bin\bash.exe -lc "pacman -Syuu --noconfirm"` (twice) and the `pacman -S` line.
Everything goes to `D:\mpexplorer-build` (`-Out <folder>`): a venv that sees MSYS2's GTK and adds esptool and pytest-timeout,
PyInstaller's work folder, and `dist\mpexplorer\mpexplorer.exe`. The tests and the self-test run on a hidden desktop
(`packaging/windows/hidden_desktop.py`, the Windows stand-in for `xvfb-run`), so their windows do not cover the screen.
