# Badge app store plan

Status (2026-11-08): plan agreed with the owner (decisions below). **Phase 1 built** on branch `app-store-core`
(`appstore.py`, `paths.data_dir`, `tests/test_appstore.py`, 41 tests): GUI-free, nothing visible changes yet. Tried
against the **live store**: all 263 apps read, 261 downloaded and unpacked (the other two, Doodle Jump and Tildagon Demo
App, have no `app.py` at the top, so the badge's own App Store refuses them too), 13 with requirements. Read-only on a
real Tildagon (OS v2.2.0, COM23): 1.1 MB free, its 8 apps listed in 5 s and matched to the store, one update found
(GPS Clock 2 -> 3). **First real install** (owner's go-ahead): that update, from the local store with no download, in
17 s: 6 files written, 3 unchanged and skipped by the on-badge SHA-256, all 9 checked on the badge against the release,
`metadata.json` now says 3 and no update is left. Found while building: Codeberg apps name their tarball by tag
(`13411001-v0.1.3.tar.gz`), and the largest app unpacks to 2.9 MB, so installs check the badge's free space first.
The owner confirmed the badge's menu showed GPS Clock 3 after a reboop.
**Phase 2 built** on the same branch (`gtkui/appstore.py`, Tools > Badge apps..., setting `appstore.folder`, file
chooser kind `appstore`, `disconnect(reset=True)`, `tests/test_gui_appstore.py`, 10 tests): the saved list at once, then
a refresh; search, Category and Show (All, Favourites, On the badge, Updates available, Not on the badge) with a count;
a star column for favourites; details with requirements met or not; Install / Update / Reinstall, Remove, Install by
code, Install all favourites, Restart the badge (disconnect with a soft reset); Refresh, Download for offline, Store
folder...; it browses and downloads with no badge, and says so for a board not running Tildagon OS. **Tried by the owner
in the real window on Windows with the Tildagon (OS v2.2.0): all working** (2026-11-08). Phase 3 came with it (the saved
list offline, *not downloaded* rows, Store folder...); still to try: a store folder copied to another computer, and the
Pi. Phase 4 left: a 2024 badge on OS 1.x, README, HAND_CHECKS round 8, HARDWARE.md. Not built: removing old releases from the store (`Store.prune` exists, but the badge does not record
which release it has, so what an installed app still needs is unknown); left until it matters.
Asked for by the owner: the badge
app store in mpexplorer: download its catalogue, browse it, and keep a **local store** on this computer so apps can be
installed onto a badge **offline** (no Wi-Fi on the badge or the computer, for example at camp).

## Decisions (owner, 2026-11-08)
1. **A dialog of its own: Tools > Badge apps...** (not a source inside Install libraries).
2. **Download for offline fetches the whole store** (about 12 MB the first time, then only new releases) with one button.
3. **The store folder is in mpexplorer's own data folder by default**, with **Store folder...** to choose any folder (a USB
   stick, to copy to another computer).
4. **Updates and reinstalls copy over the old folder, as the badge's own App Store does** (no delete first). Files a new
   version dropped can stay behind, exactly as on the badge.
5. Not asked; recommended default until the owner says otherwise: requirements **warn and ask, never block**.
6. Not asked; later if wanted: `mpexplorer --mirror-app-store <folder>` for a headless Pi.
7. **Favourites** (asked for by the owner during phase 1): a favourites list **kept in the store folder**
   (`favourites.json`), so a copied store carries the selection. It gives a **Favourites** choice in the Show filter and an
   **Install all favourites** button that installs every favourite missing from the connected badge and updates older
   ones (setting up several badges the same way). Download for offline still fetches the whole store.

## What the store is (checked 2026-11-08)
- **Catalogue:** `GET https://apps.badge.emfcamp.org/v1/apps` (the URL the badge's own App Store app uses,
  `modules/firmware_apps/app_store.py` in `emfcamp/badge-2024-software`). JSON `{"count", "items"}`, 263 apps, 150 KB.
- **An entry:** `code` (8 digits, unique; the badge's "Use Code" install), `id` (`service` github or codeberg, `owner`,
  `title` = repo name, `releaseHash` = the release's git commit), `releaseTime`, `tarballUrl`, and `manifest`:
  - `app`: `name`, `category` (a string or a list; first one counts): Apps 92, Badge 70, Media 45, Games 43, Music 6,
    Background 4, Pattern 3.
  - `metadata`: `author`, `license`, `url`, `description`, `version` (all 263), and on 13 apps `capabilities`, on 2
    `providedCapabilities` (see Requirements).
- **Tarballs** are served by the store itself (`https://apps.badge.emfcamp.org/v1/tarballs/<code>-<releaseHash>.tar.gz`),
  not by GitHub or Codeberg. A sample of 21: median 6 KB, largest 420 KB, mean 49 KB, so **the whole store is about 12 MB**.
  A tarball has one root folder (`<owner>-<repo>-<short hash>/`) holding `app.py` (or `app.mpy`), usually `tildagon.toml`,
  and whatever else the app needs. The server refused HEAD requests; GET works.
- **Which badges:** Tildagon OS badges, the 2024 Tildagon (OS 1.x, USB `16d0:120e`) and the 2026 badge (OS 2.x,
  `303a:4001`). Not the 2022 badge (TiDAL), which has its own app system.

## How the badge installs an app (its `install_app`, followed exactly)
1. Download the tarball, gunzip, find the single root folder and check `app.py` or `app.mpy` is in it.
2. Target folder: `/backgrounds` if the first category is `Background`, `/pattern` if `Pattern`, else `/apps`.
3. App folder: `<owner>_<title>` with `-` changed to `_` (no clashes across the 263).
4. Copy every file under the root into `<target>/<app folder>/`, making folders as needed.
5. Delete `tildagon.toml` if it came in the tarball; write the store's `manifest` as `tildagon.json` if the tarball had none.
6. Write `metadata.json`: `{"name": <manifest app name>, "hidden": false, "version": <manifest version>}`. The launcher reads
   this, and the Update menu compares its `version` with the store's.
- Uninstall deletes the app's folder. The badge does not delete an existing folder before an update (a `TODO` in its code),
  so files an app dropped stay behind.
- Requirements are checked with `ota.get_version()` against a minimum OS version, but the badge's code looks for the type
  `TildagonOsMinimumVersion` while the store's manifests say `TildagonOSMinimumVersion`, so the badge may never enforce it
  (worth telling EMF; not ours to fix).

## What mpexplorer would do
### The local store (a folder on this computer)
```
<store folder>/
  store.json          the catalogue exactly as fetched, plus "fetched" (time) and "source" (URL)
  tarballs/<code>-<releaseHash>.tar.gz
  files.json          per tarball: size and SHA-256, recorded when it was downloaded
  favourites.json     the favourite apps, by service/owner/repo (so a favourite survives new releases)
```
- **Refresh catalogue** fetches `store.json`. **Download for offline** fetches every tarball not already there (about 12 MB
  the first time, then only new releases), with progress and Cancel. An app installed from the store is always kept.
- Offline (the catalogue cannot be fetched), the dialog uses the last `store.json` and says when it was fetched. Apps whose
  tarball is not in the folder show as *not downloaded* and cannot be installed until you are online.
- The folder can be anywhere (a USB stick, a shared drive): copy it to another computer and point mpexplorer at it, and that
  computer can install offline too. Tarballs of older releases are removed on a refresh unless an installed app still uses one.

### Installing onto a badge (same result as the badge's own App Store)
- Unpack on the computer, not the badge (no out-of-memory installs like the badge's `install_oom`), then upload through the
  normal device backend, following the six steps above.
- **Update and reinstall** copy over the old app folder, as the badge does (decision 4): changed files are replaced,
  `tildagon.json` and `metadata.json` rewritten; files the new version no longer has are left.
- **Installed / Update available:** list `/apps`, `/backgrounds` and `/pattern` on the badge, read each `metadata.json`, and
  match folders to store entries by `<owner>_<title>`. Version compare as the badge does.
- **Remove** deletes the folder (with confirmation).
- After installing, the badge's launcher only sees new apps once its OS restarts: offer **Restart the badge** (soft reset),
  as the disconnect does with the "press reboop" page.

### Requirements (shown, not enforced)
- `TildagonOSMinimumVersion` (either spelling): compare with the badge's `ota.get_version()`; say "needs Tildagon OS 2.0.0;
  this badge has 1.9.0" and ask before installing.
- A hexpansion (`name`, `vid`, `pid`): "works with the GPS Hexpansion" (optional) or "needs the Matrix Hexpansion" (required).
- `2026 Frontboard`, and `Capability` identifiers (position, neopixels, ...): listed in the details, never block.

### The dialog (Tools > Badge apps...)
Like Install libraries: search box, **Category** and **Show** filters (All, Installed, Updates available, Not installed),
a list (Name, Author, Category, Version, On the badge), and a details pane (description, licence, link, requirements,
size, release date). Buttons: **Install** / **Update**, **Remove**, **Install by code** (the 8-digit code), and at the top
the store line ("263 apps, catalogue from 8 Nov 21:50", or "offline: catalogue from ..."), **Refresh**, **Download for
offline**, **Store folder...**. Only offered when a Tildagon OS badge is connected; otherwise it can still browse and download.

## Safety
- Fetch only from `https://apps.badge.emfcamp.org/` (catalogue and tarballs); refuse anything else (`ALLOWED`, as for libraries).
- Limits: catalogue 4 MB, tarball 4 MB compressed, 16 MB unpacked.
- Before anything is written to the badge: one root folder, `app.py` or `app.mpy` present, no absolute paths, no `..`, no
  links or devices, names Windows and the badge can store. Nothing from a tarball ever runs on the computer.
- `files.json` hashes catch a damaged or changed file in a copied store folder; such a file is downloaded again.
- Each app has its own licence (shown in the details). A store folder copied to someone else carries those apps with it.

## Build order (each phase tested before the next; GUI-free first, as elsewhere)
1. **`appstore.py` core:** catalogue fetch and cache, the store folder (`store.json`, `tarballs/`, `files.json`), download
   all, tarball checks, the install plan (target, folder, files, `tildagon.json`, `metadata.json`), install, remove,
   installed list, update check, requirements. Tests with built tarballs and the fake board (no network, no badge).
2. **The dialog** and its Tools menu entry; settings `appstore.folder`, `appstore.fetched`.
3. **Offline use:** the offline catalogue, *not downloaded* rows, Store folder..., copying a store to another computer.
4. **Real badges:** a 2024 Tildagon (OS 1.x) and a 2026 badge (OS 2.x): install, update, remove, a Background app, an app with
   a minimum OS version; compare with the badge's own App Store. Then README, HAND_CHECKS round 8, HARDWARE.md.

