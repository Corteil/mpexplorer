# Android plan

Status: **plan only, nothing built.** Written 2026-11-03 from a read of `main` (240 commits). Nothing here has been run on a phone.
Drop this file into `docs/` next to the other plans.

## Goal
An Android app (phone or tablet, USB-OTG cable to the board) that does what mpexplorer does on the Pi, minus the firmware tools:
browse the board's files, download/upload, rename, delete, new folder, copy/move on the board, edit, Run, and the emulated REPL
console. Same rule as now: **nothing is installed on the board**, everything goes through the stock raw REPL.

Not in scope: install firmware, flash backup/restore/erase (esptool and bootloader flows), CircuitPython `CIRCUITPY` drive access,
"Open with Geany", the VTE terminal.

## What the code already gives us
- **The serial seam exists.** `RawREPL(ser)` accepts any serial-like object (`read`, `in_waiting`, `write`, `flush`, `close`);
  `import serial` happens only inside `RawREPL.open()`. `tests/fakedevice.py` already plugs into the same seam, so an Android
  adapter is "one more fake".
- **Reusable unchanged:** `transport.py`, `backends.py` (`DeviceBackend`, `LocalBackend`), `ops.py`, `sync.py`, `jobs.py`,
  `console.py`, `settings.py`, `errors.py`, `boardinfo.py`, `clipboard.py`, `badgescreen.py`. About 2,000 lines, 54+ core tests.
- **`JobRunner` fits a mobile UI.** One worker thread and a `finished` queue that the UI polls: in Kivy that is
  `Clock.schedule_interval(poll, 0.1)`. No GTK-style callbacks to unpick.
- **Paths are already overridable.** `settings.py` honours `$MPEXPLORER_CONFIG`; cache dirs honour `$XDG_CACHE_HOME` /
  `$XDG_CONFIG_HOME`. Setting those to the app's private folder at start-up needs no code change.

## Coupling to fix (small, all testable on the Pi)
| Where | Problem | Fix |
|---|---|---|
| `transport.RawREPL.open` / `open_board` | builds a pyserial port | add a `serial_factory` argument (default pyserial); Android passes its own |
| `ports.py` (`list_ports`, `list_boards`, `board_id`) | uses `serial.tools.list_ports` | take a `lister` argument returning the same fields (`device`, `vid`, `pid`, `serial_number`, `manufacturer`, `product`) |
| `backends.LocalBackend.root` | `expanduser("~")` | pass the app folder in |
| `terminal.py`, `firmware.py` | reach into `repl.ser` | fine if the adapter has the same methods; firmware is not shipped |
| `__main__.py` | GTK only | add a Kivy entry point beside it |

## Design
```
Kivy UI -> JobRunner (one worker thread) -> Backend (Local = phone folder | Device = raw REPL)
                                                       |
                                          RawREPL(ser)  <- AndroidSerial (usbserial4a)  on the phone
                                                        <- pyserial                      on the Pi / desktop
```
- **UI lives in `mpexplorer/kivyui/`** and also runs on the desktop (`python -m mpexplorer --kivy`). Most UI work then happens on
  the Pi against real boards, and only the packaging and the USB adapter need a phone. This matches the project rule
  "keep logic out of the GUI code".
- **Layout:** one list with a **Phone / Board** switch (phone screens are small); a tablet layout can show two panes later.
  Plain Kivy with `RecycleView` for the file list. KivyMD is optional and adds build weight.
- **Host side = the app's own folder** (works without permissions). Getting files in and out of shared storage uses the Storage
  Access Framework (file picker for upload, "save as" for download), via `androidstorage4kivy` or `plyer`.
- **`AndroidSerial`** wraps `usbserial4a` (`usb4a` for device lists and permission). It must provide `read(n)` with the 0.1 s
  timeout the transport expects, `in_waiting`, `write`, `flush`, `close`, and `dtr` / `rts`.
- **Board quirks carry over:** Tildagon needs DTR asserted and RTS low; the 2022 badge only answers with RTS asserted
  (`open_board` already retries with the other setting). Whether `usbserial4a` lets us set both is the first thing Phase 0 checks.

## Phases
**Phase 0 - feasibility spike (half a day, a phone, a board).** A Kivy APK with one button: list USB devices, ask permission,
open the board, enter the raw REPL, show `os.uname()` and `os.listdir('/')`. **Pass =** a Tildagon and a Pico both answer, a 1 MB
file round-trips, and unplugging mid-transfer gives an error rather than a hang. Stop here if the pass fails.

**Phase 1 - seams in the core (on the Pi, CI green).** The table above. No behaviour change on the Pi; existing tests prove it.
Add tests for `AndroidSerial` against a fake `serial4a` module and for the injectable port lister.

**Phase 2 - Kivy UI on the desktop.** Connect screen (port list with the existing board labels and nicknames); file list with
sort, filter and hidden files; download, upload, rename, delete, new folder, copy/move on the board; progress bar from
`Progress` with Cancel; conflict dialog from `ConflictPolicy`; text editor that saves back; Run; emulated console. Tested with the
fake board and a Kivy smoke test under xvfb (same approach as the `gui` CI job).

**Phase 3 - Android packaging.** `android/buildozer.spec`, a small script that copies the core package into the build folder
(symlinks are unreliable with Buildozer), a USB device filter and attach intent, and a GitHub Actions job that builds a debug APK
and uploads it as an artifact. Sideload the APK; Play Store is not a goal.

**Phase 4 - storage and sharing.** Picker for upload, "save as" for download, and a Share-to-mpexplorer intent so a file from
another app can be sent to a board.

**Phase 5 - extras, only if wanted.** Folder sync (the core already does it); library install (needs HTTPS from the phone,
test `ssl` in the build); board nicknames and remembered folders (settings already exist); the badge's on-screen status page
(`badgescreen.py` works through the REPL). A Terminal mode would need `tests/ansiscreen.py` moved into the package and drawn in Kivy.

**Not planned:** WebREPL over WiFi. It avoids USB permissions, but enabling it writes config to the board, which breaks the
"nothing installed" rule. Revisit if USB-OTG proves unreliable.

## Testing
- Core: unchanged, fake board plus real MicroPython unix port.
- Adapter: fake `serial4a`.
- UI: Kivy under xvfb on the Pi/CI. Hardware: add an Android section to `docs/HAND_CHECKS.md` (Pico, Pico 2, Tildagon, 2022 badge).
- Following the project rule: state what was verified and where. Nothing counts as working on Android until it ran on a phone.

## Risks
| Risk | Why it matters | Mitigation |
|---|---|---|
| Python version | the project needs >= 3.11; python-for-android's default may be older | check the current p4a/Buildozer Python in Phase 0; lower `requires-python` only if the code allows |
| USB driver match | `usb-serial-for-android` probes known VID/PIDs; ESP32-S3 (303a) and RP2 (2e8a) CDC should match but must be tried | custom prober table if needed |
| DTR/RTS control | Tildagon corrupts long replies with DTR low; 2022 badge needs RTS | Phase 0 check; fallback is a patched adapter |
| Board power draw | some phones will not power a badge with its battery flat | note in docs; powered OTG hub |
| App lifecycle | Android pauses the app and may close the port | close cleanly on pause, reconnect on resume, show "connection lost" (already handled by the terminal code's error path) |
| Throughput | the Tildagon's output to the host is capped near 11 KB/s (HANDOFF) | same on the phone; not an Android problem |
| Build time and pain | first Buildozer build is slow and fragile | pin versions, cache in CI |

## Questions for you
1. Which phone and Android version will you test on?
2. Which board first: Tildagon, or a Pico (simpler, no badge OS to interrupt)?
3. Where should the app live: a `kivyui/` folder in this repo (recommended, shares the core and tests), or a separate repo?
4. Phone-only single list, or also a two-pane tablet layout?
