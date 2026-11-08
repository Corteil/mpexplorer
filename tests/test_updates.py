import io
import json
import urllib.error

from mpexplorer import updates

CLONE = {"version": "0.1.1", "commit": "d39b18e", "date": "2026-11-08", "changed": False, "source": "git clone"}
WINDOWS = dict(CLONE, source="CI run 57")


def github(release=None, compare=None, fail=None):
    """A fake GitHub API: urls -> JSON. A missing answer is a 404; `fail` is raised for every request."""
    seen = []

    def opener(url):
        seen.append(url)
        if fail:
            raise fail
        for key, value in (("/releases/latest", release), ("/compare/", compare)):
            if key in url:
                if value is None:
                    raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b""))
                return io.BytesIO(json.dumps(value).encode())
        raise AssertionError(url)
    opener.seen = seen
    return opener


def test_parse_version():
    assert updates.parse_version("v0.1.12") == (0, 1, 12)
    assert updates.parse_version("0.2") == (0, 2) and updates.parse_version("0.10.0") > updates.parse_version("0.9.9")
    assert updates.parse_version("nightly") is None and updates.parse_version(None) is None


def test_a_newer_release_is_reported_with_its_page():
    gh = github(release={"tag_name": "v0.1.2", "html_url": "https://github.com/x/releases/tag/v0.1.2"})
    r = updates.check(CLONE, gh)
    assert r.kind == "release" and "Version 0.1.2 is out (you have 0.1.1)" in r.text and "git pull" in r.text
    assert r.url.endswith("/v0.1.2") and len(gh.seen) == 1                      # no need to compare commits


def test_newer_changes_on_main_than_this_build():
    gh = github(release={"tag_name": "v0.1.1"}, compare={"status": "ahead", "ahead_by": 12,
                                                          "html_url": "https://github.com/x/compare/d39b18e...main"})
    r = updates.check(CLONE, gh)
    assert r.kind == "changes" and "12 newer changes on main than your build d39b18e" in r.text
    assert "git pull" in r.text and r.url.endswith("d39b18e...main")
    assert gh.seen[1] == updates.API + "/compare/d39b18e...main"
    one = updates.check(WINDOWS, github(release={"tag_name": "v0.1.1"}, compare={"status": "ahead", "ahead_by": 1}))
    assert "There is 1 newer change on main" in one.text and "Windows build" in one.text
    assert one.url == updates.WINDOWS_BUILDS                                       # where the newest exe is


def test_up_to_date_and_builds_newer_than_main():
    same = updates.check(CLONE, github(release={"tag_name": "v0.1.1"}, compare={"status": "identical", "ahead_by": 0}))
    assert same.kind == "current" and "up to date" in same.text
    dev = updates.check(CLONE, github(release={"tag_name": "v0.1.1"}, compare={"status": "behind", "ahead_by": 0}))
    assert dev.kind == "current" and "newer than main" in dev.text
    unpushed = updates.check(CLONE, github(release={"tag_name": "v0.1.1"}, compare=None))       # GitHub: 404
    assert unpushed.kind == "current" and "not on GitHub" in unpushed.text


def test_a_copy_without_a_build_only_checks_releases():
    gh = github(release={"tag_name": "v0.1.1"})
    r = updates.check(dict(CLONE, commit="", source=""), gh)
    assert r.kind == "current" and "cannot be checked" in r.text and len(gh.seen) == 1
    assert updates.check(dict(CLONE, commit="unknown"), github(release=None)).kind == "current"     # no release yet


def test_offline_or_a_bad_reply_is_an_error_result_not_an_exception():
    r = updates.check(CLONE, github(fail=urllib.error.URLError("no network")))
    assert r.kind == "error" and r.text == "Could not check for updates: no network"
    assert updates.check(CLONE, github(fail=TimeoutError("timed out"))).kind == "error"
    assert updates.check(CLONE, github(release=["not", "a", "dict"])).kind == "error"
