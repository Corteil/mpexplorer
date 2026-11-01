# Custom sources for libraries and firmware plan

Status (2026-11-01): plan agreed with the owner (decisions below), nothing built. Asked for by the owner: add custom repos for libraries and firmware,
for the makers of MicroPython and CircuitPython builds (Pimoroni, SparkFun and others), managed from one **repo manager** reachable from a button in the
Install firmware and Install libraries dialogs and from Preferences.

## Decisions (owner, 2026-11-01)
1. **One repo manager for firmware and libraries**, opened from a **Manage sources...** button inside each of the two dialogs and from a **Sources** page in
   Preferences. (No Tools menu entry.)
2. **Presets: Pimoroni and SparkFun** (Pimoroni MicroPython x2, Pimoroni `phew`, SparkFun Qwiic).
3. **Non-GitHub sites are allowed, but checked first** (see Safety: nothing is saved until the check passes and the owner has confirmed the site).
4. **Update checks: yes**, for custom sources too (see Update checks).

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
- **HTTPS only**, with certificate checking left on (the default of `urllib`). The **host allow-list** is the built-in one plus the hosts of sources the owner added.
- **Check first, then save.** Add always runs **Check** and a source is saved only if it passes:
  1. the address is HTTPS and its certificate verifies;
  2. every redirect stays on a host the owner has trusted or that is in the built-in list (a redirect elsewhere is reported, never followed silently);
  3. the answer looks like what the source type needs: a firmware source lists at least one `.uf2` / `.bin` asset and the first one's header is a valid UF2 block or ESP image
     (a ranged read of the first 512 bytes); a bundle source has the `.json` and a zip of the right name; a `package.json` has `urls` that stay on the same host and parse;
  4. sizes are inside the limits (list 16 MB, firmware 32 MB, library file 2 MB);
  5. the dialog then says in plain words what was found and **which host will be trusted**: "Add github.com/pimoroni/pimoroni-pico: 8 boards, newest v1.29.0-2" or
     "This is a site that is not GitHub: it will be asked for lists and files from now on. Trust download.example.com?". GitHub hosts need no trust prompt; any other host needs
     the owner's yes, once per host, kept in settings and shown in the manager (removable).
- **The SHA-256 of every download** is shown in the confirmation, and kept in the install record. The confirmation names the source and the repo.
- **Library files are code that will run on the board**: the confirmation already lists every file; for a custom source it also says who published them.
- Nothing is installed or written without the existing confirmation; no source runs anything on the host.
- A custom source can be removed at any time; removing it deletes its cache folder only (the trust for its host is removed with the last source that uses it).
- GitHub's anonymous API allows 60 requests an hour: one request per repo for the release list, cached on disk, and the saved list is used when offline or limited (as the other
  catalogs do). Checking all sources at once spends one request each.

## Update checks
- **Libraries:** the existing **Check for updates** button in Install libraries works for every source kind: bundles compare byte for byte with the release (as now), `mip`
  packages compare each installed file with the file their `package.json` points to, a local folder compares with the folder. The Show filter's "Updates available" and the Select
  installed switch work on the result unchanged.
- **Firmware:** the Version list marks releases newer than the running board. The running release is read from the board (`os.uname().release`, which is how the app already names
  a running board); for a custom MicroPython build the install is also recorded (`boards.installed`: source, version, SHA-256, under the chip id), so Pimoroni's `v1.29.0-2`
  compares with `v1.29.0-3` exactly, where the board alone says only `1.29.0`. The note under the list says "Newer than this board's 1.29.0: v1.29.0-2" and **never installs by itself**.
- **Manage sources > Check all** shows the newest version each source offers (one request per source, cached), so a quick look at what is new needs no board.

## Storage and UI
- Settings (validated like the others): `sources.firmware` = list of `{name, kind: "github"|"url", repo|url, pattern}`; `sources.libraries` = list of
  `{name, kind: "bundle"|"mip"|"folder", repo|url|path, prefix, for: "micropython"|"circuitpython"}`; `sources.trusted_hosts` = hosts the owner approved;
  `boards.installed` = chip id -> `{source, version, sha256}`. Presets are not stored until added.
- **One manager, three doors.** A reusable widget `SourcesManager` (GUI code in `gtkui/sources.py`, logic in a GUI-free `sources.py`) has two sections, **Firmware** and
  **Libraries**, each a list with **Add**, **Remove**, **Check** and **Check all**:
  - **Add** opens a menu: *Presets* (Pimoroni MicroPython, Pimoroni MicroPython RP2350, Pimoroni `phew`, SparkFun Qwiic; ones already added are greyed) and *Custom...*
    (a short form: name, kind, repo or URL, and the pattern or prefix, with a note for each). It runs the Check and shows the result before anything is saved.
  - **Manage sources...** button inside **Install firmware** and **Install libraries**: opens the manager as a dialog on the right section; on closing, the dialog's own source list
    (the Firmware box, the Libraries source box) is rebuilt and keeps the current choice if it still exists.
  - **Preferences > Sources** page: the same widget embedded, both sections visible, so sources can be managed with no board and no dialog open.
- The Install firmware **Firmware** box (today MicroPython / CircuitPython) and the Install libraries **source** box list the added sources after the built-in ones, each with the
  firmware it is for; the mismatch handling ("Install anyway", source files for the other firmware) applies unchanged.
- The CP / MP badge rule (docs/BOARD_LABELS_PLAN.md): a source's `for` decides which firmware an installed library or image is learned as.

## Phases
1. **GUI-free core** (`sources.py`, `custom_firmware.py`): the settings keys and trust store, the GitHub-releases firmware catalog (release listing, asset-name patterns, caching,
   the Pimoroni presets), the **Check** function (the five steps above, injectable opener), and the version comparison for update marks. Tests with fake API replies and fake hosts.
2. **Firmware dialog**: the extra Firmware entries, board and version lists, the update marks and note, `boards.installed`, install through the existing path. Real check by script on the
   bench: install a Pimoroni `picow-...-pimoroni-micropython.uf2` on the Pico W (it runs MicroPython 1.29.0), read the new version, then restore stock MicroPython.
3. **Library sources**: `cpbundle.Catalog` built from settings (SparkFun Qwiic, verified against the live release), the `mip` `package.json` resolver (Pimoroni `phew`, verified live,
   installed on the Pico W and removed again), the update check for both kinds.
4. **The manager**: `SourcesManager`, the **Manage sources...** buttons, the Preferences page, Add with presets and Custom, Check and Check all, the trust prompt. Rendered screenshots
   for the owner to approve before pushing.
5. Direct URL, local folder or zip, a custom micropython-lib index; polish, README and hand checks.

## Still open (small, plan defaults apply)
- Pattern syntax for a Custom firmware source: a regular expression with named groups `board` and `version` (default `^(?P<board>.+?)-(?P<version>v?\d[\w.]*(?:-\d+)?).*\.(uf2|bin)$`),
  shown with an example in the form and tried on the repo's real asset names by Check before saving.
- Whether a failed Check on a source already added (the repo moved) greys it with a message or removes it: default is **keep it, say why, and offer Remove**.
