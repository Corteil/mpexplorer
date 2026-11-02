# Hand checks (CircuitPython, libraries, badges, custom sources)

Rounds 1 to 5 were done by the owner in the real window on 2026-11-01 and passed; round 6 is waiting. Each first passed by script or under a fake display.
To repeat them, start the app from `main` after closing any old window. Tell Claude what you see; screenshots help.

| Round | Area | Result |
|-------|------|--------|
| 1 | CircuitPython Pico 2, libraries, firmware install | OK (3 problems found and fixed, see below) |
| 2 | Drive editing, update checks, filter, firmware tools | OK |
| 3 | Badges and nicknames | OK |
| 4 | Custom firmware sources | OK |
| 5 | Custom library sources | OK |
| 6 | Phase 5: folder and index library sources, exact revision, LiPo preset, board memory | not yet tried |

Bench boards: a Pico 2 with CircuitPython (`CIRCUITPY` drive), a Pico 2 with MicroPython, a Pico W with MicroPython 1.29.0.

---

## Round 1: first checks

### 1. Connect to the CircuitPython Pico 2
1. Scan, pick the `Pico 2 (rp2350a)` port whose drive is `CIRCUITPY`, Connect.
2. **Expect:** the pane reads `Device: /dev/ttyACM?  Pico 2 (rp2350a)  CircuitPython 10.3.1` and lists `code.py`, `lib`, `boot_out.txt`, `settings.toml`. You cannot go above the drive's root.
3. If the drive is unmounted: **expect** a message about mounting the `CIRCUITPY` drive, not a crash.
4. Copy a small file onto it and back; rename it; delete it. (Each save restarts CircuitPython: that is normal.)
5. Select `code.py`, Run. **Expect** its output in the status/console. Open the REPL console and the terminal: both work.

### 2. Install libraries on the CircuitPython board
1. Tools > Install libraries. **Expect** the source box to start on `Adafruit bundle (CircuitPython)` and a list of about 400.
2. Search `hid`, tick `adafruit_hid`, Install. **Expect** a confirmation listing the packages and "8 file(s)", then "Done".
   The first time it downloads about 18 MB (progress bar). `lib` on the drive now holds `adafruit_hid`.
3. Switch to `Community bundle`: a shorter list (about 180). Switch to `micropython-lib`: **expect** an "Install anyway" box and the note about
   the firmware mismatch; Install stays grey until it is ticked. Do not install anything from it unless you want to.
4. Tick `adafruit_hid` again and press Remove: **expect** a confirmation, then the folder is gone.

### 3. Install libraries on a MicroPython Pico
1. Connect the Pico W (or Pico 2). Tools > Install libraries. **Expect** `micropython-lib (MicroPython)` and about 129 entries.
2. Tick `urequests`, Install. Then in the console: `import urequests` works on the Pico W (the Pico 2 has no `socket`, so it fails there; that is expected).
3. Remove it again. The "In /lib" column shows `yes` after an install, and blank after a remove.

### 4. Install firmware with a CircuitPython choice
Only do this on a board you don't mind wiping (switching firmware empties its file area).
1. Put a Pico in BOOTSEL (hold BOOTSEL while plugging in). Tools > Install firmware.
2. **Expect** a `Firmware` box (MicroPython / CircuitPython) above `Board`. Choose CircuitPython: the board list is the CircuitPython site's (about 120 for an RP2040,
   about 40 for an RP2350), and the Pico list says nothing is chosen for you. Pick the exact board; **Version** shows `10.3.1` as latest.
3. Install. **Expect** "Waiting for CircuitPython to start" (up to a minute: the first boot formats the drive), then
   `Installed: CircuitPython 10.3.1 on Raspberry Pi Pico ...`, and Connect works.
4. To go back: choose MicroPython and use the board's bootloader (Tools > Start bootloader works on running MicroPython Picos; on a CircuitPython
   board use BOOTSEL).

### What round 1 found (all fixed)
1. The emulated console lost the board's output after Ctrl-D: it now shows what the board prints while restarting (banner, `code.py` / `main.py` output).
2. The emulated console showed CircuitPython's terminal codes (title and colour sequences): they are stripped now (the real terminal view is unchanged).
3. The firmware tools did not list a running CircuitPython Pico W: it uses Adafruit's USB vendor id (`239a:8120`), not Raspberry Pi's. Running CircuitPython boards are now listed
   (read without `uctypes`, started into their bootloader with `RunMode.UF2`; no backup for them), and the post-install port wait accepts both ids.
4. The install-libraries confirmation now lists each library's files.

---

## Round 2: drive editing, update checks and filters
1. **Open with on the CircuitPython drive:** double-click or right-click > Open with on `code.py` on the `CIRCUITPY` pane. The program opens the file *on the drive*
   (no copy under `~/.cache/mpexplorer/open`), and saving in it reloads the board.
2. **Check for updates** (Tools > Install libraries, with some libraries installed): each installed row says `up to date`, `update available` or `incomplete`.
   Edit one installed library file on the drive to see `update available`, then Install it again to fix it.
3. **Show filter** (All, Installed, Not installed, Updates available) combined with the search box; the text next to it reads `3 of 412 shown` while something is hidden.
4. **Select installed libraries** switch: ticks every installed library that is shown (so with Show = Updates available it ticks only those). Install then updates them.
5. **Firmware tools list the running CircuitPython Pico W** (Tools > Install firmware / Back up or restore / Erase): `Pico W (RP2040) at /dev/ttyACM?` with `State: running CircuitPython`.
   Do not use Start bootloader on it remotely: the board then needs BOOTSEL or a reinstall.

## Round 3: firmware badges and nicknames
1. **Badges:** open each pane's port list: every port has a purple **CP** or teal **MP** badge (grey `??` if unknown), and the box shows the chosen port's badge. After connecting,
   the pane label names the firmware (`MicroPython 1.29.0`, `CircuitPython 10.3.1`). Reflash a Pico to the other firmware with Tools > Install firmware and look again.
2. **Nicknames:** connect a board, press the bookmark button (or Device > Name this board), type a name. The pane label, console title, Window menu and the port
   list show it (a board whose USB serial is shared, like `000000000000`, shows it only once connected). Reconnect after restarting the app. Empty removes it.
3. **Firmware tools:** running boards are listed as `[CP] "name"  ... at /dev/ttyACM?` / `[MP] ...`; after an install the main window's badge is right at once.

## Round 4: custom firmware sources
1. **Preferences > Sources**, and **Install firmware > Manage sources...**: the same list. Add > *Pimoroni MicroPython* runs a check (needs the network) and adds it.
2. Install firmware on a Pico in BOOTSEL: the **Firmware** box lists *Pimoroni MicroPython*; pick it, then your exact board (for a Pico W: `picow`) and install. After a restart,
   `import picographics` works. (Only on a board you do not mind reflashing: install stock MicroPython again afterwards from the same dialog.)
3. **Check all** shows the newest version of each source; **Custom...** with a direct `https://` link to a `.uf2` asks you to trust a site that is not GitHub.

## Round 5: custom library sources
1. **Preferences > Sources** has a *Library sources* list under the firmware one, and **Install libraries > Manage sources...** opens it. Add the presets *Pimoroni phew* and *SparkFun Qwiic*.
2. Install libraries: the source box lists them (*Pimoroni phew (MicroPython)*, *SparkFun Qwiic (CircuitPython)*). On a MicroPython board install `phew`, then in the console `import phew`.
   On a CircuitPython board pick *SparkFun Qwiic* and install one library; **Check for updates** works on both.
3. A package that needs PyPI libraries says so in the confirmation instead of failing silently.

## Round 6 (phase 5; tests and real boards by script only, **not yet clicked in the real window**)
1. **Folder or zip library source:** Preferences > Sources > Library sources > Add > Custom..., choose *A folder or .zip file on this computer*, use *Choose a folder...*
   or *Choose a .zip...*, name it, Check and add. In Install libraries pick it: each top-level `.py` or `.mpy` file and each package folder is a library. Install one,
   edit the file in your folder, Check for updates: it should say `update available`.
2. **Package index source:** Custom... > *A package index like micropython-lib's*, with the address of a folder that holds `index.json`. Asks you to trust the site.
3. **Exact Pimoroni revision:** with a Pimoroni build running (the Pico LiPo 2 XL W), Tools > Install firmware > Start bootloader, then pick *Pimoroni Pico LiPo*: the
   version matching the running one is not marked `(newer than this board)`, a later revision is.
4. **Pico LiPo preset:** Add > *Pimoroni Pico LiPo*; the boards include `pimoroni_pico_lipo2xl_w`.
5. **Board memory:** Device > Board memory... on each connected board; CircuitPython boards say the flash chip is not reported.

---

## Known gaps (not bugs to report)
- A CircuitPython board whose drive is not mounted cannot be connected (serial-only mode is not built).
- Remove deletes everything a package lists, even a file two packages share (`urequests` and `requests`).
- Serial-only CircuitPython (no mounted drive) and flash size for CircuitPython boards are not built.
