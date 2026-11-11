# Changelog

All notable changes to mpexplorer. The format follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

## [0.3.1] - 2026-11-11

### Removed
- Folder sync (Tools > Sync folders). Copying files and folders between the panes is unchanged.

### Changed
- The Connect button no longer looks greyed out when a pane has a port chosen that is there to connect to: it shows the
  same two arrows and x, dark (mpexplorer's own icon). The grey icon is kept only when there is nothing to connect to
  (Host, or a port that has gone). The button can be pressed in every case, as before.

## [0.3.0] - 2026-11-08

### Added
- **Tools > Badge apps...**: the EMF badge app store for Tildagon OS badges (the 2024 and 2026 badges). Browse its apps
  (search, Category, and Show: All, Favourites, On the badge, Updates available, Not on the badge), see what each needs, and
  install, update or remove apps on the connected badge over USB, or install one by its 8-digit code. Apps go where the
  badge's own App Store puts them (`/apps`, `/backgrounds` or `/pattern`, with its `tildagon.json` and `metadata.json`), and an
  install checks the badge has room first. *Restart the badge* disconnects with a soft reset so its menu sees new apps.
- An offline app store: *Download for offline* keeps every app (about 12 MB) in a store folder, by default in mpexplorer's
  data folder (`~/.local/share/mpexplorer/appstore`, `%LOCALAPPDATA%\mpexplorer\appstore`); *Store folder...* points it
  anywhere, for example a USB stick to copy to another computer. With no internet the saved list and kept apps are used.
- Tried on Windows 11 and a Raspberry Pi with a Tildagon on Tildagon OS 2.2.0; not yet with a badge on OS 1.x.
- **Favourites** in the app store, kept in the store folder: a Favourites filter, and *Install all favourites* puts every
  favourite the badge lacks, or has an older version of, on it in one go.

## [0.2.2] - 2026-11-08

### Fixed
- Open in external terminal on Linux: the terminal program can end at once (lxterminal passes its window to one already
  open), and mpexplorer then reconnected while miniterm still had the board, so both talked to it and miniterm showed
  mpexplorer's `>>>` and `raw REPL` traffic. mpexplorer now takes the same port lock as miniterm, and waits until miniterm
  has let go of the port before it reconnects ("still in use by the terminal: close it ... to come back here").
- miniterm showed a 2024 or 2026 badge's escape codes as symbols (such as `␛`) instead of acting on them: it now passes
  them straight to the terminal (`--filter direct`), on Linux and Windows.
- The 2022 badge ignored every keystroke in miniterm: it only answers with RTS on, which mpexplorer's own Connect knew
  but miniterm was never told. miniterm now gets RTS on for a board that needs it (never for a Tildagon, which hangs if
  its port is closed with RTS on).
- The port is never closed with RTS on, so a Tildagon cannot hang on a disconnect even if it was ever remembered as
  needing RTS. Tried on the 2022 badge, the one board that does need RTS on: no reset, and it answers every time.

## [0.2.1] - 2026-11-08

### Fixed
- PuTTY with a Tildagon: closing the port while RTS is on hangs the badge until its reset button is pressed, and PuTTY always
  keeps RTS on, so mpexplorer could not reconnect after it. Open in external terminal now warns first and offers miniterm for
  that run (Preferences keep PuTTY). A connection that fails because a board stopped answering on USB (Windows error 31) now
  says to press its reset button.
- Windows port list: every board was called "USB Serial Device (COMn)", so two boards were easy to mix up. Boards are now named
  by their USB id ("Tildagon", "Pico / Pico 2", "EMF 2022 badge") or by their firmware ("CircuitPython board"), as on Linux.
- An ESP32-S3 in its ROM bootloader (USB id `303a:1001`, which is also where a hung Tildagon showed up) is listed as
  "ESP32-S3 bootloader: no REPL, press reboop", and Connect on it says to press reboop or use Tools > Install firmware,
  instead of only "could not enter the raw REPL".

### Changed
- Badge screen: the connection state is a coloured ring round the edge of the round screen (green idle, amber busy, red
  disconnected) instead of a dot. After a disconnect it says "press reboop button", the Tildagon's name for its reset button.
  Choosing PuTTY anyway leaves "press reboop / when finished" on the badge, since it will hang when PuTTY closes.
- Preferences > Terminal lists "miniterm (the default, comes with mpexplorer)" first and only this system's presets (no
  picocom on Windows, no Tera Term on Linux), and greys out programs that are not installed, instead of letting them fail
  with "was not found".

### Tested
- On Windows 11 with a Tildagon, a Pico W and a Pico 2 on MicroPython 1.29.0, and a Pico 2 and a Pico W on CircuitPython
  10.3.1: miniterm and PuTTY (opening the port resets none of them; the REPL keys work), and the whole window by hand
  (`docs/HAND_CHECKS.md` round 7). Results in `docs/HARDWARE.md` and `docs/WINDOWS_PLAN.md`.

## [0.2.0] - 2026-11-08

### Added
- Windows 10 and 11: a portable build (`mpexplorer-0.2.0-windows.zip`, GTK 3 from MSYS2, frozen with PyInstaller), with
  pyserial, miniterm and esptool inside. Not signed yet. Tried by hand with a Tildagon; see `docs/WINDOWS_PLAN.md`.
- Open in external terminal (Device menu and the console): the board's real REPL in miniterm, PuTTY, Tera Term or a
  command of your own, reconnecting when it closes (Preferences > Terminal).
- Help menu: About shows the version with the build it came from (commit and date; `--version` prints the same), and
  Check for updates asks GitHub, only when chosen, whether a newer release or newer changes on main are out.
- File selectors for files on this computer, the system's own dialog on Linux and Windows, each opening where the last
  one of its kind chose from: the firmware file (a path box plus Browse...), flash backups, library folders and zips,
  and the program for Open with, Preferences > File types and Preferences > Terminal.
- Install libraries > From this computer: install `.py`/`.mpy` files, a folder or a `.zip` without saving a source
  first; a folder or zip can be kept as a library source with a tick.
- The sources lists (Preferences > Sources, Manage sources...) show the built-in sources above your own.
- `packaging/windows/build-local.ps1` builds and tests the Windows version on a PC, the GUI tests on a hidden desktop
  and on every core.

### Changed
- Open with names the system default program ("System default application (Notepad++)") and shows a short name for an
  app GTK only knows by its path.
- Toolbar icons: the one-colour (symbolic) icons first, so the buttons match and New folder and Rename have icons on
  Windows; the Connect button shows two arrows once connected.

### Fixed
- Windows: Preferences did not open, a saved Microsoft Store app "was not installed any more", a file on the board could
  not be opened with another program (no ':' in its cache folder), and the external terminal crashed from the window.
- Closing the window while a folder was still loading no longer opens a "Cannot open folder" box afterwards.

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
