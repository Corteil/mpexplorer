# Custom sources for libraries and firmware plan

Status (2026-11-01): plan only, nothing built. Asked for by the owner: add custom repos for libraries and firmware, for the makers of MicroPython and
CircuitPython builds (Pimoroni, SparkFun and others).

## What is already covered (checked 2026-11-01)
- **Install firmware** lists every board micropython.org builds for (RP2040 25, RP2350 13: SparkFun 8, Pimoroni 2, Waveshare 4, Adafruit 4, Cytron, Seeed, Pololu,
  WIZnet, Arduino ...) and every Raspberry Pi RP board on circuitpython.org (ids with `pimoroni` 27, `sparkfun` 19, `waveshare` 37, `cytron` 9, `seeed` 9, `arduino` 10).
  So **vendor boards running stock MicroPython or CircuitPython are in the list already**.
- **Install libraries** has micropython-lib, the Adafruit bundle and the Community bundle.
- What is **not** covered is what a maker publishes itself: MicroPython builds with their own drivers baked in, and their own library repos.

## What the makers publish
| Maker | What | Where | Format (checked) |
|---|---|---|---|
| Pimoroni | MicroPython firmware with their modules (`picographics`, `pimoroni`, ...) built in | `pimoroni/pimoroni-pico` releases | `.uf2` assets named `<board>-<tag>-pimoroni-micropython.uf2`, tag `v1.29.0-2` (MicroPython version, then their revision); boards `pico`, `picow`, `pico2`, `pico2b`, `pico2_w`, `pico_plus2`, `pico_ppp`, `pico_usb` |
| Pimoroni | the same for RP2350 boards | `pimoroni/pimoroni-pico-rp2350` releases | `<board>-v1.29.0-micropython.uf2`: `pimoroni_pico_plus2`, `pimoroni_plasma2350`, `pimoroni_tiny2350`, `rpi_pico2`, `rpi_pico2b`, `rpi_pico2_w` |
| Pimoroni | libraries (e.g. `phew` web server) | `pimoroni/phew` | a `package.json` in the `mip` format: `urls: [[dest, "github:pimoroni/phew/phew/__init__.py"], ...]` (no release assets) |
| SparkFun | Qwiic sensor drivers for CircuitPython/Python | `sparkfun/qwiic_py` releases | **exactly the Adafruit bundle layout**: `qwiic-py-<N>.x-mpy-<tag>.zip`, `qwiic-py-py-<tag>.zip`, `qwiic-py-<tag>.json` (tag `20260227`) |
| SparkFun, Waveshare, Cytron, Seeed, Arduino, ... | CircuitPython builds for their boards | circuitpython.org | already listed, see above |

Two formats cover everything seen: **GitHub release assets** (firmware) and **bundle zips** (libraries, already supported by `cpbundle.py` with a repo and a
file prefix), plus **`mip` `package.json`** repos (MicroPython libraries). Anything else is a direct URL or a local file.

## Source types
### Firmware
1. **GitHub releases** (`owner/repo`): list the releases (stable first), parse each `.uf2` / `.bin` asset name into board and version with a pattern, and offer the
   boards and versions like the other lists. Default pattern `<board>-<version>...(.uf2|.bin)`; Pimoroni's two repos are presets with their own patterns.
2. **A direct URL** to one file (any HTTPS release link).
3. (Already there) a local file.
The wrong-image protection is what makes this safe to open up: `firmware.check_image` reads the whole file and refuses a wrong chip family, a partial image or an
app-only `.bin` before anything is written. Chip filtering of the list cannot use the file name; the list shows every asset and the family check decides on pick (plus an
optional first-block read, a ranged request for 512 bytes per asset, to label each asset's chip).

### Libraries
1. **Bundle-format GitHub repo** (`owner/repo` + file prefix): a new entry in the source dropdown, using the existing `cpbundle.Catalog`, whose `SOURCES` table becomes data
   from settings. SparkFun `qwiic_py` (prefix `qwiic-py`) is the preset.
2. **`mip` package repo** (`github:owner/repo[@ref]`, or an HTTPS `package.json` URL): the host reads `package.json` (`urls`, `deps`), fetches each file from
   `raw.githubusercontent.com` and uploads it through our transport, as for micropython-lib (nothing runs on the board, no Wi-Fi needed). Pimoroni `phew` is the preset.
3. **A custom micropython-lib-style index** (v2 `index.json` URL): the existing `libraries.Catalog` with a base URL. Rarely needed; later.
4. **A local folder or zip** of the owner's own libraries: no network.
Each library source carries the firmware it is for (`micropython` or `circuitpython`), so the existing mismatch handling ("Install anyway", source files for the other
firmware) applies unchanged.

## Safety rules
- **HTTPS only**, and a **host allow-list** that is the built-in one plus the hosts of sources the owner added. GitHub (`github.com`, `api.github.com`,
  `raw.githubusercontent.com`, `release-assets.githubusercontent.com`) is one click; any other host asks once, naming it, when the source is added.
- **The SHA-256 of every download** is shown in the confirmation, and kept in the install record. The confirmation names the source and the repo.
- **Library files are code that will run on the board**: the confirmation already lists every file; for a custom source it also says who published them.
- Nothing is installed or written without the existing confirmation; no source runs anything on the host.
- A custom source can be removed at any time; removing it deletes its cache folder only.
- GitHub's anonymous API allows 60 requests an hour: one request per repo for the release list, cached on disk, and the saved list is used when offline or limited (as the other
  catalogs do).

## Storage and UI
- Settings (validated like the others): `sources.firmware` = list of `{name, kind: "github"|"url", repo|url, pattern}` and `sources.libraries` = list of
  `{name, kind: "bundle"|"mip"|"folder", repo|url|path, prefix, for: "micropython"|"circuitpython"}`. Presets are not stored until added.
- **Tools > Manage sources...**: two tabs (Firmware, Libraries), a list, **Add** (a menu of presets, then "Custom ..."), **Remove**, and **Check**, which fetches the source and says
  what it found ("13 boards, newest v1.29.0-2" / "412 libraries").
- The Install firmware dialog's **Firmware** box (today MicroPython / CircuitPython) and the Install libraries **source** box list the added sources after the built-in ones.

## Phases
1. **GitHub-releases firmware catalog** (GUI-free, `custom_firmware.py`): release listing, asset-name patterns, caching, the Pimoroni presets; tests with a fake API reply.
2. **Firmware dialog integration**: the extra Firmware entries, board and version lists, install through the existing path. Real check by script on the bench: install a Pimoroni
   `picow-...-pimoroni-micropython.uf2` on the Pico W (it is on MicroPython 1.29.0) and restore stock MicroPython afterwards, as was done for CircuitPython.
3. **Library sources**: `cpbundle.Catalog` from settings (SparkFun Qwiic, verified against the live release), and the `mip` `package.json` resolver (Pimoroni `phew`, verified live, installed
   on the Pico W and removed again).
4. **Manage sources dialog**, settings, presets, the confirmation text, README.
5. Direct URL, local folder or zip, a custom index; polish.

## Questions for the owner
1. **Which presets to ship?** Plan default: Pimoroni MicroPython (both repos), Pimoroni `phew`, SparkFun Qwiic. Others you have in mind (a Pimoroni CircuitPython bundle, Waveshare, Cytron,
   Seeed, Arduino and friends mostly publish through circuitpython.org already)?
2. **Allow non-GitHub hosts** (a maker's own download site) with the one-time confirmation, or GitHub only?
3. **Updates for custom sources** like the library update check: later, or from the start? (Plan: later.)
4. **Where should "Manage sources" live?** Tools menu entry plus a button inside each dialog, or the Tools entry only?
