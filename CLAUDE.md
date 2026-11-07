# mpexplorer - instructions for Claude

Read `docs/HANDOFF.md` first: it is the project's memory (requirements, decisions, status, open items, lessons).
`docs/DESIGN.md` has the architecture. `CONTRIBUTING.md` has the test commands.

## What this is
A dual-pane file explorer (GTK 3 via PyGObject), REPL console and terminal for MicroPython boards, running on a **Raspberry Pi 5**
over USB serial. Targets: Pico / Pico 2 and the EMF Camp badges (2022 TiLDA Mk6, 2024 Tildagon, 2026 = same as 2024;
all ESP32-S3). **Default rule: nothing is installed on the target** - everything goes through the stock raw REPL. The one exception is
the Tools menu's firmware tools, which write or erase only a board in its bootloader, only when the user starts them (`docs/FIRMWARE_PLAN.md`).
(An opt-in RAM-only "agent mode" is designed in DESIGN.md but not built.)
It also runs on **Windows 10/11** as a portable build (GTK 3 from MSYS2, frozen with PyInstaller): `docs/WINDOWS_PLAN.md`. A session may
be on the Pi (Linux) or on the owner's Windows PC: check `Platform` in the environment before using `fuser`, `/dev/ttyACM*` or apt.

## Commands
```
pip install -e '.[dev]'
ruff check . && pytest -q                       # ~12 s, uses the fake board
MICROPYTHON=/path/to/micropython pytest tests/test_real_micropython.py -rsP --timeout=60   # real firmware (see CONTRIBUTING.md)
```
Python >= 3.11, line length 120, ruff rules E/F/W only, no type-annotation churn. Match the surrounding style.

## Layout
- Core (GUI-free, fully tested): `transport.py` (raw REPL, raw-paste, trace, input, terminal ownership), `backends.py`
  (Local + Device over snippets), `ops.py` (copy/move/delete/conflicts), `sync.py`, `jobs.py` (one worker thread owns
  the serial line), `console.py` (emulated REPL), `terminal.py` (real-REPL terminal), `badgescreen.py` (status page on a badge's screen), `clipboard.py`, `firmware.py` + `firmware_catalog.py` (install firmware), `connection.py` (one connected board's state),
  `paths.py` (settings/cache folders per OS, `portable.txt`), `winnames.py` (Windows names, drives), `extterm.py` (the board's REPL in an external terminal).
- Windows build: `packaging/windows/` (launcher, PyInstaller spec; `build-local.ps1` builds on the owner's PC with MSYS2 in
  `D:\msys64`, output in `D:\mpexplorer-build`, GUI tests on a hidden desktop: see CONTRIBUTING.md), `.github/workflows/windows-build.yml` (artifact `mpexplorer-windows`;
  `mpexplorer-console.exe --self-test` checks a build). Windows-only code takes the platform as an argument or a `windows` flag so Linux tests cover it.
- GUI, default (`mpexplorer/gtkui/`, GTK 3.24 via `python3-gi`, Wayland-native): `app.py`, `pane.py`, `editor.py`,
  `console.py`, `terminal.py` (VTE, optional), `dialogs.py`, `firmware.py`, `common.py` (import GTK only through it: it pins the versions).
  `python -m mpexplorer` runs it, or prints how to install PyGObject if it is missing. (The Tk GUI was removed on 2026-10-29;
  it is in git history.)
- GUI tests: `tests/test_gui_*.py` (all of them run in CI's `gui` job, and on Windows in the build job) build the real windows against the fake board and a temporary
  settings file. They need PyGObject and a display, so they skip on a plain Python; run them with
  `xvfb-run -a -s "-screen 0 1800x900x24" <venv python> -m pytest tests/test_gui_gtk.py`
  (the venv needs `--system-site-packages` to see python3-gi; `apt-get install gir1.2-gtk-3.0 gir1.2-vte-2.91 xvfb`
  works in the Claude sandbox; on the Pi `xvfb-run` may be missing but `Xvfb` is: see docs/HANDOFF.md). CI runs them in the `gui` job with `GUI_TESTS_REQUIRED=1`. Screenshots: render with
  `Gdk.pixbuf_get_from_window` under xvfb. Never let a test or smoke script use the real `~/.config`: pass `Settings(path)`.
- Tests: `tests/fakedevice.py` (fake board: raw REPL, raw-paste, friendly REPL, input), `ptyserial.py` + `rawrepl_shim.py`
  + `test_real_micropython.py` (real MicroPython unix port), `ansiscreen.py` (terminal screen used to read the terminal's output), `test_*.py`.

## Rules
- Keep logic out of the GUI code; put it in the GUI-free modules and test it with the fake board.
- Never claim the GUI or any hardware path works beyond what `docs/HANDOFF.md` lists as verified. Say what was
  verified (tests/CI, or by hand on which board) and what was not.
- Before a script touches a board, check its port is free (`fuser` on Linux; on Windows `tasklist` for mpexplorer and a test open/close with
  pyserial) and stop if it is held: the owner's app may be connected. On the Pi, develop in a worktree, since the desktop shortcut runs the clone's checkout.
- On Windows: write Python, regexes and paths with the Write/Edit tools, not bash heredocs or `sed` (they mangle backslashes there).
- Serial: keep DTR asserted and RTS low in `RawREPL.open()` (Tildagon corrupts long replies with DTR low). Never kill
  a connected GUI with `pkill`/SIGKILL: it leaves the badge in raw REPL with a frozen screen (Ctrl-B, Ctrl-D recovers).
- Behaviour that differs between the fake board and real MicroPython: fix the code, or the fake if the fake is wrong.
- Git: develop on a feature branch (the original was `claude/micropython-file-explorer-gui-7tsgfy`; recent fixes used
  short `fix-*` branches off `main`). **Only push to `main` or merge when the user says so**
  ("merge it into main"); then `git push origin <branch>:main` (fast-forward) or `gh pr merge N --rebase` for a PR.
  Do not open PRs unless asked.
- After a merge to main, the user usually asks "check if the CI passed": use
  `gh run list/watch/view --repo Corteil/mpexplorer` (on the Pi `gh run view --log` was blocked, use the GitHub MCP
  `get_job_logs` there; on the Windows PC it works). To run CI on the branch: `gh workflow run ci.yml --ref <branch>` (or MCP
  `actions_run_trigger`); the Windows build is `windows-build.yml`, about 6 minutes, and also starts on pushes that change the package.
- Commit messages end with the attribution lines given in the session's system-reminder (Co-Authored-By, plus
  Claude-Session if one is given).
- Do not `git checkout <file>` to undo an experiment while there are uncommitted edits in it (this lost work once). Commit first.
- Shell: foreground `sleep` is blocked; use `gh run watch` or background commands to wait.
