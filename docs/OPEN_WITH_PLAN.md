# Plan: "Open with..." - choose the program that opens a file

Status: **planned, not started.** Today double-clicking a text file (up to 256 KB) opens the built-in editor; there is no
way to pick another program, and binary or larger files are refused.

## What the user gets
- Double-click / Enter opens a file with its **default program for that file type** (built-in editor unless changed).
- Right-click > **Open with...** (also Shift+Enter, and File menu) shows a chooser: the built-in editor, "Run on the board"
  (for `.py`), a hex viewer and image viewer, every installed desktop application that handles the file type, and
  "Custom command...".
- A **"Remember for this file type"** tick sets the default. A small **File types** preferences page lists and edits the
  associations.
- Works for **host files** (launch the program directly) and **device files** (download, open, upload changes back).

## Device files: edit in place with any program
1. Download to a private cache folder (`~/.cache/mpexplorer/open/<board>/<path>`, mode 0700 dir / 0600 file), with progress
   and cancel. No size limit here (external programs can take big files); the built-in editor keeps its 256 KB limit.
2. Launch the chosen program on the local copy.
3. **Watch the copy**: Gio file monitor on the folder in GTK (editors often save by replacing the file, so watch the
   directory, not the inode), polling mtime+size every second in Tk. On a change, wait ~1 s for it to settle, then upload.
4. **Conflict check before upload**: remember the device file's size/mtime/SHA-256 at download. If the device copy changed
   since (another tool, or the board itself), ask: overwrite / keep both (saved as `name (device).ext`) / skip.
5. Status bar: "Uploaded notes.txt (3 KB)" after each save; errors in a dialog. Stop watching when the program's launcher
   process exits and no change arrives for 10 minutes, when the board disconnects, or on exit; offer "upload pending
   changes?" if the board is gone. Delete the cache copies on exit unless an upload failed.
6. Uses the job queue like every other transfer, so nothing blocks the GUI; the paused-device rules from terminal mode apply.

## Choosing and launching
- **GTK (default GUI):** `Gtk.AppChooserDialog` gives the native "Open With" list for the file's content type and its
  own "Set as default" option for free. Launch with `Gio.AppInfo.launch([Gio.File...])`. Content type from
  `Gio.content_type_guess(name, None)`.
- **Tk fallback:** a small dialog listing apps from `Gio` if PyGObject exists, else only built-ins + "Custom command".
- **Custom command:** a string with `{file}` placeholder, split with `shlex.split` and run with `subprocess.Popen(list)`,
  **never through a shell**. Example: `geany {file}`, `thonny {file}`.
- Built-ins are internal handlers: `editor` (existing), `run` (existing run-script output window), `hex` (read-only
  viewer, new, small), `image` (GdkPixbuf/Tk PhotoImage, new, small).
- Never set the executable bit on cached files and never auto-run what was downloaded; opening an executable-looking file
  with the system default asks first.

## Settings
`~/.config/mpexplorer/settings.json` (same file later used for connection profiles and the fast-mode flag):
```json
{"open_with": {"by_extension": {".py": "editor", ".png": "image", ".txt": "app:org.gnome.gedit.desktop"},
               "custom": {"geany": "geany {file}"}}}
```
Lookup order: extension -> content type -> built-in editor if the file is text -> chooser. Corrupt or missing settings
fall back to defaults and never block startup.

## Code layout
- New GUI-free `mpexplorer/openwith.py`: settings load/save, association lookup, command expansion and validation,
  the cache path rules, and `Watcher` (injectable clock and file-system functions) that decides when to upload and when
  there is a conflict. Fully unit-tested against the fake board and a temp directory.
- GUI glue in `gtkui/app.py` / `pane.py` (menu items, chooser, preferences page) and the Tk equivalents; `open_file()`
  becomes `open_with(pane, path, handler=None)`.

## Tests
- Association lookup, precedence, corrupt settings, `{file}` expansion with spaces and quotes, no-shell guarantee.
- Watcher: single save, rapid saves (debounce), replace-style saves, delete, device changed meanwhile (conflict paths),
  disconnect with pending change, cache cleanup. Download/upload round trip through the fake board.
- Real MicroPython layer: the same round trip through `rawrepl_shim.py`.
- GUI by hand on the Pi 5 (the chooser, a real external editor): add rows to `docs/HARDWARE.md`.

## Phases
1. `openwith.py` + tests (settings, lookup, commands, watcher). 2. Device download/launch/upload loop for one custom
command. 3. GTK chooser + default-for-type + context-menu entries. 4. Built-in hex and image viewers, preferences page.
5. Tk equivalent (smaller: custom command and built-ins). 6. Hardware shakedown.

## Open questions for the owner
- Auto-upload on every save, or ask once per session? (Plan: auto, with the conflict check.)
- Which external editor do you actually use on the Pi (so the first tests use it)?
- Should "Run on the board" stay a separate menu item as well? (Plan: yes.)
