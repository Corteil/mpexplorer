# Handoff / project memory

Written so a new Claude session can take over with no other context. Update it as you work. Last updated at
`main` = `bc23eea`; all CI green (Python 3.11/3.12/3.13 + real MicroPython v1.26.0 and v1.28.0).

## The user and how they work
- Owner: Corteil (GitHub), builds EMF Camp badge hardware (see their repos `rp2350-hdmi-hexpansion`,
  `tildagon-hdmi-manager`, which are separate and were not changed). Prefers Python / MicroPython / CircuitPython.
- Workflow pattern: asks for a feature -> you build and push to the feature branch -> they say "merge it into main"
  -> then "check if the CI passed". Keep replies short and honest about what was and was not verified.
- They did **not** have a Raspberry Pi or any hardware available during the original session, so nothing has been
  tested on real boards or with a real display.

## Original request (paraphrased)
"Design/plan a file explorer for MicroPython with a GUI for the Raspberry Pi. Targets are Raspberry Pi (Picos) and the
EMF badges. Upload/download files, copy/paste, all standard explorer features." Clarified later:
- GUI and all code run on a **Raspberry Pi 5**; targets are Picos and the EMF badges (2022, 2024, 2026).
- **No code installed on targets** (an opt-in RAM-only agent mode may be planned, never flash-resident).
- 2026 badge = 2024 badge with a new front plate, same firmware repo.
- Console wanted two modes: emulated (raw REPL) and real REPL terminal; emulated one has a "show all activity" switch.

## Hardware / firmware facts (verified from the firmware repos' web pages)
- 2024 + 2026 badge: `emfcamp/badge-2024-software`, ESP32-S3, ESP-IDF 5.5.1, MicroPython submodule pinned at `e0e9fbb`
  = upstream "Bump version to 1.28.0" -> **MicroPython 1.28.0**.
- 2022 badge: `emfcamp/TiDAL-Firmware`, TiLDA Mk6, **ESP32-S3** (earlier assumption of RP2040 was wrong), ESP-IDF 4.4.1,
  MicroPython submodule at upstream commit `afceb56` (a 2022 master snapshot, not a release; version unconfirmed,
  probably ~1.19.x) plus a `micropython.diff` patch.
- Picos are RP2040 / RP2350 (RP2 time epoch is 2000; handled by probing `time.gmtime(0)`).
- To learn a board's version: `import os; print(os.uname())`.

## Repo and branches
- `Corteil/mpexplorer` (this repo). `main` is protected only by convention. Feature branch:
  `claude/micropython-file-explorer-gui-7tsgfy`. The other two repos in the original session are unrelated here.
- CI: `.github/workflows/ci.yml` (`test` matrix 3.11-3.13; `micropython` matrix v1.26.0, v1.28.0 builds the unix port,
  cached), `release.yml` (wheel on `v*` tags, never exercised), Dependabot is on (it opened PRs to bump actions).

## What is built (all in main)
File explorer core + GUI, upload/download/copy/cut/paste/rename/delete, conflicts, progress/cancel, editor, run script,
raw-paste with fallback, REPL console (emulated: `_`, Ctrl-D reset, `input()`, interrupt, activity trace), folder sync
(size + on-device SHA-256, preview, optional mirror delete, atomic writes), terminal mode (real REPL: keystrokes pass
through, `AnsiScreen`, key/paste mapping, device pane paused while active), three test layers (see DESIGN.md).

## Verified vs not verified
- Verified: core logic against the fake board (77 tests) and against real MicroPython unix port v1.26.0 / v1.28.0
  (16 tests, through `rawrepl_shim.py` for file/console/sync and the unix REPL for terminal tests).
- NOT verified: **the whole Tk GUI** (never launched), raw-paste and the real raw-REPL framing (unix port has no raw
  REPL), USB serial behaviour, ESP32-S3 and RP2 ports, DTR/RTS handling on the badges, speed, the 2022 badge's
  MicroPython. Add rows to `docs/HARDWARE.md` once tested.

## Open items / suggested next steps
1. User runs it on the Pi 5 (`sudo apt install python3-tk`; `pip install -e .`; `python -m mpexplorer`) with a Pico and a
   badge, reports breakage; fix GUI layout/bugs. Likely first problems: Tk layout, drag between panes, connect flow.
2. Get `os.uname()` from the 2022 badge; if old, add an older-toolchain CI leg (container `ubuntu:22.04`; GCC 15 breaks
   releases <= ~1.22: they abort with "stack smashing detected" on the first exception).
3. Measure transfer speed with raw-paste on/off on hardware; tune `CHUNK` (2 KB) / `WRITE_BLOCK`.
4. Agent mode (opt-in, RAM-only; design in DESIGN.md) only if measured speed demands it.
5. Nice-to-haves: saved connection profiles, release tag `v0.1.0` (exercise `release.yml`), merge/handle Dependabot PRs,
   screenshots in README, hardware matrix rows.

## Lessons learned (save yourself the time)
- The unix MicroPython port **ignores Ctrl-A** (no raw REPL). `tests/rawrepl_shim.py` supplies the framing. The pty
  helper must wait for the shim's `READY` before writing, or an early Ctrl-C (SIGINT) kills the process.
- Real MicroPython's `input()` echoes what it reads: the console must not echo it locally (it did once, fixed).
- The unix REPL has no tab completion; terminal completion is only tested on the fake board.
- Reading must never swallow bytes after the delimiter (`RawREPL._rx` buffer) - an early bug.
- Raw-REPL exec has no `OK` in raw-paste mode; the device acks the end of input with 0x04 first.
- `exec(timeout=None)` waits forever for the program's stdout but handshake steps keep a 10 s limit.
- `pytest-timeout` (60 s) is used in the real-firmware CI job so a hang fails loudly with a traceback.
- Tests that wait on the fake board must wait for a specific prompt/suffix; matching an earlier `"\n>>> "` races.
- `git checkout <file>` discards uncommitted edits to it. Commit first.
- Web access: only the repos in the session scope are readable through GitHub tools; for other public repos use
  WebFetch on the github.com pages (the API host returned 403).
- No persistent memory store exists outside the repo, so this file and CLAUDE.md are the memory. Keep them current.
