# Install firmware plan

Status (2026-10-31): phase 1 built on branch `firmware-install` (`mpexplorer/firmware.py`, `tests/test_firmware.py`): tested against fake drives; `check_uf2` accepted the real v1.29.0 Pico, Pico 2 and Pico 2 RISC-V UF2s, `detect` found a real RP2040 Pico. Phase 2 built on the same branch: Tools > Install firmware... (`gtkui/firmware.py`, `tests/test_gui_firmware.py`), local UF2 files only; tested under Xvfb with fake drives and a stubbed install. It has no erase box and does not yet record installs in the settings. The owner installed v1.29.0 on an RP2040 Pico through the dialog on 2026-10-31 and reported that it worked and its files survived (Connect not itemised). The owner then installed a downloaded v1.29.0 build on a Pico 2 through the dialog (2026-10-31, reported working), so its bootloader was recognised as RP2350. Phase 3 built: `firmware_catalog.py` reads boards (`/download/?mcu=<chip>`) and builds from micropython.org, saves lists and files in `~/.cache/mpexplorer/firmware/`; the dialog offers exact boards for the found chip, with the newest plain release as default. Phase 4 code is on branch `firmware-tildagon`: `flash_esptool`, `check_esp_image`, ROM port detection and the Tildagon in the dialog, tested with a fake esptool only; On a real Tildagon (2026-10-31): ROM USB id `303a:1001` confirmed, esptool `flash-id` (8 MB, ESP32-S3-PICO-1) and a full `read-flash` worked. On 2026-10-31 `write-flash` through the dialog worked on that badge (hash verified, 10 s), but esptool's default "hard reset via RTS" left it in the ROM, so the dialog reported a failure. `--after watchdog-reset` restarted it when tried by hand with a read-only command; the dialog now uses it, and the owner confirmed a full install on the badge end to end (2026-10-31). The file area read back identical to the backup after the write, so files survive. **OTA data proves nothing: the image blanks it, but the badge's `main.py` calls `mark_app_valid_cancel_rollback` at boot and it reads back as before. To see a write, trust esptool's hash or use an image that differs.** Phase 3 was checked against the live site (25 RP2040 and 13 RP2350 boards; v1.29.0 for Pico, Pico W, Pico 2 and Pico 2 W downloaded and passed `check_uf2`). Goal: from the app, put MicroPython on a board that has none, or has no working
firmware, then connect to it.

## Scope
- Boards: any board micropython.org builds for that the app can flash safely, see "Other boards" below; plus the Tildagon (2024/2026) and 2022 badge (TiDAL), which are not on that site.
- A dialog, Tools > Install firmware..., which works with no board connected.
- First version: only boards already in their bootloader. The dialog tells the owner how to get them there.
- Not in scope: building firmware, SWD flashing (`swd_hexpansion` does that).
- This is a deliberate, user-started exception to the "nothing is installed on the target" rule: update `CLAUDE.md` when it merges.

## How each board is flashed

| Board | Getting to the bootloader | Seen on the host as | Image | Writer |
|---|---|---|---|---|
| Pico | Hold BOOTSEL, plug in | USB drive, `2e8a:0003` | `RPI_PICO-*.uf2` from micropython.org | Copy the UF2 to the drive |
| Pico 2 | Hold BOOTSEL, plug in | USB drive, `2e8a:000f` | `RPI_PICO2-*.uf2` from micropython.org | Copy the UF2 to the drive |
| Tildagon | Power off from the menu, hold boop while plugging USB into USB in, keep holding a few seconds (owner and the EMF flasher page; the badge README's bat + boop steps did not apply) | Serial port, ESP32-S3 ROM (`303a:1001`, seen) | `merged-firmware.bin` from `emfcamp.github.io/badge-2024-software` | esptool at offset 0 |
| 2022 badge | To check | Serial port, ESP32-S3 ROM (expected `303a:1001`) | `merged-firmware.bin` from `emfcamp.github.io/TiDAL-Firmware` | esptool at offset 0 |

Checked 2026-10-31: the Tildagon image downloads (2,350,928 bytes) and its header is an ESP image for 8 MB DIO flash; the micropython.org
Pico UF2 links exist (release 1.29.0 is the newest; the two Picos here run 1.28.0); both flasher manifests write one merged image at offset 0. The 2022 image was not downloaded.

**Trap:** the GitHub releases of both badges hold `micropython.bin`, the app image only. It belongs at 0x10000 and a blank chip also
needs the bootloader, partition table and OTA data. Never offer it for a fresh install.

## Other boards
micropython.org lists 228 boards (index checked 2026-10-31), and the index has `port`, `mcu`, `vendor` and `features` filters. Each board has a page
at `/download/<BOARD_ID>/` with the same link layout as the Pico 2 page, so the catalog can be generic. Per-board `board.json` files in the
MicroPython repo (`ports/<port>/boards/<BOARD_ID>/board.json`) give `mcu`, `vendor`, `product`, `variants` and `deploy_options.flash_offset`.

Boards per port: stm32 79, esp32 45, rp2 38, nrf 23, samd 18, mimxrt 14, renesas-ra 7, and one each for esp8266, cc3200, alif and psoc-edge.

What the app can flash, by file type:

| Method | Ports | Files | Support |
|---|---|---|---|
| UF2 copy | rp2, samd, some nrf and mimxrt | `.uf2` | Yes: no extra tools |
| esptool | esp32 family, esp8266 | `.bin` at the board's `flash_offset` | Yes: with the optional esptool |
| dfu-util or st-flash | stm32 | `.dfu`, `.bin`/`.hex` | Later |
| Others (J-Link, vendor tools) | the rest | varies | Show the board's own page, no install button |

**Traps:**
- The right file differs by method: `ESP32_GENERIC_S3` pages hold `.bin`, `.uf2`, `.app-bin`, `.elf`, `.map` and `.zip`. For esptool only `.bin` is right;
  its `.uf2` needs a UF2 bootloader that a fresh ESP32-S3 does not have. Each port needs its own rule.
- The flash offset depends on the chip (0 on ESP32-S3); take it from `board.json`, never guess.
- A UF2 drive or an ESP ROM says the chip family at best, not the exact board, so the owner must pick the board from the list, filtered to the
  detected chip. The UF2 family or chip check stops a wrong-chip write, but not a wrong board of the same chip (wrong pins, wrong flash size).
- The site's HTML is not an API and can change: cache the last good catalog, and keep the local file option. Be polite: fetch a board's page only when chosen.

## Design
GUI-free `mpexplorer/firmware.py`, tested without hardware:
- `detect()`: boards in a bootloader (UF2 drives by `INFO_UF2.TXT`, ROM serial ports by USB id), reported as a chip family and method.
- `catalog()`: the board list from the micropython.org index, plus a small table in the code for the two EMF badges. Versions come from the micropython.org download page (no JSON or API: links in
  the HTML, newest release first); if the page cannot be read or parsed, fall back to a local file. The Pico 2 page lists four kinds of link:
  releases and preview builds, each for Arm and RISC-V. Offer the newest Arm release by default; previews and RISC-V only if asked for.
- `fetch()`: download to `~/.cache/mpexplorer/firmware/`, to a temporary name then rename; show the SHA-256. The page publishes no checksums, so
  this records what was installed and does not prove the download: use HTTPS, and `check_image` must read the whole file.
- `check_image(board, path)`: refuse before writing. UF2 family must match, every block valid and the block count complete. RP2040 is `E48BFF56`. The Pico 2 v1.29.0
  UF2 (checked 2026-10-31) is one `E48BFF57` block at 0x10FFFF00 (ignore it, as `swd_hexpansion` does) then 1291 `E48BFF59` (Arm) blocks; the RISC-V build
  uses `E48BFF5A`. ESP image must start `E9`, be flash size 8 MB, and fit the flash.
- `install()`: writes with progress and cancel; on ESP, `python -m esptool` as a subprocess. Add `esptool` as an optional `[firmware]`
  extra, and have the dialog say how to install it if missing. Pin the major version: its command names changed between 4 and 5.
- `verify()`: wait for the board's normal serial port, connect through `RawREPL`, read `os.uname()`, show version and machine, remember the name.

Dialog, `gtkui/firmware.py`: board list with Rescan, image choice (newest, or Choose file...), the steps for that board, Install, a progress
bar, then a result with Connect. The `Erase all files first` box is off by default.

Rules:
- Disconnect first: flashing never shares a port with the app, and the worker thread is not used for it.
- Only write to a board found in a bootloader in this run, chosen by the owner, and confirmed in the dialog.
- A UF2 goes only to a volume whose `INFO_UF2.TXT` is present, never to a path that merely looks right.
- Erase only when the box is ticked. The merged image and a UF2 leave the file area alone in normal use, but a bootloader cannot tell
  a new board from a used one, so the confirmation says so. A 2022/2024 mix-up cannot be detected: both look identical in the ROM.
- Record what was installed in the settings: board id, image name, SHA-256.

## Phases
1. **Pico and Pico 2, logic:** `detect`, `check_image`, UF2 copy, `verify`, tests with a fake drive.
2. **Dialog** for the Picos, with local files only. Owner test on both Picos.
3. **Download catalog** and cache.
4. **Tildagon:** esptool, progress parsing, cancel, tests with a fake esptool. Before writing any code, confirm the bootloader USB id and
   that the serial port works with esptool on the Pi. Owner test: keep a copy of the badge's files first.
5. **2022 badge:** find the bootloader steps and confirm flash size, then test on the second badge.
5a. **Other boards:** searchable board list, UF2 boards first, then esp32/esp8266 once tested on a generic board. A board is marked "tested" only after a real run, listed in `docs/HARDWARE.md`.
6. **Later, if wanted:** reflash a running board without the button dance (`machine.bootloader()`); read-only backup first.

## Idea: RAM-only probe for RP2 boards (not in the first version)
A small `no_flash` UF2 for each chip, copied to the BOOTSEL drive, runs from RAM and leaves the flash alone. It reports over USB serial, then
reboots to BOOTSEL. It could report the chip, flash size and whether a CYW43 wireless chip answers on its SPI pins (GP23, 24, 25, 29), which would
separate Pico from Pico W and Pico 2 from Pico 2 W. It cannot name other makers' boards. All untested, and it adds compiled binaries to the repo.
To prove first: RAM UF2s on both chips, that the CYW43 check gives no false result, and that it is harmless on a plain Pico (those pins are the LED, power-save control,
VBUS sense and the VSYS divider there). Needs a W board to test. Until then the owner picks the board.

## To check on hardware
- The ROM USB ids, and the right esptool `--before`/`--after` values. The Tildagon build used `--before=default_reset --after=no_reset`;
  check on the badge whether it needs a manual replug after the write.
- `INFO_UF2.TXT`: a real RP2040 Pico matches the source (seen 2026-10-31); a real Pico 2 was recognised as `Board-ID: RP2350` by the dialog, its other lines not read. From the bootrom source, RP2040 is fixed text in the chip: `UF2 Bootloader v3.0`,
  `Model: Raspberry Pi RP2`, `Board-ID: RPI-RP2`. RP2350 defaults to `Model: Raspberry Pi RP2350`, `Board-ID: RP2350`; a board maker can change these in
  OTP. So on an RP2040 it names the chip, not the board (a Pico and a Pico W read the same and need different UF2s): detect the chip from it, use
  the `Board-ID` to filter the list, and ask for the exact board. Other UF2 bootloaders (samd, nrf) are expected to give a more specific `Board-ID`; not checked.
- That `machine.bootloader()` works as the page says on a running Pico (it would avoid BOOTSEL, phase 6 can start with Picos).
- BOOTSEL drive mounting: on the Pi's desktop an RP2040 Pico mounted unaided at `/media/pi/RPI-RP2` (`2e8a:0003`, seen 2026-10-31, label `RPI-RP2`). Still to do: `udisksctl mount` when nothing auto-mounts.
- A fresh Tildagon has an empty file area; does it format and boot alone, or do the badge's apps need the `sponsors-vfs.bin` image
  the factory provisioning uses (`provision.json`, offset 0x4F0000)? This decides whether fresh install means one write or two.

## Tests
Fake BOOTSEL volume in a temp folder; fake esptool script (progress lines, failure, hang for cancel); bad images (wrong family, wrong size,
truncated UF2, app-only `micropython.bin`); failed download; dialog under Xvfb. Real boards by hand only, and listed in `docs/HARDWARE.md`.
