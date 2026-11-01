"""Firmware from a maker's GitHub releases (or one direct link): boards and builds like `firmware_catalog.Catalog`.

Checked 2026-11-01 against Pimoroni: `pimoroni/pimoroni-pico` releases carry assets like
`pico2-v1.29.0-2-pimoroni-micropython.uf2` and `pimoroni/pimoroni-pico-rp2350` carry
`pimoroni_pico_plus2-v1.29.0-micropython.uf2`. The wrong-image protection is
`firmware.check_image`, which reads the whole file before anything is written.
"""
import json
import os
import posixpath
import re
import urllib.parse

from . import sources
from .firmware import FirmwareError
from .firmware_catalog import Board, Build, Catalog, default_cache_dir, _open_url

MAX_LIST = 16 << 20
EXTENSIONS = (".uf2", ".bin")


def parse_releases(text, pattern):
    """Builds (newest release first) from a GitHub releases reply: every .uf2 / .bin asset the pattern understands."""
    rx = re.compile(pattern)
    builds = []
    for rel in json.loads(text):
        if rel.get("draft"):
            continue
        for asset in rel.get("assets", []):
            name = asset.get("name", "")
            m = rx.match(name) if name.lower().endswith(EXTENSIONS) else None
            if m:
                builds.append(Build(name, m.group("version"), (rel.get("published_at") or "")[:10], "",
                                    bool(rel.get("prerelease")), asset.get("browser_download_url", "")))
    return sorted(builds, key=lambda b: (not b.preview, b.date, sources.version_key(b.version)), reverse=True)


class GitHubReleases(Catalog):
    """A maker's repo, read through its releases. The boards are whatever the file names say."""

    def __init__(self, source, cache_dir=None, opener=_open_url):
        self.source, self.rx = source, re.compile(source["pattern"])
        super().__init__(cache_dir or os.path.join(default_cache_dir(), "custom", sources.slug(source)), opener,
                         sources.firmware_allowed(source))

    def releases(self):
        """(builds, saved): the saved list is used when GitHub cannot be reached (or its anonymous limit is used up)."""
        url = "https://api.github.com/repos/%s/releases?per_page=30" % self.source["repo"]
        try:
            return self._list("releases.json", url, lambda text: parse_releases(text, self.source["pattern"]), Build)
        except FirmwareError as e:
            if "Found nothing" in str(e):           # the reply was fine: the repo has no file the pattern understands
                raise FirmwareError("No release file (.uf2 or .bin) in %s matches the pattern. Compare the pattern "
                                    "with the file names of one release." % self.source["repo"])
            raise

    def boards(self, chip=None, badges=()):
        found, saved = self.releases()
        names = sorted({self.rx.match(b.filename).group("board") for b in found})
        return [Board(n, n, self.source["name"]) for n in names], saved

    def builds(self, board_id, chip=None):
        found, saved = self.releases()
        return [b for b in found if self.rx.match(b.filename).group("board") == board_id], saved

    def read_head(self, url, size=512):
        """The first bytes of a file, for the header check (the rest of the download is never fetched)."""
        if not url.startswith(self.allowed):
            raise FirmwareError("Refusing to read from %s" % url)
        try:
            with self.opener(url) as reply:
                if not reply.geturl().startswith(self.allowed):
                    raise FirmwareError("The file is on another site than the source: %s" % reply.geturl())
                return reply.read(size)
        except (OSError, ValueError) as e:
            raise FirmwareError("Cannot read %s: %s" % (url, e))


class DirectUrl(Catalog):
    """One firmware file at one address."""

    def __init__(self, source, cache_dir=None, opener=_open_url):
        self.source = source
        super().__init__(cache_dir or os.path.join(default_cache_dir(), "custom", sources.slug(source)), opener,
                         sources.firmware_allowed(source))
        name = posixpath.basename(urllib.parse.urlsplit(source["url"]).path) or "firmware.uf2"
        self.build = Build(re.sub(r"[^A-Za-z0-9._-]", "_", name), "download", "", "", False, source["url"])

    def boards(self, chip=None, badges=()):
        return [Board(self.source["name"], self.source["name"], "")], None

    def builds(self, board_id, chip=None):
        return [self.build], None

    read_head = GitHubReleases.read_head


def make_catalog(source, cache_dir=None, opener=_open_url):
    return (GitHubReleases if source["kind"] == "github" else DirectUrl)(source, cache_dir, opener)


def _header_problem(name, head):
    """Why the first bytes are not a firmware image of the kind the name says ('' if they look right)."""
    if name.lower().endswith(".uf2"):
        return "" if head[:4] == b"UF2\n" else "its first bytes are not a UF2 file"
    return "" if head[:1] == b"\xe9" else "its first byte is not an ESP firmware image (0xE9)"


def check_source(source, settings, cache_dir=None, opener=_open_url):
    """The Check every new firmware source passes before it is saved (docs/CUSTOM_SOURCES_PLAN.md, Safety rules).
    Returns sources.Check(ok, message, trust_host): trust_host names a site the owner must approve before saving."""
    try:
        src = sources.validate_firmware_source(source)
        catalog = make_catalog(src, cache_dir, opener)
        host = sources.host_of(src.get("url") or "https://api.github.com/")
        ask = host if not sources.is_trusted(settings, host) else None
        if src["kind"] == "github":
            builds, _ = catalog.releases()
            boards = sorted({catalog.rx.match(b.filename).group("board") for b in builds})
            newest = builds[0]
            summary = "%d board%s, newest %s" % (len(boards), "" if len(boards) == 1 else "s", newest.version)
        else:
            newest, summary = catalog.build, "one file, %s" % catalog.build.filename
        problem = _header_problem(newest.filename, catalog.read_head(newest.url))
        if problem:
            raise FirmwareError("%s looks wrong: %s." % (newest.filename, problem))
        return sources.Check(True, "%s: %s" % (src["name"], summary), ask)
    except (sources.SourceError, FirmwareError) as e:
        return sources.Check(False, str(e), None)
