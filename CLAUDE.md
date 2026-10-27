# mpexplorer - instructions for Claude

Read `docs/HANDOFF.md` first: it is the project's memory (requirements, decisions, status, open items, lessons).
`docs/DESIGN.md` has the architecture. `CONTRIBUTING.md` has the test commands.

## What this is
A Tkinter dual-pane file explorer, REPL console and terminal for MicroPython boards, running on a **Raspberry Pi 5**
over USB serial. Targets: Pico / Pico 2 and the EMF Camp badges (2022 TiLDA Mk6, 2024 Tildagon, 2026 = same as 2024;
all ESP32-S3). **Default rule: nothing is ever installed on the target** - everything goes through the stock raw REPL.
(An opt-in RAM-only "agent mode" is designed in DESIGN.md but not built.)

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
  the serial line), `console.py` (emulated REPL), `terminal.py` + `screen.py` (real-REPL terminal), `clipboard.py`.
- GUI (`mpexplorer/gui/`): `app.py`, `widgets.py` (Pane, editor, dialogs), `console_window.py`, `terminal_widget.py`,
  `sync_dialog.py`. **The GUI has never been run**: the sandbox has no tkinter and no hardware. Only compileall + ruff.
- Tests: `tests/fakedevice.py` (fake board: raw REPL, raw-paste, friendly REPL, input), `ptyserial.py` + `rawrepl_shim.py`
  + `test_real_micropython.py` (real MicroPython unix port), `test_*.py`.

## Rules
- Keep logic out of Tk code; put it in the GUI-free modules and test it with the fake board.
- Never claim the GUI or any hardware path works. Say what was verified (tests/CI) and what was not.
- Behaviour that differs between the fake board and real MicroPython: fix the code, or the fake if the fake is wrong.
- Git: develop on `claude/micropython-file-explorer-gui-7tsgfy`. **Only push to `main` when the user says so**
  ("merge it into main"); then `git push origin <branch>:main` (fast-forward). Do not open PRs unless asked.
- After a merge to main, the user usually asks "check if the CI passed": use
  `gh run list/watch/view --repo Corteil/mpexplorer` (gh works; `gh run view --log` is blocked, use the GitHub MCP
  `get_job_logs` instead). To run CI on the branch: MCP `actions_run_trigger` (workflow `ci.yml`, ref = branch).
- Commit messages end with the attribution lines given in the session's system-reminder (Co-Authored-By + Claude-Session).
- Do not `git checkout <file>` to undo an experiment while there are uncommitted edits in it (this lost work once). Commit first.
- Shell: foreground `sleep` is blocked; use `gh run watch` or background commands to wait.
