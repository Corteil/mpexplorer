"""Custom firmware and library sources (docs/CUSTOM_SOURCES_PLAN.md): presets, which hosts are trusted, and the
checks on a source before it is saved. GUI-free; the lists and files are fetched by `custom_firmware` and the library
catalogs."""
import re
import urllib.parse
from collections import namedtuple

from .errors import BackendError


class SourceError(BackendError):
    pass


GITHUB_HOSTS = ("github.com", "api.github.com", "raw.githubusercontent.com", "objects.githubusercontent.com",
                "release-assets.githubusercontent.com", "codeload.github.com")
# '<board>-<version>[-extra words].uf2|bin', the version like v1.29.0 or v1.29.0-2 (Pimoroni's revision after the dash)
DEFAULT_PATTERN = (r"^(?P<board>.+?)-(?P<version>v?\d[\w.]*(?:-(?:\d+|(?:rc|preview|alpha|beta)[\w.]*))?)"
                   r"(?:-[a-z][a-z0-9_]*)*\.(?:uf2|bin)$")
MAX_NAME = 40

FIRMWARE_PRESETS = (
    {"name": "Pimoroni MicroPython", "kind": "github", "repo": "pimoroni/pimoroni-pico", "pattern": DEFAULT_PATTERN,
     "firmware": "micropython"},
    {"name": "Pimoroni MicroPython (RP2350)", "kind": "github", "repo": "pimoroni/pimoroni-pico-rp2350",
     "pattern": DEFAULT_PATTERN, "firmware": "micropython"},
    # the LiPo boards have their own repo; its '-with-filesystem' twins carry example files, so only the plain builds
    {"name": "Pimoroni Pico LiPo", "kind": "github", "repo": "pimoroni/pico-lipo",
     "pattern": r"^(?P<board>.+?)-(?P<version>v?\d[\w.]*)-micropython\.uf2$", "firmware": "micropython"},
)
LIBRARY_PRESETS = (
    {"name": "Pimoroni phew", "kind": "mip", "repo": "pimoroni/phew", "for": "micropython"},
    {"name": "SparkFun Qwiic", "kind": "bundle", "repo": "sparkfun/qwiic_py", "prefix": "qwiic-py",
     "for": "circuitpython"},
)
Check = namedtuple("Check", "ok message trust_host")        # trust_host: a site the owner must approve before saving


def host_of(url):
    return (urllib.parse.urlsplit(url).hostname or "").lower()


def is_github(host):
    return host in GITHUB_HOSTS


def trusted_hosts(settings):
    return set(settings.get("sources.trusted_hosts"))


def is_trusted(settings, host):
    return is_github(host) or host in trusted_hosts(settings)


def trust_host(settings, host):
    hosts = settings.get("sources.trusted_hosts")
    if host and host not in hosts and not is_github(host):
        settings.set("sources.trusted_hosts", hosts + [host])


def valid_repo(repo):
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo or ""))


def validate_firmware_source(src):
    """The source cleaned up, or SourceError saying what is wrong with it."""
    name, kind = (src.get("name") or "").strip(), src.get("kind")
    if not name or len(name) > MAX_NAME:
        raise SourceError("Give the source a name of up to %d characters." % MAX_NAME)
    out = {"name": name, "kind": kind, "firmware": src.get("firmware") or "micropython"}
    if out["firmware"] not in ("micropython", "circuitpython"):
        raise SourceError("The firmware is micropython or circuitpython.")
    if kind == "github":
        repo = (src.get("repo") or "").strip().removeprefix("https://github.com/").strip("/")
        if not valid_repo(repo):
            raise SourceError("A GitHub repo looks like owner/name, for example pimoroni/pimoroni-pico.")
        out["repo"] = repo
    elif kind == "url":
        url = (src.get("url") or "").strip()
        if not url.startswith("https://") or not host_of(url):
            raise SourceError("The address must start with https://")
        out["url"] = url
    else:
        raise SourceError("Unknown kind of source: %r" % kind)
    pattern = (src.get("pattern") or "").strip() or DEFAULT_PATTERN
    try:
        compiled = re.compile(pattern)
    except re.error as e:
        raise SourceError("The pattern is not valid: %s" % e)
    if kind == "github" and not {"board", "version"} <= set(compiled.groupindex):
        raise SourceError("The pattern needs the named groups (?P<board>...) and (?P<version>...).")
    if kind == "github":
        out["pattern"] = pattern
    return out


def slug(src):
    """A folder name for this source's cache."""
    return re.sub(r"[^A-Za-z0-9]+", "_", src.get("repo") or src.get("url") or src["name"]).strip("_")[:60]


def firmware_allowed(src):
    """URL prefixes a firmware source may read: its own repo (and the GitHub hosts its files come from), or its host."""
    if src["kind"] == "github":
        repo = src["repo"]
        return ("https://api.github.com/repos/%s/" % repo, "https://github.com/%s/" % repo,
                "https://objects.githubusercontent.com/", "https://release-assets.githubusercontent.com/")
    return ("https://%s/" % host_of(src["url"]),)


def add_source(settings, section, src):
    """Save a source (replacing one of the same name). `section` is 'firmware' or 'libraries'."""
    key = "sources." + section
    sources = [s for s in settings.get(key) if s["name"] != src["name"]]
    settings.set(key, sources + [src])


def remove_source(settings, section, name):
    """Remove a source by name; a trusted site that no remaining source uses is no longer trusted."""
    key = "sources." + section
    settings.set(key, [s for s in settings.get(key) if s["name"] != name])
    used = {host_of(s.get("url", "")) for sec in ("firmware", "libraries") for s in settings.get("sources." + sec)}
    settings.set("sources.trusted_hosts", [h for h in settings.get("sources.trusted_hosts") if h in used])


# -- versions ----------------------------------------------------------------------------------------------------
def version_key(text):
    """'v1.29.0-2' -> (1, 29, 0, 2); numbers only, so releases of one project order correctly."""
    return tuple(int(n) for n in re.findall(r"\d+", text or ""))


def exact_release(version):
    """The release with its revision from `os.uname().version`: 'picow v1.29.0-2 on 2026-08-31' -> '1.29.0-2'.
    Stock builds say 'v1.29.0 on ...', and a development build 'v1.27.0-72-g1234abc' (its -72 counts commits, not a
    revision): both give no revision, so '' for those."""
    m = re.search(r"(?:^|\s)v?(\d+\.\d+\.\d+-\d+)(?=\s|$)", version or "")
    return m.group(1) if m else ""


def newer_than(candidate, running="", installed=""):
    """Is `candidate` newer than the board? An install recorded from this source compares exactly. Otherwise only the
    release the board reports (1.29.0, with no revision) can be used, so an equal release is never called newer."""
    if installed:
        return version_key(candidate) > version_key(installed)
    if not running:
        return False
    have = version_key(running)
    return version_key(candidate)[:max(3, len(have))] > have
