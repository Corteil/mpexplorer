# CircuitPython mode and libraries plan

Status (2026-11-01): plan only, nothing built. Asked for by the owner. Facts marked "checked" were fetched on 2026-11-01; anything marked
"unverified" must be tried on a real CircuitPython board before it is relied on (none is on the bench yet).

## Goals
1. **CircuitPython mode:** work with a board running CircuitPython as well as MicroPython.
2. **Libraries:** Tools > Install libraries..., for both CircuitPython (Adafruit bundle) and MicroPython (mip / micropython-lib).

## What is different about CircuitPython (unverified unless noted)
- Most boards show a `CIRCUITPY` USB drive. Files can be edited as a plain host folder; the board restarts when a file is saved.
  So a second connection kind is needed: **a folder on the host** (no serial). Our `LocalBackend` already is one.
- It still has a serial REPL (`os.uname()` / `sys.implementation.name == "circuitpython"`), and a raw REPL (Ctrl-A). Raw-paste is
  probably missing, so the transport must fall back to plain sending (it already can).
- The drive is read-only to the board while the host has it mounted, and the reverse. Writes through the serial REPL fail with
  `OSError: [Errno 30]` unless `storage.remount("/", readonly=False)` has run (and then the host drive goes read-only). Plan: **prefer
  the drive when it is mounted on the host**, use the serial REPL only for running code, and only fall back to REPL file access
  when there is no drive (boards with USB drive off, nRF/ESP boards using web workflow).
- Auto-reload on save and `code.py` instead of `main.py` (`boot.py`, `settings.toml`, `lib/`).
- Never soft-reset assumptions: Ctrl-D reloads `code.py`; keep our "soft reset in a finally" rule but test what it does.

## Phase 1: detect and label
- On connect, run `sys.implementation.name` and keep it with the board's name (`boards.names`). Show `CircuitPython 10.x` in the pane label.
- Scan: list `CIRCUITPY` mounts (match volume label via `/proc/mounts` or `lsblk`) next to serial ports in the source list, with a
  chip-id style identity (`/CIRCUITPY/boot_out.txt` carries board name, version and UID).
- Done when: both a MicroPython and a CircuitPython board are named correctly in the list. Needs a CircuitPython board.

## Phase 2: the drive as a pane source
- A "CircuitPython drive" source in the per-pane source list (the side-by-side work already supports sources per pane). It is a
  `LocalBackend` on the mount path with the device conventions: hide `.fseventsd`, `.Trashes`, `System Volume Information`, `._*`;
  `lib/` folder shown first; sync after write (`os.sync`) before reporting done, because FAT can lose writes if unplugged.
- Open with (Geany) edits in place with no upload step; the board reloads on save. Keep the cache copy path off for these.
- Run/Console/Terminal use the serial port of the same board (paired through `boot_out.txt` UID vs. the USB serial number).
- Done when: copy, rename, delete, sync and drag between a Pico and a CircuitPython drive work by hand.

## Phase 3: serial-only CircuitPython
- Raw REPL ops through the existing transport, with the remount step for writes and a clear message when the drive is mounted
  elsewhere. Likely hand-testing only. Decide after phase 2 whether it is worth doing at all.

## Phase 4: libraries
Two catalogs, one dialog (Tools > Install libraries...), a target folder that is either the drive's or the board's `/lib`.

### CircuitPython (checked)
- Adafruit bundle: GitHub releases of `adafruit/Adafruit_CircuitPython_Bundle`; latest is `20261031` with
  `adafruit-circuitpython-bundle-{9,10,11}.x-mpy-<date>.zip` (about 18.6 MB each), `-py-` and `-examples-` zips, and
  `adafruit-circuitpython-bundle-<date>.json` (156 KB, 412 libraries). Each JSON entry has `package` (folder or single file),
  `path` (`lib/<name>`), `dependencies`, `version`, `repo` and `pypi_description`.
- The `.mpy` major version must match the board's CircuitPython major (`sys.implementation.version[0]`, also in `boot_out.txt`).
  A mismatch gives "incompatible .mpy file" at import.
- Download: the JSON for the list (small), the big zip only when the user installs something; keep it in `~/.cache/mpexplorer/libs/`.
  Zip read in place with `zipfile`; copy just the chosen libraries plus their dependencies (resolve from the JSON, recursively).
- The Community bundle (`adafruit/CircuitPython_Community_Bundle`) is the same layout: add as a second source later.
- `circuitpython.org/libraries` and `circuitpython.org/downloads` answer 403 to a plain `urllib` request: use GitHub releases, and send a
  User-Agent. GitHub API has a rate limit for anonymous use (60 per hour): cache the release answer.
- Also useful: show which installed libraries are outdated by comparing versions (needs `__version__` in the library's `.mpy`
  which cannot be read cheaply; circup does it by reading the file, so check how before promising it).

### MicroPython (checked)
- Index `https://micropython.org/pi/v2/index.json` (46 KB, 129 packages, each with `versions` keyed by `.mpy` version (`6` etc.) or `py`).
  Package file: `https://micropython.org/pi/v2/package/py/<name>/latest.json` returns hashes and file names (checked for `urequests`);
  the `.mpy` form lives under `.../package/<mpy-version>/<name>/...`. Third party packages come from GitHub (`github:org/repo`),
  which `mip` also accepts.
- Install on the host side: fetch the files on the Pi and upload them through our own transport (works with no network on the board
  and on boards without Wi-Fi; Picos). Do **not** run `mip` on the board by default. Optional later: "Install on the board" for Wi-Fi boards.
- Pick `.mpy` or `.py`: read the board's `sys.implementation._mpy` (version in the low byte, plus architecture) to choose the
  matching `.mpy`; fall back to the `py` form (always right, bigger).
- Destination `/lib` (created if missing); skip files already identical (our sync's content compare can be reused).

### Common
- Dialog: search box, list with checkboxes, dependency list shown before installing, a Remove button for installed ones, progress
  using the existing job and progress popup, and "tell the owner before writing to a board" as a confirm step.
- Installed list comes from listing `/lib` (drive or board).
- Code: `libraries.py` (catalogs, dependency resolve, download, like `firmware_catalog.py`), `gtkui/libraries.py`, tests with
  a fake opener and a fake zip.

## Order and sizes
| Phase | Needs a CircuitPython board | Size |
|---|---|---|
| 4 MicroPython libraries | No (Picos and the badges work) | Small to medium; can go first |
| 1 detect and label | Yes | Small |
| 2 drive as a source | Yes | Medium |
| 4 CircuitPython libraries | Drive (phase 2) | Medium |
| 3 serial-only | Yes | Decide later |

Suggested start: **MicroPython libraries first**, since all the hardware is on the bench and the pattern (catalog, cache, dialog)
already exists in the firmware tools; then CircuitPython once a board is available.

## Decisions (owner, 2026-11-01)
- **Test boards:** the Picos (Pico RP2040 and Pico 2 RP2350) can run CircuitPython. Switching a Pico between MicroPython and
  CircuitPython may need the flash **nuked** first (`flash_nuke.uf2`, which the Tools > Erase flash code already handles). The two
  firmwares use the flash differently (CircuitPython shows a `CIRCUITPY` FAT drive; MicroPython uses littlefs), so leftover files
  from the other one can break boot. Plan for tests: nuke, install, test, in that order, every time we switch. Each switch needs the
  owner to put the Pico in BOOTSEL; tell the owner before writing to a board and say which one. The CircuitPython UF2 comes from
  circuitpython.org/downloads (403 to plain scripts: find the file via the GitHub/S3 listing or ask the owner to download it).
  This also means the firmware installer should offer CircuitPython builds for RP2 boards (not in the firmware plan yet).
- **Bench (owner, 2026-11-01):** a second Pico 2 will be added, so one Pico 2 stays MicroPython and the other stays CircuitPython. That
  removes most of the nuking (only when a board is repurposed), and gives a real MP + CP pair for side-by-side, drag between
  them, sync, and the drive-to-serial pairing. Both Pico 2s share a USB product id; tell them apart by serial number (and by the
  chip id in `boot_out.txt` / `machine.unique_id()`), not by port number. The RP2040 Pico stays free for nuke-and-switch tests.
- **Files:** drive first (as proposed). **Everything else** (Run, Console, Terminal, board identity, version checks) goes over the serial REPL.
- **Libraries (CircuitPython):** Adafruit bundle first, Community bundle second.
- **Libraries (MicroPython):** install from the Pi (fetch on the host, upload through the transport). `mip` on the board only where
  it is the better source: that is where a package needs `mip`'s own resolution (GitHub `github:` or other URL packages with a
  `package.json`), and only on a Wi-Fi board. Even then the host can read the same `package.json` and fetch the files itself, so the
  default is the host for everything; revisit if a package cannot be resolved that way.

## Still open
- Which CircuitPython UF2 versions to test with (10.x is the newest line in the bundle that matches a stable release; 11.x bundle exists too).
- Pairing a `CIRCUITPY` drive with its serial port when two Picos are both running CircuitPython (use the UID in `boot_out.txt`).
