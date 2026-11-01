"""MicroPython libraries from a repo's `package.json` (the `mip` format), fetched on the host and put in /lib.

Checked 2026-11-01 on `pimoroni/phew`: `{"urls": [["phew/__init__.py", "github:pimoroni/phew/phew/__init__.py"], ...],
"deps": [], "version": "0.0.3"}`. `urls` pairs a destination under /lib with a source (`github:owner/repo/path[@ref]`
or an https address); `deps` names other packages (`github:owner/repo` with a ref, or a micropython-lib name). The
catalog has the same shape as `libraries.Catalog` (libraries / resolve / fetch), so `libraries.install`, `remove` and
`compare` work with it. Nothing runs on the board.
"""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from .libraries import Library, LibraryError, default_cache_dir

RAW = "https://raw.githubusercontent.com/"
TIMEOUT = 30
MAX_JSON = 1 << 20
MAX_FILE = 2 << 20
MAX_FILES = 200
MAX_DEPTH = 8


def _open_url(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "mpexplorer"}), timeout=TIMEOUT)


def github_url(spec, default_ref="HEAD"):
    """'github:owner/repo/path[@ref]' -> the raw file address, or None when `spec` is not that form."""
    m = re.fullmatch(r"github:([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/?([^@]*)(?:@(.+))?", spec)
    if not m:
        return None
    owner, repo, path, ref = m.groups()
    ref = urllib.parse.quote(ref or default_ref, safe="")
    return "%s%s/%s/%s/%s" % (RAW, owner, repo, ref, urllib.parse.quote(path))


class MipRepo:
    """One package repo: `source` is {name, repo: 'owner/name[@ref]'} or {name, url: 'https://.../package.json'}."""

    def __init__(self, source, cache_dir=None, opener=_open_url):
        self.source, self.opener = source, opener
        self.title = source["name"]
        repo, _, ref = (source.get("repo") or "").partition("@")
        self.package_url = (source.get("url") or "%s%s/%s/package.json" % (RAW, repo, ref or "HEAD")).strip()
        fallback = re.sub(r"\.json$", "", os.path.basename(self.package_url)) or source["name"]
        self.name = os.path.basename(repo) if repo else fallback
        tail = re.sub(r"[^A-Za-z0-9]+", "_", self.package_url)[-60:]
        self.dir = os.path.join(cache_dir or default_cache_dir(), "mip-" + tail)
        host = urllib.parse.urlsplit(self.package_url).hostname or ""
        # GitHub packages may use other GitHub repos as dependencies; any other host only its own files
        self.allowed = (RAW,) if host == "raw.githubusercontent.com" else ("https://%s/" % host,)
        self.external = []

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

    def package(self, url=None):
        """The parsed package.json at `url` (the repo's own by default)."""
        url = url or self.package_url
        try:
            pkg = json.loads(self._read(url, MAX_JSON))
            if not isinstance(pkg.get("urls", []), list) or not isinstance(pkg.get("deps", []), list):
                raise ValueError("urls and deps must be lists")
            return pkg
        except ValueError as e:
            raise LibraryError("%s is not a package.json: %s" % (url, e))

    def libraries(self):
        """([the package], None): a repo is one library."""
        pkg = self.package()
        return [Library(self.name, str(pkg.get("version", "")), "package.json of %s" % self.title)], None

    def _files(self, pkg, base_url):
        files = []
        for item in pkg.get("urls", []):
            if not (isinstance(item, list) and len(item) == 2 and all(isinstance(x, str) for x in item)):
                raise LibraryError("A urls entry must be [destination, source]: %r" % (item,))
            dest, src = item
            if dest.startswith("/") or ".." in dest.split("/"):
                raise LibraryError("Unsafe destination in a package: %s" % dest)
            url = github_url(src) or (urllib.parse.urljoin(base_url, src) if not re.match(r"[a-z]+:", src) else src)
            if not url.startswith("https://"):
                raise LibraryError("A package file must come from https or github: %s" % src)
            files.append((dest, url))
        return files

    def resolve(self, names, mpy=None, depth=0):
        """[(name, version, 'py', [(destination, url)])] with dependencies first."""
        if depth > MAX_DEPTH:
            raise LibraryError("Dependencies are nested too deeply.")
        out, seen = [], set()

        def visit(name, url, depth):
            if url in seen:
                return
            seen.add(url)
            pkg = self.package(url)
            for dep in pkg.get("deps", []):
                spec, ref = (dep + ["latest"])[:2] if isinstance(dep, list) else (str(dep), "latest")
                dep_url = github_url(spec, ref if ref not in ("latest", "") else "HEAD")
                if not dep_url:
                    raise LibraryError("%s needs %s from micropython-lib: install that from micropython-lib first."
                                       % (name, spec))
                if depth >= MAX_DEPTH:
                    raise LibraryError("Dependencies are nested too deeply.")
                dep_name = os.path.basename(spec.partition("@")[0].rstrip("/")) or spec
                visit(dep_name, dep_url.rstrip("/") + "/package.json", depth + 1)
            files = self._files(pkg, url)
            if len(files) > MAX_FILES:
                raise LibraryError("%s lists %d files; the limit is %d." % (name, len(files), MAX_FILES))
            out.append((name, str(pkg.get("version", "")), "py", files))

        for n in names:
            if n != self.name:
                raise LibraryError("%s is not in %s." % (n, self.title))
            visit(self.name, self.package_url, depth)
        return out

    def fetch(self, key):
        """A file's bytes by its address (the second item of a resolved pair)."""
        if not key.startswith("https://"):
            raise LibraryError("Bad file address: %r" % key)
        return self._read(key, MAX_FILE)
