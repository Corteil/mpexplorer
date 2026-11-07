# Plan: choose local files with a file selector (libraries, firmware, Open with)

Status (2026-11-07): **plan only**, asked for by the owner after trying the Windows build. Nothing built.

## What there is today
| Place | Today | Problem |
|---|---|---|
| Tools > Install firmware, "Use a file" | `Gtk.FileChooserButton` (`gtkui/firmware.py`) | GTK's own chooser, not Windows' Explorer dialog; no remembered folder |
| Tools > Back up or restore flash | `Gtk.FileChooserDialog` (save and open) | the same |
| Library sources, kind "folder" | Choose a folder... / Choose a .zip... (`gtkui/sources.py`), only inside Preferences > Sources > Add > Custom | three dialogs deep; the path box shows Linux examples (`/home/pi/...`) on Windows |
| Tools > Install libraries | no local option at all | a file or folder on this computer must first be added as a source |
| Open with... > Custom command | a text box (`geany {file}`) | the program must be typed, with quotes, e.g. `"C:\Program Files\Geany\bin\geany.exe" {file}` |
| Preferences > File types (Add / Change), Preferences > Terminal | typed commands | the same |

## Proposal
1. **One helper for every chooser** in `gtkui/common.py`: `choose_file(parent, title, kind, filters=(), start=None, save_name=None)`
   with `kind` = open / save / folder, built on **`Gtk.FileChooserNative`** (GTK 3.20+): on Windows it opens the real Explorer
   dialog (drives, Quick access, OneDrive), on Linux the desktop's own (portal) or GTK's dialog. Returns a path or None.
   Filters as (name, patterns). All present `FileChooserDialog` / `FileChooserButton` uses move to it.
2. **Remembered folders**: setting `general.last_dirs` (kind -> folder: `firmware`, `backup`, `libraries`, `program`), so each chooser
   opens where the last one of its kind was; first time: Downloads for firmware and libraries, Program Files (Windows) or `/usr/bin`
   for a program. A remembered folder that no longer exists is ignored.
3. **Firmware**: the "Use a file" row becomes a path box plus **Browse...** (filter: `*.uf2 *.bin`, plus All files, as now). Backup
   save and restore use the helper. Typing or pasting a path still works.
4. **Libraries**: Tools > Install libraries gets **Install from this computer...** with two choices: files (`.py`, `.mpy`, several at
   once) or a folder / `.zip` (a package or a folder of libraries). It reuses `local_source.py` (the folder/zip catalog, already
   tested), shows the same confirmation (files listed, `/lib` target, the firmware mismatch rule) and writes through the same job.
   No source is saved unless the owner ticks "Keep this as a library source". The Preferences > Sources Custom form keeps its
   buttons, moved to the helper; its examples follow the platform (`C:\Users\...\my-libs` on Windows).
5. **Open with**: a **Browse...** button beside the Custom command box picks a program (Windows filter: `*.exe *.bat *.cmd`;
   Linux: any file, checked to be executable) and fills in the template: `"<path>" {file}` when the path has spaces, else
   `<path> {file}` (GUI-free `openwith.program_template(path, platform)`, tested). The same button in Preferences > File types
   (Add / Change go through the same chooser dialog) and in Preferences > Terminal (template `"<path>" {port}`, the owner adds options).
6. **Tests**: GUI-free parts (`program_template`, choosing the start folder, the local-libraries plan) with the fake board; GUI tests
   replace `choose_file` with a stub that returns a path, so no real dialog opens under xvfb or on the Windows runner.

## Order and size
1. The helper and remembered folders, firmware and backup moved to it (small).
2. Open with Browse... and `program_template`, in the chooser, File types and Terminal (small).
3. Install from this computer... in the libraries dialog (medium: a new entry in the dialog, the rest is reused).
4. Hand check on Windows and on the Pi (the native dialog differs per desktop; the portal may be missing on the Pi, where GTK's dialog
   is the fallback).

## Decided (owner, 2026-11-07)
- **The same on Linux and Windows**: every Browse... button, the remembered folders and Install from this computer... go into the
  shared GTK code, so the Pi gets them too. `Gtk.FileChooserNative` shows each system's own dialog (Explorer on Windows, the
  desktop's on Linux, GTK's dialog where there is no portal, as on the Pi), and the tests cover both.

## Questions for the owner
- Libraries: install straight from a file/folder without saving a source (proposed), or always save it as a source?
- Anything else that should get a Browse... button (the host pane's path box, Sync's folders)?
