# mpexplorer

A dual-pane file explorer with a GUI for MicroPython boards (Raspberry Pi Pico / Pico 2, EMF Camp 2022 badge, anything
that runs MicroPython). It runs on a **Raspberry Pi 5** (or any computer with Python 3 and Tkinter) and talks to the
board over USB serial. **Nothing is installed on the board**: it works through the stock raw REPL, like `mpremote`.

## Features
- Host and Device panes: browse, sort, filter, show hidden files
- Upload / download files and folders (buttons, drag between panes, or copy and paste)
- Copy, cut, paste, rename, delete, new file / folder, properties, free space
- Conflict handling (overwrite / skip / keep both / apply to all), progress, speed and cancel
- Built-in text editor that saves straight back to the board; run a script and see its output; soft reset

## Install (Raspberry Pi OS)
```
sudo apt install python3-tk pipx
sudo usermod -aG dialout $USER      # serial access; log out and in again
pipx install git+https://github.com/Corteil/mpexplorer
mpexplorer
```
Plug the board in, press **Scan**, pick the port, **Connect**.

## Development
```
pip install -e '.[dev]'
ruff check . && pytest
```
Tests use a fake board that speaks the raw REPL, so no hardware is needed. See [docs/DESIGN.md](docs/DESIGN.md).

## Status
Core and GUI are written; the GUI has not yet been run on real hardware. Tested boards are listed in
[docs/HARDWARE.md](docs/HARDWARE.md); please add yours.
