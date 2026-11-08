# Changelog

All notable changes to mpexplorer. The format follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- Help menu: About shows the version with the build it came from (commit and date; `--version` prints the same), and
  Check for updates asks GitHub, only when chosen, whether a newer release or newer changes on main are out.
- Windows: a portable build (GTK 3 from MSYS2, PyInstaller), an external terminal for the board's REPL, and
  `packaging/windows/build-local.ps1` to build it on a PC.

## [0.1.1] - 2026-11-04

### Added
- `mpexplorer --install-desktop` adds a menu entry, desktop icon and taskbar icon for the current user, from files shipped
  in the package (no checkout needed); `--uninstall-desktop` removes them. `packaging/install-launcher.sh` now runs it.
- Release notes include install instructions for the release's tag.

## [0.1.0] - 2026-11-04

First release.

### Added
- Dual-pane file explorer (GTK 3) for MicroPython boards over USB serial: browse, copy, cut, paste, rename, delete, new
  file or folder, properties, free space, drag and drop, conflict handling with progress and cancel.
- Two boards side by side, one per pane, each with its own list and Connect; copy between boards.
- Nothing is installed on the board: everything goes through the stock raw REPL (raw-paste, adaptive upload mode).
- REPL console (emulated, with `input()` and an activity trace) and a Terminal mode that is the board's real REPL (VTE).
- Folder sync with a preview, size and SHA-256 comparison on the board, optional mirror-delete, atomic writes.
- Built-in text editor, hex and image viewers, Open with an external editor (saves upload back with a conflict check).
- CircuitPython boards, through the mounted `CIRCUITPY` drive and over serial.
- Install libraries (micropython-lib, Adafruit and Community bundles, custom sources) with update checks.
- Firmware tools: install, back up, restore and erase flash for Pico, Pico 2 and ESP32-S3 badges (needs `esptool` for the badges).
- Board nicknames, board clock setting, status page on the Tildagon's screen while connected, Preferences.
- `mpexplorer --version`.

### Tested
- 450 automated tests against a fake board, real MicroPython (unix port, v1.26.0 and v1.28.0) and the GTK windows under a virtual display.
- By hand on a Raspberry Pi 5 with a Tildagon, Pico, Pico 2, Pico W and the 2022 TiLDA badge (see `docs/HARDWARE.md` and
  `docs/HANDOFF.md` for exactly what was and was not verified).

### Known limits
- Board to host transfers on the Tildagon are capped near 11 KB/s by the board's firmware.
- Not run on real hardware: ESP32-S3 breakouts other than the badges; two boards at once on real boards is only partly hand-tested.
