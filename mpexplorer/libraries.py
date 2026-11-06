"""MicroPython libraries from micropython-lib (the `mip` index), fetched on the host and written to a board's /lib.

Nothing runs on the board: the files are downloaded here and uploaded through whatever backend is given, so it works
with no network on the board. Package format (checked 2026-11-01): `index.json` lists packages;
`package/<py|mpy version>/<name>/<version>.json`
holds `hashes` ([path, short hash] pairs) and `deps` ([name, version]); a file is at `file/<first two of hash>/<hash>`.
"""
import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from . import paths
from .errors import BackendError

INDEX = "https://micropython.org/pi/v2"
ALLOWED = ("https://micropython.org/",)
TIMEOUT = 30
MAX_FILE = 2 << 20
MAX_INDEX = 4 << 20
DEST = "/lib"


class LibraryError(BackendError):
    pass


def default_cache_dir():
    return os.path.join(paths.cache_dir(), "libs")


def _open_url(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "mpexplorer"}), timeout=TIMEOUT)


def mpy_version(mpy):
    """The .mpy format number from `sys.implementation._mpy` (low byte), or None when the board has none."""
    return (mpy & 0xFF) if mpy else None


class Library:
    def __init__(self, name, version, description="", mpy_versions=()):
        self.name, self.version, self.description, self.mpy_versions = name, version, description, tuple(mpy_versions)

    def label(self):
        return "%s %s" % (self.name, self.version)


def parse_index(data):
    """Libraries from index.json, sorted by name."""
    try:
        packages = json.loads(data)["packages"]
        libs = [Library(p["name"], p["version"], p.get("description", ""),
                        [v for v in p.get("versions", {}) if v != "py"]) for p in packages]
    except (ValueError, KeyError, TypeError):
        raise LibraryError("The package index is not in the expected format.")
    return sorted(libs, key=lambda lib: lib.name)


class Catalog:
    def __init__(self, cache_dir=None, opener=_open_url, base=INDEX):
        """`base` is the index's address; another one (a maker's own v2 index) may read only its own site."""
        self.base, self.opener = base.rstrip("/"), opener
        self.dir = cache_dir or default_cache_dir()
        if self.base == INDEX:
            self.allowed = ALLOWED
        else:
            self.allowed = ("https://%s/" % (urllib.parse.urlsplit(self.base).hostname or "-"),)
            self.dir = os.path.join(self.dir, "index-" + re.sub(r"[^A-Za-z0-9]+", "_", self.base)[-60:])

    def _read(self, url, limit):
        if not url.startswith(self.allowed):
            raise LibraryError("Refusing to download from %s" % url)
        try:
            with self.opener(url) as reply:
                if not reply.geturl().startswith(self.allowed):
                    raise LibraryError("The download was redirected to %s" % reply.geturl())
                data = reply.read(limit + 1)
        except (OSError, urllib.error.HTTPError, ValueError) as e:
            raise LibraryError("Cannot read %s: %s" % (url, e))
        if len(data) > limit:
            raise LibraryError("The download from %s is too large." % url)
        return data

    def libraries(self):
        """(libraries, saved): saved is None for a fresh index, else the time of the saved copy used instead."""
        path = os.path.join(self.dir, "index.json")
        try:
            data = self._read(self.base + "/index.json", MAX_INDEX)
            libs = parse_index(data)
        except LibraryError as e:
            try:
                with open(path, "rb") as f:
                    return parse_index(f.read()), os.path.getmtime(path)
            except OSError:
                raise e
        os.makedirs(self.dir, exist_ok=True)
        with open(path + ".part", "wb") as f:
            f.write(data)
        os.replace(path + ".part", path)
        return libs, None

    def package(self, name, version="latest", mpy=None):
        """The package's JSON for the .mpy format `mpy`, falling back to plain source when that has none."""
        kinds = ([str(mpy)] if mpy else []) + ["py"]
        last = None
        for kind in kinds:
            try:
                url = "%s/package/%s/%s/%s.json" % (self.base, kind, name, version)
                return json.loads(self._read(url, MAX_INDEX)), kind
            except (LibraryError, ValueError) as e:
                last = e
        raise LibraryError("Cannot find %s: %s" % (name, last))

    def resolve(self, names, mpy=None):
        """Every package needed for `names`, dependencies first: [(name, version, kind, [(path, hash)])]."""
        out, seen = [], set()

        def visit(name, version, depth=0):
            if name in seen:
                return
            if depth > 20:
                raise LibraryError("Dependencies of %s are nested too deeply." % name)
            seen.add(name)
            pkg, kind = self.package(name, version, mpy)
            for dep in pkg.get("deps", []):
                visit(dep[0], dep[1] if len(dep) > 1 else "latest", depth + 1)
            out.append((name, pkg.get("version", version), kind, [tuple(h) for h in pkg.get("hashes", [])]))

        for n in names:
            visit(n, "latest")
        return out

    def fetch(self, hash_):
        """A file's bytes, checked against its short hash (the first 8 hex digits of its SHA-256)."""
        if len(hash_) < 8 or not all(c in "0123456789abcdef" for c in hash_):
            raise LibraryError("Bad file hash: %r" % hash_)
        path = os.path.join(self.dir, "files", hash_)
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            data = self._read("%s/file/%s/%s" % (self.base, hash_[:2], hash_), MAX_FILE)
            if hashlib.sha256(data).hexdigest()[:len(hash_)] != hash_:
                raise LibraryError("A downloaded file does not match its hash (%s)." % hash_)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path + ".part", "wb") as f:
                f.write(data)
            os.replace(path + ".part", path)
        return data


def _mkdirs(backend, path):
    if backend.stat(path) is not None:
        return
    parent = backend.parent(path)
    if parent != path:
        _mkdirs(backend, parent)
    backend.mkdir(path)


def install(catalog, backend, plan, dest=DEST, on_progress=None, cancelled=None):
    """Write a resolved plan under `dest` on the backend. Files already identical (same size and bytes) are skipped.
    Returns (written, skipped) file counts."""
    files = [(path, h) for _, _, _, hashes in plan for path, h in hashes]
    written = skipped = 0
    _mkdirs(backend, dest)
    for i, (path, h) in enumerate(files):
        if cancelled and cancelled():
            raise LibraryError("Cancelled.")
        if path.startswith("/") or ".." in path.split("/"):
            raise LibraryError("Unsafe file path in a package: %s" % path)
        data = catalog.fetch(h)
        target = backend.join(dest, path)
        _mkdirs(backend, backend.parent(target))
        st = backend.stat(target)
        if st is not None and not st.is_dir and st.size == len(data):
            with backend.open_read(target) as f:
                same = f.read(len(data) + 1) == data
            if same:
                skipped += 1
                if on_progress:
                    on_progress(i + 1, len(files))
                continue
        with backend.open_write(target) as f:
            f.write(data)
        written += 1
        if on_progress:
            on_progress(i + 1, len(files))
    return written, skipped


def installed(backend, dest=DEST):
    """Names in the board's lib folder (files without .py/.mpy, and folders), [] if there is none."""
    if backend.stat(dest) is None:
        return []
    return sorted(os.path.splitext(e.name)[0] for e in backend.listdir(dest))


def compare(catalog, backend, names, mpy=None, dest=DEST, cancelled=None):
    """How each named installed library compares with the catalog's current copy, by file contents:
    {name: 'current' | 'differs' | 'incomplete'}. 'differs' covers an older version and a file edited on the board; a
    name the catalog does not have is left out. 'incomplete': some of its files are missing."""
    out = {}
    for name in names:
        if cancelled and cancelled():
            raise LibraryError("Cancelled.")
        try:
            plan = catalog.resolve([name], mpy)
        except LibraryError:
            continue
        files = [f for n, _v, _k, hashes in plan if n == name for f in hashes]
        state = "current"
        for path, key in files:
            target = backend.join(dest, path)
            st = backend.stat(target)
            if st is None or st.is_dir:
                state = "incomplete"
                break
            data = catalog.fetch(key)
            if st.size != len(data):
                state = "differs"
                continue
            with backend.open_read(target) as f:
                if f.read(len(data) + 1) != data:
                    state = "differs"
        out[name] = state
    return out


def board_mpy(backend):
    """The board's .mpy format number, or None when it cannot say (then plain source is used)."""
    try:
        out = backend._run("import sys\nprint(getattr(sys.implementation,'_mpy',0))").strip()
        return mpy_version(int(out))
    except Exception:
        return None


def board_firmware(backend):
    """'micropython', 'circuitpython' or whatever `sys.implementation.name` says; None when the board cannot say."""
    try:
        return backend._run("import sys\nprint(sys.implementation.name)").strip().decode().lower() or None
    except Exception:
        return None


def board_implementation(backend):
    """('circuitpython', '10.3.1') or ('micropython', '1.28.0'); (None, '') when the board cannot say."""
    try:
        out = backend._run("import sys\nprint(sys.implementation.name)\n"
                           "print('.'.join(str(n) for n in sys.implementation.version[:3]))").decode().split()
        return (out[0].lower(), out[1]) if len(out) >= 2 else (None, "")
    except Exception:
        return None, ""


def remove(catalog, backend, names, mpy=None, dest=DEST, cancelled=None):
    """Delete the files of the named libraries (not their dependencies) from `dest`, in either the .mpy or the source
    form, then any folder they leave empty. Returns the number of files removed."""
    paths = set()
    for n in names:
        for form in {mpy, None}:
            try:
                plan = catalog.resolve([n], form)
            except LibraryError:
                continue
            paths.update(p for _n, _v, _k, hashes in plan if _n == n for p, _h in hashes)
    removed, folders = 0, set()
    for path in sorted(paths):
        if path.startswith("/") or ".." in path.split("/"):
            raise LibraryError("Unsafe file path in a package: %s" % path)
        if cancelled and cancelled():
            raise LibraryError("Cancelled.")
        target = backend.join(dest, path)
        st = backend.stat(target)
        if st is not None and not st.is_dir:
            backend.remove(target)
            removed += 1
        parent = backend.parent(target)
        while parent != dest and parent.startswith(dest):
            folders.add(parent)
            parent = backend.parent(parent)
    for folder in sorted(folders, key=len, reverse=True):
        if backend.stat(folder) is not None and not backend.listdir(folder):
            backend.rmdir(folder)
    return removed


def summary(plan, max_lines=30):
    """Lines for the confirmation (what will be written) and the number of files: each library with its version and
    form, then its files indented. A long list is cut with a count of what is left out."""
    files = sum(len(p[3]) for p in plan)
    lines = []
    for name, version, kind, hashes in plan:
        lines.append("%s %s (%s)" % (name, version, "source" if kind == "py" else ".mpy %s" % kind))
        lines.extend("    " + path for path, _h in hashes)
    if len(lines) > max_lines:
        left = len(lines) - max_lines
        lines = lines[:max_lines] + ["    ... and %d more line(s)" % left]
    return lines, files
