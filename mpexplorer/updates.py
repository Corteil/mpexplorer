"""Help > Check for updates: is there a newer release, or newer changes on main than this build? GUI-free.

Asks GitHub's public API only when the owner chooses the menu item (no automatic checks): the latest release, then
how this build's commit compares with main. Two requests, no sign-in (GitHub allows 60 an hour per address).
"""
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

REPO = "Corteil/mpexplorer"
API = "https://api.github.com/repos/" + REPO
RELEASES_PAGE = "https://github.com/%s/releases" % REPO
WINDOWS_BUILDS = "https://github.com/%s/actions/workflows/windows-build.yml?query=branch%%3Amain" % REPO
TIMEOUT = 15


def _open_url(url):
    req = urllib.request.Request(url, headers={"User-Agent": "mpexplorer", "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=TIMEOUT)


@dataclass
class Result:
    kind: str           # 'release' (a newer version is out), 'changes' (newer commits on main), 'current', 'error'
    text: str
    url: str = ""       # a page to open for more (the release, the list of changes), or ''


def parse_version(text):
    """'v0.1.12' -> (0, 1, 12); None if it is not a dotted number."""
    m = re.fullmatch(r"v?(\d+(?:\.\d+)*)", (text or "").strip())
    return tuple(int(p) for p in m.group(1).split(".")) if m else None


def _get(opener, url):
    """The JSON at url, or None for 404 (no release yet; a commit GitHub has never seen)."""
    try:
        with opener(url) as f:
            return json.load(f)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def how_to_update(info):
    if info.get("source") == "git clone":
        return "To update, run git pull in the folder mpexplorer runs from, then start it again."
    if (info.get("source") or "").startswith(("CI run", "local build")):
        return "The newest Windows build is the mpexplorer-windows download of the latest Windows build run on main."
    return "To update, install the new version the way this one was installed."


def check(info, opener=_open_url):
    """A Result for this build (`info` from buildinfo.build_info). Never raises: a failure is a Result too."""
    try:
        release = _get(opener, API + "/releases/latest")
        ours, theirs = parse_version(info["version"]), parse_version(release.get("tag_name") if release else "")
        if ours and theirs and theirs > ours:
            return Result("release", "Version %s is out (you have %s).\n\n%s" % (
                ".".join(map(str, theirs)), info["version"], how_to_update(info)),
                release.get("html_url") or RELEASES_PAGE)
        commit = info.get("commit")
        if not commit or commit == "unknown":
            return Result("current", "You have %s, the latest release. This copy does not say which build it is, so "
                                     "newer changes on main cannot be checked." % info["version"], RELEASES_PAGE)
        compare = _get(opener, "%s/compare/%s...main" % (API, commit))
        if compare is None:
            return Result("current", "You have the latest release (%s). Your build %s is not on GitHub (a local "
                                     "commit?), so it cannot be compared with main." % (info["version"], commit))
        newer, status = compare.get("ahead_by", 0), compare.get("status")
        if newer and status in ("ahead", "diverged"):
            url = compare.get("html_url") or "https://github.com/%s/compare/%s...main" % (REPO, commit)
            if info.get("source", "").startswith(("CI run", "local build")):
                url = WINDOWS_BUILDS
            return Result("changes", "There %s %d newer change%s on main than your build %s.\n\n%s" % (
                "is" if newer == 1 else "are", newer, "" if newer == 1 else "s", commit, how_to_update(info)), url)
        if status == "behind":
            return Result("current", "Your build %s is newer than main: you are up to date." % commit)
        return Result("current", "You are up to date: %s, the newest build on main." % info["version"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        reason = getattr(e, "reason", None) or e
        return Result("error", "Could not check for updates: %s" % reason)
