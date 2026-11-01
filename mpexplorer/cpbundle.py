"""CircuitPython libraries from the Adafruit and Community bundles (GitHub releases), written to a board's lib folder.

Checked 2026-11-01: a release has `<prefix>-<tag>.json` (name -> dependencies, package, path, version, description) and
`<prefix>-<N>.x-mpy-<tag>.zip` / `<prefix>-py-<tag>.zip` (every library under `<root>/lib/`). The latest tag is
found by the redirect from `<repo>/releases/latest`, which avoids the API's anonymous rate limit. The .mpy zip must
match the board's CircuitPython major version; the py zip is source, for any version. The catalog has the same shape as
`libraries.Catalog` (libraries / resolve / fetch), so `libraries.install` and `libraries.remove` work with it.
"""
import io
import json
import os
import re
import urllib.error
import urllib.request
import zipfile

from .errors import Cancelled
from .libraries import Library, LibraryError, default_cache_dir

TIMEOUT = 30
MAX_JSON = 4 << 20
MAX_ZIP = 64 << 20

SOURCES = {
    "adafruit": ("Adafruit bundle", "adafruit/Adafruit_CircuitPython_Bundle", "adafruit-circuitpython-bundle"),
    "community": ("Community bundle", "adafruit/CircuitPython_Community_Bundle", "circuitpython-community-bundle"),
}


def _open_url(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "mpexplorer"}), timeout=TIMEOUT)


class Catalog:
    def __init__(self, source="adafruit", cache_dir=None, opener=_open_url):
        """source: a built-in key ('adafruit', 'community') or a custom source {name, repo, prefix, ...}."""
        if isinstance(source, dict):
            self.title, self.repo, self.prefix = source["name"], source["repo"], source["prefix"]
            self.source = "custom-" + re.sub(r"[^A-Za-z0-9]+", "_", self.repo)
        else:
            self.source = source
            self.title, self.repo, self.prefix = SOURCES[source]
        self.dir = os.path.join(cache_dir or default_cache_dir(), "bundle-" + self.source)
        self.opener, self.tag, self.meta = opener, None, None
        self.external = []              # PyPI libraries the chosen ones need that this bundle lacks (set by resolve)
        # only its own repo's releases, and the hosts GitHub serves its release files from
        self.allowed = ("https://github.com/%s/" % self.repo, "https://objects.githubusercontent.com/",
                        "https://release-assets.githubusercontent.com/")
        self.on_progress = None         # optional callable(done, total) for the big downloads
        self._zip = (None, None)        # (key, ZipFile) of the last one opened

    # -- reading ----------------------------------------------------------------------------------------------
    def _read(self, url, limit, cancelled=None):
        if not url.startswith(self.allowed):
            raise LibraryError("Refusing to download from %s" % url)
        try:
            with self.opener(url) as reply:
                if not reply.geturl().startswith(self.allowed):
                    raise LibraryError("The download was redirected to %s" % reply.geturl())
                length = reply.headers.get("Content-Length") if hasattr(reply, "headers") else None
                total, data = int(length) if length and length.isdigit() else 0, bytearray()
                while True:
                    if cancelled and cancelled():
                        raise Cancelled()
                    chunk = reply.read(1 << 16)
                    if not chunk:
                        break
                    data += chunk
                    if len(data) > limit:
                        raise LibraryError("The download from %s is too large." % url)
                    if self.on_progress and total:
                        self.on_progress(len(data), total)
        except (OSError, urllib.error.HTTPError, ValueError) as e:
            raise LibraryError("Cannot read %s: %s" % (url, e))
        return bytes(data)

    def _latest_tag(self):
        path = os.path.join(self.dir, "tag.txt")
        try:
            with self.opener("https://github.com/%s/releases/latest" % self.repo) as reply:
                url = reply.geturl()
            tag = url.rstrip("/").rsplit("/", 1)[-1]
            if not url.startswith(self.allowed) or "/tag/" not in url or not tag.isalnum():
                raise LibraryError("Unexpected release address: %s" % url)
        except (OSError, LibraryError) as e:
            try:
                with open(path, encoding="utf-8") as f:
                    return f.read().strip(), True
            except OSError:
                raise LibraryError("Cannot find the latest %s: %s" % (self.title, e))
        os.makedirs(self.dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(tag)
        return tag, False

    def _asset(self, name):
        return "https://github.com/%s/releases/download/%s/%s" % (self.repo, self.tag, name)

    def libraries(self):
        """(libraries, saved): saved is None when the list is current, else the time of the saved copy used."""
        self.tag, offline = self._latest_tag()
        cached = os.path.join(self.dir, "%s-%s.json" % (self.prefix, self.tag))
        data = None
        if not os.path.exists(cached):
            try:
                data = self._read(self._asset("%s-%s.json" % (self.prefix, self.tag)), MAX_JSON)
            except LibraryError:
                raise
            os.makedirs(self.dir, exist_ok=True)
            with open(cached + ".part", "wb") as f:
                f.write(data)
            os.replace(cached + ".part", cached)
        with open(cached, "rb") as f:
            try:
                self.meta = json.loads(f.read())
            except ValueError:
                os.unlink(cached)
                raise LibraryError("The %s list is damaged; try again." % self.title)
        libs = [Library(name, str(m.get("version", "")), m.get("pypi_description", ""))
                for name, m in sorted(self.meta.items(), key=lambda kv: kv[0].lower())]
        return libs, (os.path.getmtime(cached) if offline else None)

    # -- the bundle zip -----------------------------------------------------------------------------------------
    def _zip_name(self, major):
        return "%s-%s.x-mpy-%s.zip" % (self.prefix, major, self.tag) if major else \
            "%s-py-%s.zip" % (self.prefix, self.tag)

    def _open_zip(self, name, cancelled=None):
        if self._zip[0] == name:
            return self._zip[1]
        path = os.path.join(self.dir, name)
        if not os.path.exists(path):
            data = self._read(self._asset(name), MAX_ZIP, cancelled)
            try:
                zipfile.ZipFile(io.BytesIO(data)).testzip()
            except zipfile.BadZipFile:
                raise LibraryError("The downloaded %s is damaged." % name)
            os.makedirs(self.dir, exist_ok=True)
            with open(path + ".part", "wb") as f:
                f.write(data)
            os.replace(path + ".part", path)
        z = zipfile.ZipFile(path)
        self._zip = (name, z)
        return z

    def _bundle(self, major, cancelled=None):
        """(zip name, ZipFile) for the board's CircuitPython major version, else the source zip when there is none."""
        if self.meta is None:
            self.libraries()
        for m in ((major, None) if major else (None,)):
            name = self._zip_name(m)
            try:
                return name, self._open_zip(name, cancelled)
            except LibraryError:
                if m is None:
                    raise
        raise LibraryError("unreachable")

    # -- resolving and fetching -----------------------------------------------------------------------------------
    def resolve(self, names, mpy=None, cancelled=None):
        """[(name, version, kind, [(path, key)])] with dependencies first. `mpy` is the board's CircuitPython major
        version (the .mpy zip is used), or None for source files."""
        zname, z = self._bundle(mpy, cancelled)
        kind = "py" if zname.startswith(self.prefix + "-py-") else str(mpy)
        root = zname[:-4] + "/"
        members = set(z.namelist())
        out, seen, self.external = [], set(), []

        def files_of(meta):
            base = root + meta["path"]
            if meta.get("package"):
                return [(m[len(root) + len("lib/"):], zname + "|" + m) for m in sorted(members)
                        if m.startswith(base + "/") and not m.endswith("/")]
            for ext in (".mpy", ".py"):
                if base + ext in members:
                    return [(os.path.basename(base) + ext, zname + "|" + base + ext)]
            return []

        def visit(name, depth=0):
            if name in seen:
                return
            if depth > 20:
                raise LibraryError("Dependencies of %s are nested too deeply." % name)
            seen.add(name)
            meta = self.meta.get(name)
            if meta is None:
                raise LibraryError("%s is not in the %s." % (name, self.title))
            for dep in meta.get("dependencies", []):
                visit(dep, depth + 1)
            files = files_of(meta)
            if not files:
                raise LibraryError("%s has no files in %s." % (name, zname))
            self.external += [e for e in meta.get("external_dependencies", []) if e not in self.external]
            out.append((name, str(meta.get("version", "")), kind, files))

        for n in names:
            visit(n)
        self.external = sorted(e for e in self.external if e not in self.meta)      # the bundle's own were installed
        return out

    def fetch(self, key):
        zname, _, member = key.partition("|")
        if os.path.basename(zname) != zname or not zname.endswith(".zip") or ".." in member.split("/"):
            raise LibraryError("Bad file reference: %r" % key)
        try:
            return self._open_zip(zname).read(member)
        except KeyError:
            raise LibraryError("%s is not in %s." % (member, zname))
