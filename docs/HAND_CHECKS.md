# Hand checks (CircuitPython, libraries, badges, custom sources)

Rounds 1 to 5 were done by the owner in the real window on 2026-11-01 and passed; rounds 7 (Windows) and 8 (Badge apps) on 2026-11-08, passed; round 6 is waiting. Each first passed by script or under a fake display.
To repeat them, start the app from `main` after closing any old window. Tell Claude what you see; screenshots help.
Round 7 is for the Windows build: use the desktop shortcut *mpexplorer (test build)* (`D:\mpexplorer-build\dist\mpexplorer\mpexplorer.exe`).

| Round | Area | Result |
|-------|------|--------|
| 1 | CircuitPython Pico 2, libraries, firmware install | OK (3 problems found and fixed, see below) |
| 2 | Drive editing, update checks, filter, firmware tools | OK |
| 3 | Badges and nicknames | OK |
| 4 | Custom firmware sources | OK |
| 5 | Custom library sources | OK |
| 6 | Phase 5: folder and index library sources, exact revision, LiPo preset, board memory | not yet tried |
| 7 | The window on Windows: drives, hidden files, copies and Windows names, editor and Open with, firmware tools, badge screen, port names, high DPI | OK (owner, Windows 11, 2026-11-08) |
| 8 | Badge apps: browse, offline store, favourites, install, update, remove, by code, restart | OK on Windows 11 and the Pi (owner, 2026-11-08); a copied store folder and a badge on OS 1.x not tried |

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

## Round 7: the window on Windows (WINDOWS_PLAN.md step 5; all working, owner, test build of `main` at `75c6e69`, 2026-11-08)
Bench (second Windows 11 PC, 2026-11-08): Tildagon COM13, Pico W MicroPython 1.29.0 COM15, Pico 2 MicroPython 1.29.0 COM18,
Pico W CircuitPython 10.3.1 COM17 (`CIRCUITPY` drive G:). Port numbers can differ on your PC.

### 1. Port list and connecting
1. Press **Scan** and open a pane's port list. **Expect** no row to say `USB Serial Device`: `COM13  Tildagon  #0000`,
   `COM15  Pico / Pico 2  #1931`, `COM18  Pico / Pico 2  #3c1e`, `COM17  CircuitPython board  #5837` (a board you have connected to
   before shows the model it learned instead, such as `Pico 2 (RP2350)`), each with its **MP** or **CP** badge.
2. Connect each board in turn. **Expect** the pane label to name the port, model and firmware; files listed; Disconnect works.
3. Put the Tildagon in its bootloader (power off, hold boop while plugging in). Scan. **Expect** a row
   `ESP32-S3 bootloader: no REPL, press reboop`. Connect to it: **expect** "Connection failed" that starts by saying it is the
   bootloader port and to press reboop (or use Tools > Install firmware), not just "could not enter the raw REPL". Press reboop after.

### 2. Badge screen (Tildagon)
1. Connect the Tildagon. **Expect** its screen to show the mpexplorer page with a **green ring round the edge** (no dot at the top),
   `CONNECTED`, this PC's name, the folder and free space.
2. Copy a file to it: the ring turns **amber** while it works, then green again.
3. Disconnect. **Expect** a **red ring**, `DISCONNECTED` and `press reboop button`.
4. Connect again, choose PuTTY in Preferences > Terminal, then Device > Open in external terminal... > *Use PuTTY anyway*. **Expect** the
   badge to show `press reboop` and `when finished` on two lines. Close PuTTY, press reboop (the badge hangs, as expected), reconnect.
   Set Preferences > Terminal back to miniterm.

### 3. Host pane: drives and hidden files
1. In a host pane go **Up** from `C:\`. **Expect** a *This PC* view with one folder per drive (`C:\`, `D:\`, the `CIRCUITPY` drive...).
   You cannot create, paste or rename anything there; opening a drive goes into it.
2. Go to a folder with a hidden file (for example `C:\Users\<you>` has `NTUSER.DAT`). **Expect** it hidden until *Show hidden files* is ticked.
3. Edit > Preferences, then close and reopen the app: **expect** your settings kept (they live in `%APPDATA%\mpexplorer`).

### 4. Copies between the PC and boards
1. Copy a folder of a few files from the PC to the Pico W (button, drag, and copy/paste), then back to a new folder on the PC.
   **Expect** progress, speed and the same files at the far end. Try overwrite / skip / keep both on a second copy.
2. The same to and from the CircuitPython Pico W (it writes through drive G:; the board restarts after each save, which is normal).
3. **Names Windows cannot store:** on the Pico W, in Device > REPL console, run
   `import os; os.mkdir('/clash'); [open('/clash/' + n, 'w').close() for n in ('CON.txt', 'Test.py', 'test.py')]`.
   Copy `/clash` to the PC. **Expect** a refusal that names `CON.txt` (a name Windows reserves) and `Test.py` / `test.py`
   (names that differ only in case) and says why, before anything is written. Delete `/clash` from the board afterwards.
4. Copy between two boards side by side (one in each pane), then cut and paste within one board's pane.

### 5. Editor, Run and Open with
1. Double-click a `.py` file on the Pico W: the built-in editor opens. Change it, save, close, open again: **expect** the change.
2. Select a `.py` file, Run on the board: **expect** its output.
3. Right-click > **Open with...** on a board file: **expect** the built-in editor, *Run on the board*, the hex and image viewers, the
   Windows apps for that type (Notepad, Notepad++ ...) and *Custom command*. Open it in Notepad, change it, save: **expect** the
   status line to say it was uploaded, and the board's copy changed. Saving a `.py` file with Windows line endings (CRLF) also
   **expects** a one-time note about them in the status line.
4. *Always use this for .py files*, then double-click: it opens in that program. Remove the default again in Preferences > File types.

### 6. Firmware tools (only on a board you do not mind reflashing)
1. **UF2 drive:** hold BOOTSEL and plug in a Pico. **Expect** Tools > Install firmware to find it (the `RPI-RP2` / `RP2350` drive),
   with board and version lists. Install MicroPython: **expect** "Installed: ..." and Connect works afterwards.
2. **esptool (Tildagon):** in its bootloader (as in 1.3), Tools > Back up or restore flash... > Back up.... **Expect** progress
   and a file the size of the badge's flash. Do not erase or restore unless you mean to. The backup can hold your Wi-Fi password:
   keep it private. Press reboop afterwards.
3. Tools > Start bootloader on a running MicroPython Pico: **expect** it to reappear as a UF2 drive.

### 7. Look and feel
1. **High DPI:** Windows Settings > System > Display > Scale at 150% (and back to your usual). Restart the app. **Expect** sharp text and
   icons, nothing cut off, dialogs that fit the screen.
2. Help > About MicroPython Explorer: **expect** the version and the commit it was built from.
3. Optional: put an empty `portable.txt` next to `mpexplorer.exe`, restart: **expect** settings in a `data` folder there instead.
   Delete it again to go back.

---

## Round 8: Badge apps (docs/APP_STORE_PLAN.md; passed on Windows 11 and the Pi, owner, 2026-11-08)
1. **No badge connected:** Tools > Badge apps... **Expect** the list (about 263 apps), "No badge connected", and working search,
   Category and Show filters. Click a star: the app shows under Show > Favourites, and stays a favourite after reopening.
2. **Download for offline:** about 12 MB the first time; the Offline column empties and the top line says how many are kept.
3. **With a Tildagon connected:** the badge line names its Tildagon OS, how many apps are on it and the free space; On the badge
   shows versions, "(update)" where the store is newer, and Show > Updates available lists only those.
4. **Install, Update, Remove:** each asks first; the note says what was done; **Restart the badge** disconnects, the badge
   restarts and its menu shows the change.
5. **Install by code** with an app's 8-digit code, and **Install all favourites** (only missing or older favourites go on).
6. **Offline:** with no network, reopen the dialog: "offline: the saved list", and a kept app still installs.
7. Not tried yet: a store folder copied to another computer (Store folder... on it, offline), and a 2024 badge on Tildagon OS 1.x.

---

## Known gaps (not bugs to report)
- A CircuitPython board whose drive is not mounted cannot be connected (serial-only mode is not built).
- Remove deletes everything a package lists, even a file two packages share (`urequests` and `requests`).
- Serial-only CircuitPython (no mounted drive) and flash size for CircuitPython boards are not built.
