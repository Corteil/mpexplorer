# mpexplorer

A dual-pane file explorer with a GUI for MicroPython boards (Raspberry Pi Pico / Pico 2, EMF Camp badges from 2022 (TiLDA Mk6), 2024
(Tildagon) and 2026, anything that runs MicroPython). It runs on a **Raspberry Pi 5** (or any computer with Python 3 and GTK 3 or Tkinter) and talks to the
board over USB serial. **Nothing is installed on the board**: it works through the stock raw REPL, like `mpremote`.

## Features
- Host and Device panes: browse, sort, filter, show hidden files
- Upload / download files and folders (buttons, drag between panes, or copy and paste)
- Copy, cut, paste, rename, delete, new file / folder, properties, free space
- Conflict handling (overwrite / skip / keep both / apply to all), progress, speed and cancel
- **REPL console** (Device > REPL console), emulated mode: run Python on the board, output streams as it arrives.
  `_` holds the last result, Ctrl+D soft-resets, Esc interrupts, Up/Down is history, Shift+Enter adds a line.
  `input()` works: while a program runs, Enter sends your line to it. **Show activity** displays every snippet
  sent to the board and every reply (file operations included), which is handy for debugging. While a command
  runs, other device operations wait. 
  **Terminal mode** (the radio button at the top of the console) is the board's real REPL: keystrokes go straight
  to it, so you get its own tab completion, history, auto-indent, paste mode (multi-line paste is wrapped
  automatically), Ctrl+C/D/E/B, `_` and `input()`. Copy and paste with Ctrl+Shift+C / Ctrl+Shift+V. While the
  terminal is active the device pane is paused (the serial line has one user at a time); switch back and your
  variables are still there.
- **Folder sync** (Tools > Sync this folder to the other pane): compares by size and SHA-256 (computed on the
  device, so nothing is transferred to check), shows a preview, copies only what changed, optional mirror-delete.
  `__pycache__`, `.git`, `*.pyc` are skipped. Files are written under a temporary name so a cancel never loses
  the old copy.
- Built-in text editor that saves straight back to the board; run a script and see its output; soft reset

## Install (Raspberry Pi OS)
```
sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-vte-2.91 pipx
sudo usermod -aG dialout $USER      # serial access; log out and in again
pipx install --system-site-packages git+https://github.com/Corteil/mpexplorer
mpexplorer
```
Plug the board in, press **Scan**, pick the port, **Connect**.

The interface is GTK 3 through PyGObject (`python3-gi`, already on Raspberry Pi OS); `--system-site-packages` lets
pipx see it. `gir1.2-vte-2.91` is only needed for the console's Terminal mode. A Tkinter interface is kept as a
fallback: `sudo apt install python3-tk`, then `mpexplorer --tk` (it is also used automatically if GTK is missing).

For a desktop launcher, create `~/.local/share/applications/mpexplorer.desktop` with `Exec=mpexplorer`,
`Icon=mpexplorer` and `Type=Application`, and copy `mpexplorer/icon.png` to
`~/.local/share/icons/hicolor/256x256/apps/mpexplorer.png`. The window reports the app id `mpexplorer`, which is how
the taskbar finds that file and its icon.

## Development
```
pip install -e '.[dev]'
ruff check . && pytest
```
Tests use a fake board that speaks the raw REPL, so no hardware is needed. See [docs/DESIGN.md](docs/DESIGN.md).

## Status
Core and both GUIs are written. The GUI has been used by hand on a Raspberry Pi 5 with a Tildagon; the GTK interface
is newer and has seen less hardware time than Tk (see [docs/HANDOFF.md](docs/HANDOFF.md) for what is verified). Tested boards are listed in
[docs/HARDWARE.md](docs/HARDWARE.md); please add yours.
