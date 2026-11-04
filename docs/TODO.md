# To do

Ordered roughly by what to pick up first. Tick items off and move finished ones into `docs/HANDOFF.md` if they teach
something. State of the project: see "Where we stopped" in `docs/HANDOFF.md`.

## 1. Check GTK on real hardware (do first)
- [ ] Run `python -m mpexplorer`, connect the Tildagon, and go through: list `/`, upload, download, copy/paste on the
      device, rename, delete, new folder/file, properties, free space, soft reset.
- [ ] Editor on a device file: open, edit, Ctrl+S, close with unsaved changes (info bar), save and close.
- [ ] REPL console (emulated): run code, `input()`, Ctrl+D reset, Esc interrupt, Show activity. Then Terminal mode
      (real REPL, tab completion, paste), switch back, device pane resumes.
- [x] Does Disconnect bring the badge's own app back? No: the badge stays frozen after a plain disconnect. Preferences >
      "Restart the board when disconnecting or quitting" (off by default) fixes it; confirmed on the Tildagon 2026-10-29.
- [x] Same run on a Pico / Pico 2 (done 2026-10-30, rows in `docs/HARDWARE.md`). [ ] Still to do on the 2022 badge (DTR change may behave differently).
- [ ] Mouse in GTK windows: drag between panes, context menu, conflict dialog, sync dialog, Window menu. (The old
      virtual-mouse rig can't aim at Wayland-native windows yet: locate them by differencing `grim` screenshots.)

## 2. Toolbar buttons become icons with hover hints (GTK)
Goal: a slimmer toolbar; every icon button keeps its old name as a tooltip (and as the accessible name). Use theme
icons through `Gtk.Image.new_from_icon_name`, `Gtk.IconSize.LARGE_TOOLBAR`, flat buttons (`Gtk.ReliefStyle.NONE`).
All icon names below exist in the Pi's current theme (PiXflat); check `Gtk.IconTheme.has_icon` and fall back to the text
label if one is missing on another theme.

- [x] (Done 2026-10-29, `main` after `8fd57f2`; rendered under Xvfb with the PiXflat theme, then confirmed OK by the owner on the real display 2026-10-30.)
- [x] Add a helper in `gtkui/common.py`: `icon_button(icon_name, tooltip, callback, fallback_label)` (and `set_icon`).
- [x] Toolbar (`gtkui/app.py` `_build_toolbar`), keep Port box and keep Connect as an icon that swaps:

  | Button | Icon | Hover hint |
  |---|---|---|
  | Scan | `view-refresh` | Scan for serial ports |
  | Connect / Disconnect | `network-wired` / `network-offline` (swap with state) | Connect to the board / Disconnect |
  | Upload >> | `go-next` | Upload selected to the device |
  | << Download | `go-previous` | Download selected to the host |
  | Copy / Cut / Paste | `edit-copy` / `edit-cut` / `edit-paste` | Copy / Cut / Paste (Ctrl+C / X / V) |
  | New folder | `folder-new` | New folder (F7) |
  | Rename | `accessories-text-editor` (no `edit-rename` in this theme) | Rename (F2) |
  | Delete | `user-trash` (or `edit-delete`) | Delete (Del) |
  | Cancel (status bar) | `process-stop` | Cancel the running job |

- [x] Pane bar (`gtkui/pane.py`): Up -> `go-up` ("Parent folder, Backspace"), Refresh -> `view-refresh` ("Refresh, F5").
- [x] Editor Save -> `document-save` ("Save, Ctrl+S"); console Run / Interrupt / Clear -> `media-playback-start` /
      `process-stop` / `edit-clear` with hints ("Run, Enter", "Interrupt, Esc", "Clear output").
- [x] Show the shortcut in each hint, and keep the menus as they are (text) so nothing is lost.
- [x] Fits the 1180 px window; the window's minimum width is 857 px (2026-10-31, rendered under Xvfb with PiXflat, not re-checked with `grim`).
- [x] (The Tk GUI was removed on 2026-10-29.) README screenshots: see section 3.

## 2d. Badge status screen (`mpexplorer/badgescreen.py`)
- [x] While connected, a Tildagon's frozen screen shows a status page: CONNECTED, this computer's name, the device folder, free
      space, what is happening (Uploading x, Done, Failed) and the connect time; DISCONNECTED when the badge is not restarted.
      Preferences > "Show a status screen on the badge while connected" (`general.badge_screen`, on by default, from the next
      connect). Boards without `display.get_ctx()` are detected on the first draw and left alone. Seen on the real badge.
- [x] Confirmed OK by the owner in real use (2026-10-30); each redraw costs about 0.2-0.6 s on the serial line.

## 2a. Settings (module built; see "Settings" in `docs/DESIGN.md`)
- [x] `settings.py` + 22 tests: validated keys, atomic writes, damaged-file recovery, forward compatibility.
- [x] GTK: remember last port / folders (per board) / window size / sort / hidden; saved on exit and on change.
- [x] Edit > Preferences... (General, Settings file) in both GUIs; a damaged file is reported in the status bar.
- [x] Preferences File types page (2026-10-31).
- [ ] Preferences Connection tab (fast mode), only if agent mode is built.
- [ ] Check on the Pi: window size/position restore under Wayland, the remembered device folder on the badge.

## 2b. Open with... (plan: `docs/OPEN_WITH_PLAN.md`)
- [x] (Decided: saves upload automatically; Geany first.) Phase 1: `openwith.py` (settings, associations, `{file}` commands, upload watcher) with tests, no GUI. (Done 2026-10-30.)
- [x] Device files: download to a private cache, launch the program, upload saves back with a conflict check. (Done 2026-10-30 for Geany, via right-click; the owner confirmed it on the Pico 2. Still to try by hand: the conflict dialog, a host file, the Tildagon.)
- [x] `open_with.auto_upload` is read and the Pause/Resume button exists (owner confirmed 2026-10-31).
- [x] Open with on a badge, by hand (owner, 2026-11-04; which badge and which steps were not itemised).
- [ ] Try the conflict dialog (overwrite, keep both, skip) by hand, and Open with on an RP2040.
- [x] GTK: Open with... chooser, "remember for this type", context menu (built 2026-10-30 on branch `open-with-chooser`; tests only, not tried by hand).
- [x] Open with...: Shift+Enter; ask before opening an executable-looking file with a system app (owner confirmed 2026-10-31).
- [x] Hex and image viewers, File types preferences page (owner confirmed 2026-10-31).

## 2e. Install firmware (plan: `docs/FIRMWARE_PLAN.md`)
- [x] Phase 1: UF2 detection, image checks, copy and identify (`firmware.py`), fake drives only.
- [x] Phase 2: Tools > Install firmware... with local files (Xvfb, fake drives).
- [x] Owner installed v1.29.0 on an RP2040 Pico through the dialog (2026-10-31, reported working; its files survived).
- [x] Phase 3: board list and downloads from micropython.org, with a cache (tests and the live site; no real install from a download yet).
- [x] Owner installed a downloaded v1.29.0 on a Pico 2 (2026-10-31, reported working).
- [x] Phase 4, Tildagon: install through the dialog worked on a real badge (2026-10-31, confirmed by the owner); files survive.
- [x] A chip that does not read as a Tildagon is not offered its image (confirmed on a real Tildagon; not tried on a breakout).
- [x] Phase 5, 2022 badge: install through the dialog worked on a real badge (2026-10-31, reported by the owner).
- [x] Second 2022 badge reads the same; erase on a 2022 badge works.
- [x] Tildagon install re-run on this code (2026-10-31, reported working).
- [ ] Try a real ESP32-S3 breakout: the "does not match" message, then a micropython.org build and the erase option.
- [x] Back up flash and erase only, in the dialog (fakes only).
- [x] Real boards, from scripts using the tools' code (2026-11-01): Pico, Pico 2, Tildagon and 2022 badge backup, erase and restore, all compared.
- [x] Restore clicked through the tool on a real 2022 badge (2026-11-01, reported working).
- [ ] Click Start bootloader, Back up and Erase on a real board.
- [ ] Recording installs in the settings.

## 2f. Side-by-side boards (plan: `docs/SIDE_BY_SIDE_PLAN.md`)
- [x] Phase 1: boards are told apart by chip id for remembered folders (fake board; `unique_id` itself read on a Tildagon, Pico 2, Pico W and 2022 badge).
- [x] Hand test: the two 2022 badges each went back to their own folder (2026-11-01, owner).
- [x] Paste between boards: Pico 2 to Pico W by hand (2026-11-01, owner reports it worked). Owner reports all of it worked, including folder, cut and conflict.
- [x] Phase 2: one board's state lives in `connection.py`, with no visible change (fake board, then by hand on real boards; owner reports it worked, 2026-11-01).
- [x] Phase 3: two boards at once, a source list and Connect in each pane (fake boards).
- [x] Dragging onto a folder in the same pane moves into it (owner confirmed by hand, 2026-11-01).
- [ ] Hand test phase 3 on real boards.
- [x] One console per board, titled with the board (owner confirmed on real boards, 2026-11-01).
- [x] Each pane remembers what its list had chosen; nothing connects by itself.
- [x] Phase 4: badge screens per job, firmware tools close only their board, Sync between boards (fake boards).
- [x] Badge status line fits the round screen (text widths measured on a real Tildagon).
- [x] Phase 5: docs.
- [ ] By hand: badge screen shows Copying and Done with two boards and no longer clips; firmware tools close only their board; Sync dialog between boards.

## 2c. Agent mode (plan: `docs/AGENT_PLAN.md`)
- [x] `tools/bench.py` written and tested (fake board + real MicroPython).
- [x] Ran `tools/bench.py` on the Tildagon (2026-10-29): SLOW, tables in `docs/HARDWARE.md`. Causes measured; see "Phase 0 results"
      in `docs/AGENT_PLAN.md` (host->board can be about 10x faster with an agent, board->host is capped near 11 KB/s by the board).
- [x] Run it on a Pico and Pico 2 (2026-10-30; it now skips sizes that do not fit). [x] 2022 badge run 2026-10-31 (`docs/HARDWARE.md`); its 200-file listing stalled in the benchmark but not in a standalone test.
- [ ] Repeat the benchmark order on the 2022 badge to chase that stall.
- [ ] Then, decide with the owner whether to build an upload-focused agent first. Check data integrity at 100+ KB/s on stdin
      (the 2026-10-29 measurement only counted bytes). `agent_src.py` + `AgentLink` (CPython tests), `AgentBackend` with
      fallback, GUI "Fast mode" toggle, pipelining.
- [x] Find out why the Tildagon's stdout is capped near 11 KB/s: stdout also goes to UART0 at 115200 and the UART wait is about
      10 ms (see "Board output" in `docs/HANDOFF.md`).
- Not doing (owner, 2026-10-31): a firmware change to lift the cap (`MICROPY_HW_ENABLE_UART_REPL` 0, confirmed 1 kHz tick).
  Badge files are tiny on average, so the speed does not matter in practice.

## 2g. CircuitPython and libraries (asked for by the owner, 2026-11-01; plan in docs/CIRCUITPYTHON_PLAN.md)
- [ ] A CircuitPython mode.
- [ ] Download and install libraries on the boards, for both CircuitPython and MicroPython. (Plan written 2026-11-01; owner decisions recorded in it.)

## 3. Release and housekeeping
- [x] Release prep (2026-11-04, branch `release-0.1.0-prep`): CHANGELOG, package metadata, `--version`, release workflow checks the tag against the version and runs lint, tests and `twine check`; the stray flash backup `Backups/unicorn.md` removed from the tree and `Backups/`, `*.uf2`, `*.bin` ignored.
- [ ] Merge that branch (owner says so), then tag `v0.1.0` to exercise `release.yml` and check the GitHub release. Remove "Unreleased" from the 0.1.0 heading in `CHANGELOG.md` when tagging.
- [x] Screenshots in the README (main window, console; 2026-10-31, rendered against the fake board, path boxes set by hand for the picture).
- [x] Terminal mode screenshot in the README (2026-11-01, rendered against the fake board). (Main window screenshot redone for side-by-side, 2026-11-01.)
- [x] Deleted the leftover GitHub branches (`claude/micropython-file-explorer-gui-7tsgfy`, `handoff-pi`, `wire-settings` were fully merged; `todo-refresh` was a stale 2026-10-30 TODO snapshot), by the owner's word 2026-11-01. Only `main` and the Dependabot branch are left.
- [x] `packaging/mpexplorer.desktop` and `packaging/install-launcher.sh` (2026-11-04, install script run into a temporary HOME; launcher not clicked on the Pi's taskbar yet).

## 4. Later / ideas
- [ ] Saved connection profiles (port, last folders).
- [x] GUI tests in CI (the `gui` job, under xvfb).
- [x] Delete the Tk GUI, the `--tk` option and the Tk rules in `CLAUDE.md` (done 2026-10-29; `AnsiScreen` moved to `tests/ansiscreen.py`).
- [x] Transfer speed with raw-paste on and off measured on the Tildagon; sending now adapts per connection (`transport.py`).
      Still to tune/measure: `CHUNK` / `WRITE_BLOCK`, and a Pico and the 2022 badge.
- [x] (Owner confirmed OK 2026-10-30.) Try by hand in the GUI: Preferences > "Show modified times for device files" and "Set the board's clock (UTC)...",
      Device > "Set board time now", and that a file downloaded from the badge keeps its time (all tested only through the fake
      board, Xvfb and scripts).
- [ ] Check the other GTK dialogs with the mouse (conflict, sync, message boxes) now that GTK is the default.
- [ ] Opt-in RAM-only agent mode (design in `docs/DESIGN.md`) only if measured speed demands it.
