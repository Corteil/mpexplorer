"""The badge app store (Tildagon OS: the 2024 and 2026 badges), fetched on this computer and installed over USB.

Plan and decisions: docs/APP_STORE_PLAN.md. Nothing runs on the board but the file writes: the catalogue and each app's
tarball are downloaded here, kept in a local store folder so apps can be installed offline, unpacked here, and written
through any backend, following the badge's own App Store (`install_app` in emfcamp/badge-2024-software,
modules/firmware_apps/app_store.py, read 2026-11-08):

- the catalogue is `GET https://apps.badge.emfcamp.org/v1/apps`: `{"count", "items"}`, each item `code`, `id`
  (`service`, `owner`, `title`, `releaseHash`), `releaseTime`, `tarballUrl` and `manifest` (`app`: name, category;
  `metadata`: author, license, url, description, version, and sometimes `capabilities` / `providedCapabilities`);
- an app goes to `/backgrounds` if its first category is Background, `/pattern` if Pattern, else `/apps`, in a folder
  named `<owner>_<title>` with '-' made '_';
- the tarball's single root folder is copied into that folder, `tildagon.toml` is dropped, the store's manifest is
  written as `tildagon.json` unless the tarball has one, and `metadata.json` holds `name`, `hidden` and `version`.

The local store folder:
    store.json        the catalogue as fetched, with "source" and "fetched"
    tarballs/         <code>-<releaseHash>.tar.gz, named as on the server
    files.json        {tarball name: {"size", "sha256", "fetched"}}, to catch a damaged copy
    favourites.json   {"apps": ["github/owner/title", ...]}
"""
import gzip
import hashlib
import io
import json
import os
import posixpath
import re
import tarfile
import time
import urllib.error
import urllib.request

from . import paths
from .errors import BackendError

LISTING = "https://apps.badge.emfcamp.org/v1/apps"
ALLOWED = ("https://apps.badge.emfcamp.org/",)
TIMEOUT = 30
MAX_CATALOGUE = 4 << 20
MAX_TARBALL = 4 << 20           # compressed; the largest seen was 420 KB (2026-11-08)
MAX_UNPACKED = 16 << 20
MAX_METADATA = 8 << 10          # a metadata.json is about 60 bytes; anything bigger is not one
READ_CHUNK = 2048               # bytes per read from the board when comparing without hashlib
TARGETS = {"Background": "backgrounds", "Pattern": "pattern"}
APPS = "apps"
TARGET_NAMES = (APPS, "backgrounds", "pattern")     # where the badge's launcher looks
# <code>-<release>.tar.gz: a git hash for GitHub apps, a tag such as v0.1.3 for Codeberg ones (64 of 263, 2026-11-08)
TARBALL_NAME = re.compile(r"^[0-9]+-(?!.*\.\.)[A-Za-z0-9][A-Za-z0-9._-]{0,99}\.tar\.gz$")
SAFE_FOLDER = re.compile(r"^[A-Za-z0-9_.]+$")


class AppStoreError(BackendError):
    pass


def default_folder():
    """Where the local store lives unless the owner chose another folder (decision 3)."""
    return os.path.join(paths.data_dir(), "appstore")


def _open_url(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "mpexplorer"}), timeout=TIMEOUT)


def _now_iso(clock):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(clock()))


def _write_atomic(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".part", "wb") as f:
        f.write(data)
    os.replace(path + ".part", path)


# -- the catalogue --------------------------------------------------------------------------------------------------
class App:
    """One catalogue entry. `entry` is the raw item, kept for store.json and the manifest written as tildagon.json."""

    def __init__(self, entry):
        ident, manifest = entry["id"], entry["manifest"]
        app, meta = manifest["app"], manifest["metadata"]
        self.entry = entry
        self.code = str(entry["code"])
        self.service, self.owner, self.title = ident.get("service", ""), ident["owner"], ident["title"]
        self.release_hash = ident.get("releaseHash", "")
        self.release_time = entry.get("releaseTime", "")
        self.tarball_url = entry["tarballUrl"]
        self.name = app["name"]
        category = app.get("category")
        self.categories = [c for c in (category if isinstance(category, list) else [category]) if c]
        self.category = self.categories[0] if self.categories else ""
        self.author = meta.get("author") or meta.get("maintainer") or self.owner
        self.license = meta.get("license", "")
        self.url = meta.get("url", "")
        self.description = meta.get("description", "")
        self.version = str(meta["version"])
        self.capabilities = meta.get("capabilities") or []
        self.provided = meta.get("providedCapabilities") or []
        self.folder = ("%s_%s" % (self.owner, self.title)).replace("-", "_")
        self.target = TARGETS.get(self.category, APPS)
        self.key = "%s/%s/%s" % (self.service, self.owner, self.title)     # stays the same across releases
        base = posixpath.basename(self.tarball_url.split("?", 1)[0])
        self.tarball_name = base if TARBALL_NAME.match(base) else None

    @property
    def manifest(self):
        return self.entry["manifest"]

    def __repr__(self):
        return "App(%r %s)" % (self.name, self.version)


def parse_catalogue(data):
    """(apps sorted by name, entries skipped as malformed or unsafe). Raises when it is not a catalogue at all."""
    try:
        items = json.loads(data)["items"]
        if not isinstance(items, list):
            raise TypeError
    except (ValueError, KeyError, TypeError):
        raise AppStoreError("The app store catalogue is not in the expected format.")
    apps, skipped = [], 0
    for item in items:
        try:
            app = App(item)
        except (KeyError, TypeError, AttributeError):
            skipped += 1
            continue
        if not (app.tarball_url.startswith(ALLOWED) and app.tarball_name and SAFE_FOLDER.match(app.folder)):
            skipped += 1                            # downloads only from the store; folder names the badge can hold
            continue
        apps.append(app)
    return sorted(apps, key=lambda a: a.name.lower()), skipped


# -- the local store folder -----------------------------------------------------------------------------------------
class Store:
    def __init__(self, folder=None, opener=_open_url, clock=time.time, listing=LISTING):
        self.folder = folder or default_folder()
        self.opener, self.clock, self.listing = opener, clock, listing
        self.catalogue_path = os.path.join(self.folder, "store.json")
        self.files_path = os.path.join(self.folder, "files.json")
        self.favourites_path = os.path.join(self.folder, "favourites.json")
        self.tarball_dir = os.path.join(self.folder, "tarballs")

    def _read(self, url, limit):
        if not url.startswith(ALLOWED):
            raise AppStoreError("Refusing to download from %s" % url)
        try:
            with self.opener(url) as reply:
                if not reply.geturl().startswith(ALLOWED):
                    raise AppStoreError("The download was redirected to %s" % reply.geturl())
                data = reply.read(limit + 1)
        except (OSError, urllib.error.HTTPError, ValueError) as e:
            raise AppStoreError("Cannot read %s: %s" % (url, e))
        if len(data) > limit:
            raise AppStoreError("The download from %s is too large." % url)
        return data

    def _json(self, path, default):
        try:
            with open(path, "rb") as f:
                return json.loads(f.read())
        except (OSError, ValueError):
            return default

    # the catalogue
    def refresh(self):
        """Fetch the catalogue and save it; returns the apps."""
        data = self._read(self.listing, MAX_CATALOGUE)
        apps, _skipped = parse_catalogue(data)
        saved = json.loads(data)
        saved.update(source=self.listing, fetched=_now_iso(self.clock))
        _write_atomic(self.catalogue_path, json.dumps(saved).encode("utf-8"))
        return apps

    def load(self):
        """(apps, fetched) from the saved catalogue; AppStoreError if there is none."""
        try:
            with open(self.catalogue_path, "rb") as f:
                data = f.read()
        except OSError:
            raise AppStoreError("There is no saved copy of the app store in %s." % self.folder)
        apps, _skipped = parse_catalogue(data)
        return apps, json.loads(data).get("fetched", "")

    def catalogue(self):
        """(apps, fetched, online): a fresh catalogue when the store can be reached, else the saved one (offline)."""
        try:
            return self.refresh(), self.fetched(), True
        except AppStoreError as e:
            try:
                apps, fetched = self.load()
            except AppStoreError:
                raise e
            return apps, fetched, False

    def fetched(self):
        return self._json(self.catalogue_path, {}).get("fetched", "")

    # tarballs
    def tarball_path(self, app):
        return os.path.join(self.tarball_dir, app.tarball_name)

    def has(self, app):
        """True when the app's tarball is here and matches what was recorded when it was downloaded."""
        path = self.tarball_path(app)
        try:
            with open(path, "rb") as f:
                data = f.read(MAX_TARBALL + 1)
        except OSError:
            return False
        record = self._json(self.files_path, {}).get(app.tarball_name)
        if record is None:                          # a store folder copied without files.json: record it now
            self._record(app, data)
            return True
        return record.get("size") == len(data) and record.get("sha256") == hashlib.sha256(data).hexdigest()

    def _record(self, app, data):
        files = self._json(self.files_path, {})
        files[app.tarball_name] = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                                   "fetched": _now_iso(self.clock)}
        _write_atomic(self.files_path, json.dumps(files, indent=1, sort_keys=True).encode("utf-8"))

    def tarball(self, app, download=True):
        """The app's tarball: the local copy if it is good, else downloaded (and kept) unless download is False."""
        if self.has(app):
            with open(self.tarball_path(app), "rb") as f:
                return f.read()
        if not download:
            raise AppStoreError("%s is not in the local store; connect to the internet to download it." % app.name)
        data = self._read(app.tarball_url, MAX_TARBALL)
        unpack(data, app)                           # refuse a bad tarball before it is kept
        _write_atomic(self.tarball_path(app), data)
        self._record(app, data)
        return data

    def download_all(self, apps, on_progress=None, cancelled=None):
        """Fetch every tarball not already here (decision 2: the whole store). Returns (downloaded, present, failed)
        with failed a list of (app, reason); one bad app does not stop the rest."""
        downloaded, present, failed = 0, 0, []
        for i, app in enumerate(apps):
            if cancelled and cancelled():
                raise AppStoreError("Cancelled.")
            if self.has(app):
                present += 1
            else:
                try:
                    self.tarball(app)
                    downloaded += 1
                except AppStoreError as e:
                    failed.append((app, str(e)))
            if on_progress:
                on_progress(i + 1, len(apps))
        return downloaded, present, failed

    def prune(self, apps, keep=()):
        """Remove tarballs no current catalogue entry uses, except the names in `keep` (installed releases)."""
        wanted = {a.tarball_name for a in apps} | set(keep)
        removed = []
        try:
            names = os.listdir(self.tarball_dir)
        except OSError:
            return removed
        files = self._json(self.files_path, {})
        for name in names:
            if TARBALL_NAME.match(name) and name not in wanted:
                os.remove(os.path.join(self.tarball_dir, name))
                files.pop(name, None)
                removed.append(name)
        if removed:
            _write_atomic(self.files_path, json.dumps(files, indent=1, sort_keys=True).encode("utf-8"))
        return removed

    # favourites (decision 7: kept in the store folder, so a copied store carries them)
    def favourites(self):
        return set(self._json(self.favourites_path, {}).get("apps", []))

    def set_favourite(self, app, on=True):
        favs = self.favourites()
        (favs.add if on else favs.discard)(app.key)
        _write_atomic(self.favourites_path, json.dumps({"apps": sorted(favs)}, indent=1).encode("utf-8"))
        return favs


# -- unpacking and the install plan ---------------------------------------------------------------------------------
class Plan:
    """What installing one app writes, relative to the badge's root: `target` ('apps'), `folder` (the app's folder),
    `dirs` and `files` relative to that folder, then tildagon.json (if the tarball had none) and metadata.json."""

    def __init__(self, app, dirs, files, manifest, metadata):
        self.app, self.target, self.folder = app, app.target, app.folder
        self.dirs, self.files, self.manifest, self.metadata = dirs, files, manifest, metadata

    def writes(self):
        """[(relative path, bytes)] in the order they are written: the app, then tildagon.json, metadata.json last."""
        out = list(self.files)
        if self.manifest is not None:
            out.append(("tildagon.json", self.manifest))
        out.append(("metadata.json", self.metadata))
        return out

    def size(self):
        return sum(len(d) for _p, d in self.writes())


BAD_NAME_CHARS = set('<>:"|?*\\')     # not allowed in a FAT name (the badge's file system) or on Windows


def _safe_parts(name):
    """The path split into parts, or None if it is not a plain relative path the badge and Windows can hold."""
    if not name or name.startswith("/"):
        return None
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." or any(ord(c) < 32 or c in BAD_NAME_CHARS for c in p) for p in parts):
        return None
    return parts


def unpack(data, app):
    """Check a tarball and work out what to write, without writing anything (the badge's own checks, plus the ones it
    leaves out: path traversal, links and size). Raises AppStoreError."""
    try:
        raw = gzip.GzipFile(fileobj=io.BytesIO(data)).read(MAX_UNPACKED + 1)
    except (OSError, EOFError, ValueError) as e:
        raise AppStoreError("%s: the download is not a gzip file (%s)." % (app.name, e))
    if len(raw) > MAX_UNPACKED:
        raise AppStoreError("%s is too large to unpack." % app.name)
    try:
        members = tarfile.open(fileobj=io.BytesIO(raw), mode="r:").getmembers()
    except tarfile.TarError as e:
        raise AppStoreError("%s: the download is not a tar file (%s)." % (app.name, e))
    tar = tarfile.open(fileobj=io.BytesIO(raw), mode="r:")
    entries = []
    for m in members:
        parts = _safe_parts(m.name)
        if parts is None:
            raise AppStoreError("%s has an unsafe path in it: %r" % (app.name, m.name))
        if not (m.isdir() or m.isfile()):
            raise AppStoreError("%s has a link or special file in it: %r" % (app.name, m.name))
        entries.append((parts, m))
    roots = {parts[0] for parts, m in entries if len(parts) == 1 and m.isdir()}
    if not roots:                                   # files only, no directory entries: one shared first folder
        roots = {parts[0] for parts, m in entries if len(parts) > 1}
    if len(roots) != 1:
        raise AppStoreError("%s: expected one folder at the top of the download, found %d." % (app.name, len(roots)))
    root = roots.pop()
    dirs, files, has_json = set(), [], False
    for parts, m in entries:
        if parts[0] != root or len(parts) == 1:
            continue                                # outside the app's folder (the badge skips these too)
        rel = "/".join(parts[1:])
        if m.isdir():
            dirs.add(rel)
            continue
        for i in range(1, len(parts) - 1):
            dirs.add("/".join(parts[1:i + 1]))
        if rel == "tildagon.toml":
            continue                                # the badge deletes it after copying
        if rel == "tildagon.json":
            has_json = True
        files.append((rel, tar.extractfile(m).read()))
    if not any(rel in ("app.py", "app.mpy") for rel, _d in files):
        raise AppStoreError("%s has no app.py (or app.mpy) in it." % app.name)
    manifest = None if has_json else json.dumps(app.manifest).encode("utf-8")
    metadata = json.dumps({"name": app.name, "hidden": False, "version": app.version}).encode("utf-8")
    return Plan(app, sorted(dirs, key=lambda d: (d.count("/"), d)), sorted(files), manifest, metadata)


# -- on the badge -----------------------------------------------------------------------------------------------------
def _mkdirs(backend, path):
    if backend.stat(path) is not None:
        return
    parent = backend.parent(path)
    if parent != path:
        _mkdirs(backend, parent)
    backend.mkdir(path)


def _same(backend, path, data):
    """True when the file on the board already holds exactly `data` (checked on the board when it can hash, so an
    update re-sends only what changed)."""
    st = backend.stat(path)
    if st is None or st.is_dir or st.size != len(data):
        return False
    try:
        digest = backend.checksum(path)
    except Exception:
        digest = None
    if digest:
        return digest == hashlib.sha256(data).hexdigest()
    # no hashlib on the board: compare in small reads (a read asks the board for that many bytes at once)
    with backend.open_read(path) as f:
        for i in range(0, len(data), READ_CHUNK):
            if f.read(min(READ_CHUNK, len(data) - i)) != data[i:i + READ_CHUNK]:
                return False
    return True


def kib(n):
    return "%d KB" % max(1, round(n / 1024)) if n < 1 << 20 else "%.1f MB" % (n / (1 << 20))


def free_space(backend, root):
    """Bytes free on the board's file system, or None when it cannot say (then installs are not size-checked)."""
    try:
        return int(backend.free_space(root))
    except Exception:
        return None


def install(store, backend, app, root="/", on_progress=None, cancelled=None, download=True):
    """Install or update an app, copying over what is there (decision 4, as the badge does). Returns (written,
    skipped) file counts."""
    plan = unpack(store.tarball(app, download), app)
    folder = backend.join(backend.join(root, plan.target), plan.folder)
    writes = plan.writes()
    need = 0
    for rel, data in writes:                        # what it adds, after what an update replaces
        st = backend.stat(backend.join(folder, rel))
        need += len(data) - (st.size if st is not None and not st.is_dir else 0)
    free = free_space(backend, root)
    if free is not None and need > free:
        raise AppStoreError("%s needs %s more space on the badge, and it has %s free." % (
            app.name, kib(need), kib(free)))
    _mkdirs(backend, folder)
    for d in plan.dirs:
        _mkdirs(backend, backend.join(folder, d))
    toml = backend.join(folder, "tildagon.toml")
    if backend.stat(toml) is not None:              # left by the badge's own installer only if it failed midway
        backend.remove(toml)
    written = skipped = 0
    for i, (rel, data) in enumerate(writes):
        if cancelled and cancelled():
            raise AppStoreError("Cancelled.")
        target = backend.join(folder, rel)
        if _same(backend, target, data):
            skipped += 1
        else:
            with backend.open_write(target) as f:
                f.write(data)
            written += 1
        if on_progress:
            on_progress(i + 1, len(writes))
    return written, skipped


class Installed:
    """An app folder on the badge, as its launcher sees it (`metadata.json`; version '0.0.0' when it has none)."""

    def __init__(self, target, folder, path, name, version, hidden):
        self.target, self.folder, self.path = target, folder, path
        self.name, self.version, self.hidden = name, version, hidden

    def __repr__(self):
        return "Installed(%s/%s %s)" % (self.target, self.folder, self.version)


def installed(backend, root="/"):
    """Every app folder in /apps, /backgrounds and /pattern."""
    out = []
    for target in TARGET_NAMES:
        base = backend.join(root, target)
        st = backend.stat(base)
        if st is None or not st.is_dir:
            continue
        for e in backend.listdir(base):
            if not e.is_dir:
                continue
            path = backend.join(base, e.name)
            meta = {}
            meta_path = backend.join(path, "metadata.json")
            mst = backend.stat(meta_path)
            if mst is not None and not mst.is_dir and mst.size <= MAX_METADATA:
                try:
                    with backend.open_read(meta_path) as f:
                        meta = json.loads(f.read(mst.size))     # its real size: the board sets aside what is asked
                except (ValueError, OSError, BackendError):
                    meta = {}
            if not isinstance(meta, dict):
                meta = {}
            out.append(Installed(target, e.name, path, str(meta.get("name") or e.name),
                                 str(meta.get("version") or "0.0.0"), bool(meta.get("hidden", False))))
    return sorted(out, key=lambda a: (a.target, a.name.lower()))


def remove(backend, inst):
    """Delete an installed app's folder and everything in it."""
    from .ops import Progress, delete_tree
    delete_tree(backend, inst.path, Progress())


# -- versions, updates and favourites --------------------------------------------------------------------------------
def version_key(v):
    """A sortable form of a version such as '1.10', 'v2.0.0-rc1' or '0'. Numbers compare as numbers (the badge's
    own Update check compares them as strings, so it ranks 1.9 above 1.10); a pre-release sorts before its release,
    following the badge's `ota.parse_version`."""
    v = str(v).strip().lstrip("vV").split("+", 1)[0]
    main, _, pre = v.partition("-")
    nums = [int(x) if x.isdigit() else x for x in re.split(r"[.]", main) if x != ""]
    while len(nums) > 1 and nums[-1] == 0:
        nums.pop()                                  # 1.0 == 1.0.0
    pre_key = (1,) if not pre else (0, pre[2:] if pre.startswith("rc") else pre)
    return [(0, n) if isinstance(n, int) else (1, n) for n in nums], pre_key


def newer(a, b):
    """True when version a is later than version b."""
    try:
        return version_key(a) > version_key(b)
    except TypeError:
        return str(a) != str(b) and str(a) > str(b)


def by_folder(apps):
    return {a.folder: a for a in apps}


def updates(apps, on_badge):
    """[(installed, store app)] for installed apps the store has a later version of."""
    index = by_folder(apps)
    return [(i, index[i.folder]) for i in on_badge if i.folder in index and newer(index[i.folder].version, i.version)]


def favourites_to_install(apps, favourites, on_badge):
    """The favourite apps a badge is missing or has an older version of (Install all favourites), by name."""
    have = {i.folder: i for i in on_badge}
    out = []
    for a in apps:
        if a.key not in favourites:
            continue
        inst = have.get(a.folder)
        if inst is None or newer(a.version, inst.version):
            out.append(a)
    return out


# -- requirements (decision 5: shown and asked about, never enforced) -------------------------------------------------
class Requirement:
    def __init__(self, kind, text, required, met=None):
        self.kind, self.text, self.required, self.met = kind, text, required, met   # met: True / False / None

    def __repr__(self):
        return "Requirement(%s %r required=%s met=%s)" % (self.kind, self.text, self.required, self.met)


def requirements(app, os_version=None):
    """What the app says it needs or can use. The OS minimum is checked against `os_version` (the badge's
    ota.get_version()) when known; the store spells the type TildagonOSMinimumVersion and the badge's own check
    TildagonOsMinimumVersion, so either is accepted."""
    out = []
    for cap in app.capabilities:
        if not isinstance(cap, dict):
            continue
        required = bool(cap.get("required"))
        feature = cap.get("feature") or {}
        kind = str(feature.get("type", "")).lower()
        if kind == "tildagonosminimumversion":
            need = str(feature.get("version", ""))
            met = None if not os_version else not newer(need, os_version)
            out.append(Requirement("os", "Tildagon OS %s or later" % need, required, met))
        elif "vid" in feature or "pid" in feature:
            out.append(Requirement("hexpansion", "the %s" % (feature.get("name") or "hexpansion"), required))
        elif kind == "2026 frontboard":
            out.append(Requirement("frontboard", "the 2026 front board", required))
        elif kind == "capability":
            ident = str(feature.get("identifier", "")).rstrip("/")
            out.append(Requirement("capability", ident.rsplit("/", 1)[-1] or ident, required))
        else:
            out.append(Requirement("other", str(feature.get("type") or feature), required))
    return out


def unmet(reqs):
    """Required needs known not to be met (the dialog asks before installing those)."""
    return [r for r in reqs if r.required and r.met is False]


def board_os_version(backend):
    """The badge's Tildagon OS version (`ota.get_version()`), or None for a board that is not running Tildagon OS."""
    run = getattr(backend, "_run", None)
    if run is None:
        return None
    try:
        out = run("import ota\nprint(ota.get_version())").decode().strip()
    except Exception:
        return None
    return out or None
