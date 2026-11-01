# Hand checks for the CircuitPython and libraries work (2026-11-01)

Everything below passed by script or under a fake display; **none of it has been clicked in the real window**. Boards on the bench:
Pico 2 with CircuitPython (`CIRCUITPY` drive, serial `559C...8A64`), a Pico 2 with MicroPython, and a Pico W with MicroPython 1.29.0
(its files were wiped when it was switched to CircuitPython and back). Start the app from `main` (`ac9ca59` or later), after closing any old window.
Tell Claude what you see; screenshots help.

## 1. Connect to the CircuitPython Pico 2
1. Scan, pick the `Pico 2 (rp2350a)` port whose drive is `CIRCUITPY`, Connect.
2. **Expect:** the pane reads `Device: /dev/ttyACM?  Pico 2 (rp2350a)  CircuitPython 10.3.1` and lists `code.py`, `lib`, `boot_out.txt`, `settings.toml`. You cannot go above the drive's root.
3. If the drive is unmounted: **expect** a message about mounting the `CIRCUITPY` drive, not a crash.
4. Copy a small file onto it and back; rename it; delete it. (Each save restarts CircuitPython: that is normal.)
5. Select `code.py`, Run. **Expect** its output in the status/console. Open the REPL console and the terminal: both work.

## 2. Install libraries on the CircuitPython board
1. Tools > Install libraries. **Expect** the source box to start on `Adafruit bundle (CircuitPython)` and a list of about 400.
2. Search `hid`, tick `adafruit_hid`, Install. **Expect** a confirmation listing the packages and "8 file(s)", then "Done".
   The first time it downloads about 18 MB (progress bar). `lib` on the drive now holds `adafruit_hid`.
3. Switch to `Community bundle`: a shorter list (about 180). Switch to `micropython-lib`: **expect** an "Install anyway" box and the note about
   the firmware mismatch; Install stays grey until it is ticked. Do not install anything from it unless you want to.
4. Tick `adafruit_hid` again and press Remove: **expect** a confirmation, then the folder is gone.

## 3. Install libraries on a MicroPython Pico
1. Connect the Pico W (or Pico 2). Tools > Install libraries. **Expect** `micropython-lib (MicroPython)` and about 129 entries.
2. Tick `urequests`, Install. Then in the console: `import urequests` works on the Pico W (the Pico 2 has no `socket`, so it fails there; that is expected).
3. Remove it again. The "In /lib" column shows `yes` after an install, and blank after a remove.

## 4. Install firmware with a CircuitPython choice
Only do this on a board you don't mind wiping (switching firmware empties its file area).
1. Put a Pico in BOOTSEL (hold BOOTSEL while plugging in). Tools > Install firmware.
2. **Expect** a `Firmware` box (MicroPython / CircuitPython) above `Board`. Choose CircuitPython: the board list is the CircuitPython site's (about 120 for an RP2040,
   about 40 for an RP2350), and the Pico list says nothing is chosen for you. Pick the exact board, **Version** shows `10.3.1` as latest.
3. Install. **Expect** "Waiting for CircuitPython to start" (up to a minute: the first boot formats the drive), then
   `Installed: CircuitPython 10.3.1 on Raspberry Pi Pico ...`, and Connect works.
4. To go back: choose MicroPython and use the board's bootloader (Tools > Start bootloader button works on running MicroPython Picos; on a CircuitPython
   board use BOOTSEL).

## Known gaps (not bugs to report)
Clock set/read on a CircuitPython board is refused; Open with copies through the cache instead of editing the drive in place; a CircuitPython
board whose drive is not mounted cannot be connected (serial-only mode is not built); no "outdated library" check; Remove deletes everything a package
lists, even a file two packages share (`urequests` and `requests`).
