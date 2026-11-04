# mpexplorer

A dual-pane file explorer with a GUI for MicroPython boards (Raspberry Pi Pico / Pico 2, EMF Camp badges from 2022 (TiLDA Mk6), 2024
(Tildagon) and 2026, anything that runs MicroPython). It runs on a **Raspberry Pi 5** (or any computer with Python 3 and GTK 3) and talks to the
board over USB serial. **Nothing is installed on the board** in normal use: it works through the stock raw REPL, like `mpremote`.

![Main window: host and device panes](docs/images/main.png)

![REPL console](docs/images/console.png)

![REPL console in Terminal mode (the board's real REPL)](docs/images/terminal.png)

## Features
- Two panes, each showing the host or a board, so two boards can be open side by side: browse, sort, filter, show hidden files
- **Firmware badges and nicknames:** every board in the port list carries a **CP** (CircuitPython) or **MP** (MicroPython) badge, the
  pane label names the firmware and version, and *Device > Name this board* (or the bookmark button by the port box) gives a board a
  nickname that follows it by chip id, kept on this computer only
- **CircuitPython boards** work too: files go through the mounted `CIRCUITPY` drive, and Run, the REPL console and the terminal go over
  serial. *Open with* edits a file on the drive in place
- **Install libraries** (Tools menu): micropython-lib for MicroPython, the Adafruit and Community bundles for CircuitPython, fetched on the
  Pi and written to `/lib` (the board needs no network). *Check for updates* compares each installed library with its source, and the
  Show filter and *Select installed* act on the result. **Custom sources** (Preferences > Sources, or *Manage sources...* in the dialog)
  add a maker's bundle repo (SparkFun Qwiic), a `package.json` repo (Pimoroni `phew`), a micropython-lib style index, or a folder or
  .zip of your own libraries
- **Custom firmware sources** (Tools > Install firmware > *Manage sources...*): a maker's GitHub releases (presets for Pimoroni MicroPython,
  RP2350 and Pico LiPo) or a direct HTTPS link. A source is checked before it is saved, a site that is not GitHub is trusted only after
  you say so, and the chip check still refuses a wrong image before anything is written
- *Device > Board memory...* shows a connected board's flash chip, file system and RAM
- Copy files and folders between the panes (buttons, drag, or copy and paste). Dragging onto a folder copies into it, or moves
  into it within one pane
- Copy, cut, paste, rename, delete, new file / folder, properties, free space
- Copy from a board, disconnect it, and paste on another: the files are downloaded as you disconnect, so a large copy takes
  time then. A cut becomes a copy, because the first board has gone
- **Install firmware, back up or restore the flash, and erase it** (Tools menu), for boards in their bootloader. Firmware comes from
  micropython.org or the EMF flashers, or a file. A running Pico or Tildagon can be sent to its bootloader with a button
- Conflict handling (overwrite / skip / keep both / apply to all), progress, speed and cancel
- **REPL console** (Device > REPL console, one per board), emulated mode: run Python on the board, output streams as it arrives.
  `_` holds the last result, Ctrl+D soft-resets, Esc interrupts, Up/Down is history, Shift+Enter adds a line.
  `input()` works: while a program runs, Enter sends your line to it. **Show activity** displays every snippet
  sent to the board and every reply (file operations included), which is handy for debugging. While a command
  runs, other device operations wait. 
  **Terminal mode** (the radio button at the top of the console) is the board's real REPL: keystrokes go straight
  to it, so you get its own tab completion, history, auto-indent, paste mode (multi-line paste is wrapped
  automatically), Ctrl+C/D/E/B, `_` and `input()`. Copy and paste with Ctrl+Shift+C / Ctrl+Shift+V. While the
  terminal is active the device pane is paused (the serial line has one user at a time); switch back and your
  variables are still there.
- **Folder sync** (Tools > Sync folders): compares by size and SHA-256 (computed on the
  device, so nothing is transferred to check), shows a preview, copies only what changed, optional mirror-delete.
  `__pycache__`, `.git`, `*.pyc` are skipped. Files are written under a temporary name so a cancel never loses
  the old copy.
- Built-in text editor that saves straight back to the board; run a script and see its output; soft reset

## Install (Raspberry Pi OS)
```
sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-vte-2.91 pipx
sudo usermod -aG dialout $USER      # serial access; log out and in again
pipx install --system-site-packages git+https://github.com/Corteil/mpexplorer@v0.1.0
mpexplorer
```
Drop the `@v0.1.0` to follow the development version on `main`. `mpexplorer --version` prints the installed version, and
[CHANGELOG.md](CHANGELOG.md) lists what changed.
Plug the board in, press **Scan**, choose it in a pane's list, and press that pane's **Connect**.

The Tools menu can install firmware, back up or restore the flash, and erase it. A badge or other ESP32-S3 needs
`pipx install esptool`; a Pico or Pico 2 needs nothing extra.

The interface is GTK 3 through PyGObject (`python3-gi`, already on Raspberry Pi OS); `--system-site-packages` lets
pipx see it. `gir1.2-vte-2.91` is only needed for the console's Terminal mode.

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
The core and the GTK interface are written. The GUI has been used by hand on a Raspberry Pi 5 with a Tildagon
(see [docs/HANDOFF.md](docs/HANDOFF.md) for what is verified). Tested boards are listed in
[docs/HARDWARE.md](docs/HARDWARE.md); please add yours.
