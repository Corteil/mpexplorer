# To do

Ordered roughly by what to pick up first. Tick items off and move finished ones into `docs/HANDOFF.md` if they teach
something. State of the project: see "Where we stopped" in `docs/HANDOFF.md`.

## 1. Check GTK on real hardware (do first)
- [ ] Run `python -m mpexplorer`, connect the Tildagon, and go through: list `/`, upload, download, copy/paste on the
      device, rename, delete, new folder/file, properties, free space, soft reset.
- [ ] Editor on a device file: open, edit, Ctrl+S, close with unsaved changes (info bar), save and close.
- [ ] REPL console (emulated): run code, `input()`, Ctrl+D reset, Esc interrupt, Show activity. Then Terminal mode
      (real REPL, tab completion, paste), switch back, device pane resumes.
- [ ] Does Disconnect bring the badge's own app back? (it only leaves raw REPL today, no soft reset)
- [ ] Same run on a Pico / Pico 2, and the 2022 badge (DTR change may behave differently). Add rows to `docs/HARDWARE.md`.
- [ ] Mouse in GTK windows: drag between panes, context menu, conflict dialog, sync dialog, Window menu. (The old
      virtual-mouse rig can't aim at Wayland-native windows yet: locate them by differencing `grim` screenshots.)

## 2. Toolbar buttons become icons with hover hints (GTK)
Goal: a slimmer toolbar; every icon button keeps its old name as a tooltip (and as the accessible name). Use theme
icons through `Gtk.Image.new_from_icon_name`, `Gtk.IconSize.LARGE_TOOLBAR`, flat buttons (`Gtk.ReliefStyle.NONE`).
All icon names below exist in the Pi's current theme (PiXflat); check `Gtk.IconTheme.has_icon` and fall back to the text
label if one is missing on another theme.

- [ ] Add a helper in `gtkui/common.py`: `icon_button(icon_name, tooltip, callback, fallback_label)`.
- [ ] Toolbar (`gtkui/app.py` `_build_toolbar`), keep Port box and keep Connect as an icon that swaps:

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

- [ ] Pane bar (`gtkui/pane.py`): Up -> `go-up` ("Parent folder, Backspace"), Refresh -> `view-refresh` ("Refresh, F5").
- [ ] Editor Save -> `document-save` ("Save, Ctrl+S"); console Run / Interrupt / Clear -> `media-playback-start` /
      `process-stop` / `edit-clear` with hints ("Run, Enter", "Interrupt, Esc", "Clear output").
- [ ] Show the shortcut in each hint, and keep the menus as they are (text) so nothing is lost.
- [ ] Check it still fits the 1180 px window and the minimum width; look at it with `grim` before and after.
- [ ] Leave the Tk GUI alone (it is the fallback). Update README screenshots later.

## 2a. Settings (module built; see "Settings" in `docs/DESIGN.md`)
- [x] `settings.py` + 22 tests: validated keys, atomic writes, damaged-file recovery, forward compatibility.
- [x] GTK and Tk: remember last port / folders (per board) / window size / sort / hidden; saved on exit and on change.
- [x] Edit > Preferences... (General, Settings file) in both GUIs; a damaged file is reported in the status bar.
- [ ] Preferences tabs for File types (with Open with...) and Connection (fast mode, once agent mode exists).
- [ ] Check on the Pi: window size/position restore under Wayland, the remembered device folder on the badge.

## 2b. Open with... (plan: `docs/OPEN_WITH_PLAN.md`)
- [ ] (Decided: saves upload automatically; Geany first.) Phase 1: `openwith.py` (settings, associations, `{file}` commands, upload watcher) with tests, no GUI.
- [ ] Device files: download to a private cache, launch the program, upload saves back with a conflict check.
- [ ] GTK: Open with... chooser (`Gtk.AppChooserDialog`), "remember for this type", context menu and Shift+Enter.
- [ ] Hex and image viewers, File types preferences page, Tk fallback.

## 2c. Agent mode (plan: `docs/AGENT_PLAN.md`)
- [x] `tools/bench.py` written and tested (fake board + real MicroPython).
- [x] Ran `tools/bench.py` on the Tildagon (2026-10-29): SLOW, tables in `docs/HARDWARE.md`. Causes measured; see "Phase 0 results"
      in `docs/AGENT_PLAN.md` (host->board can be about 10x faster with an agent, board->host is capped near 11 KB/s by the board).
- [ ] Run it on a Pico and the 2022 badge too (`--sizes 64,256` if they have little free space).
- [ ] Then, decide with the owner whether to build an upload-focused agent first. Check data integrity at 100+ KB/s on stdin
      (the 2026-10-29 measurement only counted bytes). `agent_src.py` + `AgentLink` (CPython tests), `AgentBackend` with
      fallback, GUI "Fast mode" toggle, pipelining.
- [ ] Find out why the Tildagon's stdout is capped near 11 KB/s (firmware USB serial flush/tick?). Fixing that would speed up
      downloads far more than anything on our side.

## 3. Release and housekeeping
- [ ] Merge the `handoff-2026-10-28` branch (docs and `tools/`) when the owner says so.
- [ ] Tag `v0.1.0` to exercise `release.yml`, then merge Dependabot PR #2 (`softprops/action-gh-release` v3).
- [ ] Screenshots in the README (GTK window, console, terminal).
- [ ] Optional: add `mpexplorer.desktop` and the icon to the repo with an install note, instead of README text only.

## 4. Later / ideas
- [ ] Saved connection profiles (port, last folders).
- [ ] GUI smoke tests in CI (GTK needs a display: weston or xvfb). The probes in `tools/` are the starting point.
- [ ] Delete the Tk GUI (`mpexplorer/gui/`) once GTK has had enough hardware time; drop the `--tk` option and the Tk rules in `CLAUDE.md`.
- [x] Transfer speed with raw-paste on and off measured on the Tildagon; sending now adapts per connection (`transport.py`).
      Still to tune/measure: `CHUNK` / `WRITE_BLOCK`, and a Pico and the 2022 badge.
- [ ] Try by hand in the GUI: Preferences > "Show modified times for device files" and "Set the board's clock (UTC)...",
      Device > "Set board time now", and that a file downloaded from the badge keeps its time (all tested only through the fake
      board, Xvfb and scripts).
- [ ] Check the other GTK dialogs with the mouse (conflict, sync, message boxes) now that GTK is the default.
- [ ] Opt-in RAM-only agent mode (design in `docs/DESIGN.md`) only if measured speed demands it.
