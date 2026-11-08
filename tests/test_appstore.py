"""The badge app store core (docs/APP_STORE_PLAN.md phase 1): no network and no badge; built tarballs and a local
folder standing in for the badge's file system."""
import gzip
import hashlib
import io
import json
import os
import tarfile

import pytest

from mpexplorer import appstore, paths
from mpexplorer.appstore import AppStoreError
from mpexplorer.backends import LocalBackend

STORE = "https://apps.badge.emfcamp.org/v1/tarballs/"


def tarball(files, root="owner-repo-abc1234", dirs=True, extra=()):
    """A gzip'd tar like the store's: one root folder holding `files` ({relative path: bytes}). `extra` adds
    (TarInfo, bytes or None) members as they are, for the bad cases."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        if dirs:
            d = tarfile.TarInfo(root)
            d.type = tarfile.DIRTYPE
            t.addfile(d)
        for rel, data in files.items():
            if dirs and "/" in rel:
                sub = tarfile.TarInfo(root + "/" + rel.rsplit("/", 1)[0])
                sub.type = tarfile.DIRTYPE
                t.addfile(sub)
            info = tarfile.TarInfo(root + "/" + rel)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
        for info, data in extra:
            t.addfile(info, io.BytesIO(data) if data is not None else None)
    return gzip.compress(buf.getvalue())


def entry(code="10000001", owner="alice", title="neat-app", version="1.0", category="Apps", caps=None,
          release="abc1234def", url=None, name=None):
    meta = {"author": owner, "license": "MIT", "url": "https://github.com/%s/%s" % (owner, title),
            "description": "A test app", "version": version}
    if caps is not None:
        meta["capabilities"] = caps
    return {"code": code, "id": {"service": "github", "owner": owner, "title": title, "releaseHash": release},
            "releaseTime": "2026-11-01T10:00:00Z",
            "tarballUrl": url or "%s%s-%s.tar.gz" % (STORE, code, release),
            "manifest": {"app": {"name": name or title, "category": category}, "metadata": meta}}


class Reply:
    def __init__(self, data, url):
        self.data, self.url = data, url

    def read(self, n=-1):
        return self.data if n < 0 else self.data[:n]

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Web:
    """A fake internet: {url: bytes}; counts requests; `down` makes every request fail."""

    def __init__(self, pages):
        self.pages, self.calls, self.down, self.redirect = dict(pages), [], False, {}

    def __call__(self, url):
        self.calls.append(url)
        if self.down or url not in self.pages:
            raise OSError("no route to host")
        return Reply(self.pages[url], self.redirect.get(url, url))


def catalogue(*entries):
    return json.dumps({"count": len(entries), "items": list(entries)}).encode("utf-8")


APP_PY = b"import app\nclass Neat(app.App):\n    pass\n__app_export__ = Neat\n"


def web_for(*pairs):
    """A Web serving the catalogue of these (entry, tarball bytes) pairs and each tarball."""
    pages = {appstore.LISTING: catalogue(*[e for e, _t in pairs])}
    for e, t in pairs:
        pages[e["tarballUrl"]] = t
    return Web(pages)


def store_for(tmp_path, *pairs, clock=lambda: 1793000000):
    web = web_for(*pairs)
    return appstore.Store(str(tmp_path / "store"), web, clock), web


# -- the catalogue ----------------------------------------------------------------------------------------------------
def test_a_catalogue_entry_gives_the_badges_folder_target_and_a_stable_key():
    apps, skipped = appstore.parse_catalogue(catalogue(
        entry(owner="robmckinnon", title="tildagon-and0r", category=["Media"]),
        entry(code="2", owner="x", title="glow-bg", category="Background"),
        entry(code="3", owner="y", title="swirl", category=["Pattern", "Apps"])))
    assert skipped == 0
    by = {a.title: a for a in apps}
    a = by["tildagon-and0r"]
    assert a.folder == "robmckinnon_tildagon_and0r" and a.target == "apps" and a.category == "Media"
    assert a.key == "github/robmckinnon/tildagon-and0r" and a.tarball_name == "10000001-abc1234def.tar.gz"
    assert by["glow-bg"].target == "backgrounds" and by["swirl"].target == "pattern"     # first category decides
    assert [x.name for x in apps] == sorted((x.name for x in apps), key=str.lower)


def test_codeberg_apps_name_their_tarball_by_tag():
    # 64 of the 263 live apps are on Codeberg, with a tag where GitHub apps have a commit hash (2026-11-08)
    e = entry(code="13411001", owner="andypiper", title="tildagon-scd-app", release="v0.1.3")
    e["id"]["service"] = "codeberg"
    apps, skipped = appstore.parse_catalogue(catalogue(e))
    assert skipped == 0 and apps[0].tarball_name == "13411001-v0.1.3.tar.gz"
    assert apps[0].key == "codeberg/andypiper/tildagon-scd-app"
    for bad in ("1-..tar.gz", "1-a..b.tar.gz", "1-.hidden.tar.gz", "1-a/b.tar.gz"):
        assert not appstore.TARBALL_NAME.match(bad), bad


def test_unsafe_or_malformed_entries_are_skipped_not_fatal():
    bad_host = entry(code="5", title="elsewhere", url="https://evil.example/t/5-abc1234.tar.gz")
    bad_name = entry(code="6", title="x", url=STORE + "6-abc1234.zip")                 # not <code>-<hash>.tar.gz
    bad_folder = entry(code="7", owner="a b", title="c")                               # a space in the folder name
    broken = {"code": "8", "id": {"owner": "z"}}                                       # no title, no manifest
    apps, skipped = appstore.parse_catalogue(catalogue(entry(), bad_host, bad_name, bad_folder, broken))
    assert [a.title for a in apps] == ["neat-app"] and skipped == 4
    with pytest.raises(AppStoreError, match="expected format"):
        appstore.parse_catalogue(b'{"nope": 1}')


def test_the_catalogue_is_saved_and_used_offline(tmp_path):
    store, web = store_for(tmp_path, (entry(), tarball({"app.py": APP_PY})))
    apps, fetched, online = store.catalogue()
    assert online and [a.title for a in apps] == ["neat-app"] and fetched.startswith("2026-")
    saved = json.loads((tmp_path / "store" / "store.json").read_text(encoding="utf-8"))
    assert saved["source"] == appstore.LISTING and saved["fetched"] == fetched and saved["items"][0]["code"]
    web.down = True
    apps, fetched2, online = store.catalogue()
    assert not online and fetched2 == fetched and [a.title for a in apps] == ["neat-app"]


def test_no_catalogue_and_no_saved_copy_is_an_error(tmp_path):
    store = appstore.Store(str(tmp_path / "s"), Web({}))
    with pytest.raises(AppStoreError, match="Cannot read"):
        store.catalogue()


def test_only_the_store_site_is_read_even_after_a_redirect(tmp_path):
    store, web = store_for(tmp_path, (entry(), tarball({"app.py": APP_PY})))
    web.redirect[appstore.LISTING] = "https://evil.example/apps"
    with pytest.raises(AppStoreError, match="redirected"):
        store.refresh()
    with pytest.raises(AppStoreError, match="Refusing"):
        store._read("https://evil.example/x", 10)


# -- the local store folder -------------------------------------------------------------------------------------------
def test_a_tarball_is_downloaded_once_kept_and_checked(tmp_path):
    e, t = entry(), tarball({"app.py": APP_PY})
    store, web = store_for(tmp_path, (e, t))
    app = store.refresh()[0]
    assert not store.has(app)
    assert store.tarball(app) == t and store.has(app)
    record = json.loads((tmp_path / "store" / "files.json").read_text(encoding="utf-8"))[app.tarball_name]
    assert record["size"] == len(t) and record["sha256"] == hashlib.sha256(t).hexdigest()
    web.down = True
    assert store.tarball(app) == t                                     # offline: the kept copy
    (tmp_path / "store" / "tarballs" / app.tarball_name).write_bytes(b"damaged")
    assert not store.has(app)
    with pytest.raises(AppStoreError, match="Cannot read"):           # damaged and offline: it says so
        store.tarball(app)
    web.down = False
    assert store.tarball(app) == t and store.has(app)                  # online again: fetched afresh
    with pytest.raises(AppStoreError, match="not in the local store"):
        appstore.Store(str(tmp_path / "other"), web).tarball(app, download=False)


def test_a_store_folder_copied_without_files_json_is_still_used(tmp_path):
    e, t = entry(), tarball({"app.py": APP_PY})
    store, web = store_for(tmp_path, (e, t))
    app = store.refresh()[0]
    store.tarball(app)
    os.remove(tmp_path / "store" / "files.json")
    web.down = True
    assert store.has(app) and store.tarball(app) == t


def test_download_all_fetches_what_is_missing_and_carries_on_past_a_bad_app(tmp_path):
    good, bad, also = entry(code="1"), entry(code="2", title="broken"), entry(code="3", title="third")
    store, web = store_for(tmp_path, (good, tarball({"app.py": APP_PY})), (bad, b"not a tarball"),
                           (also, tarball({"app.py": APP_PY})))
    apps = store.refresh()
    seen = []
    downloaded, present, failed = store.download_all(apps, on_progress=lambda a, b: seen.append((a, b)))
    assert (downloaded, present) == (2, 0) and [a.title for a, _why in failed] == ["broken"]
    assert seen[-1] == (3, 3) and not os.path.exists(tmp_path / "store" / "tarballs" / "2-abc1234def.tar.gz")
    assert store.download_all(apps)[:2] == (0, 2)                       # nothing new the second time
    with pytest.raises(AppStoreError, match="Cancelled"):
        store.download_all(apps, cancelled=lambda: True)


def test_prune_removes_old_releases_but_keeps_installed_ones(tmp_path):
    store, _web = store_for(tmp_path, (entry(), tarball({"app.py": APP_PY})))
    app = store.refresh()[0]
    store.tarball(app)
    folder = tmp_path / "store" / "tarballs"
    (folder / "9-0123456.tar.gz").write_bytes(b"old release")
    (folder / "8-7654321.tar.gz").write_bytes(b"old but installed")
    (folder / "notes.txt").write_text("not ours")
    assert store.prune([app], keep={"8-7654321.tar.gz"}) == ["9-0123456.tar.gz"]
    assert sorted(os.listdir(folder)) == sorted([app.tarball_name, "8-7654321.tar.gz", "notes.txt"])


def test_favourites_are_kept_in_the_store_folder_by_repo_not_release(tmp_path):
    store, _web = store_for(tmp_path, (entry(), tarball({"app.py": APP_PY})))
    app = store.refresh()[0]
    assert store.favourites() == set()
    assert store.set_favourite(app) == {"github/alice/neat-app"}
    again = appstore.Store(str(tmp_path / "store"), Web({}))           # another computer with the copied folder
    newer_release = appstore.App(entry(version="2.0", release="ffff999"))
    assert newer_release.key in again.favourites()
    assert again.set_favourite(newer_release, False) == set()
    assert json.loads((tmp_path / "store" / "favourites.json").read_text(encoding="utf-8")) == {"apps": []}


# -- unpacking --------------------------------------------------------------------------------------------------
def app_of(**kw):
    return appstore.App(entry(**kw))


def test_unpack_follows_the_badges_install_steps():
    app = app_of(title="neat-app", version="1.2")
    plan = appstore.unpack(tarball({"app.py": APP_PY, "tildagon.toml": b"[app]\n", "img/logo.png": b"PNG",
                                    "lib/deep/util.py": b"x=1"}), app)
    rels = [r for r, _d in plan.writes()]
    assert rels == ["app.py", "img/logo.png", "lib/deep/util.py", "tildagon.json", "metadata.json"]
    assert "tildagon.toml" not in rels                                     # the badge deletes it
    assert plan.dirs == ["img", "lib", "lib/deep"] and plan.target == "apps" and plan.folder == "alice_neat_app"
    writes = dict(plan.writes())
    assert json.loads(writes["tildagon.json"]) == app.manifest
    # exactly what the badge writes: json.dump of name, hidden, version
    assert writes["metadata.json"] == b'{"name": "neat-app", "hidden": false, "version": "1.2"}'
    assert plan.size() == sum(len(d) for d in writes.values())


def test_a_tarball_with_its_own_tildagon_json_keeps_it_and_app_mpy_is_enough():
    own = b'{"app": {"name": "Own"}}'
    plan = appstore.unpack(tarball({"app.mpy": b"M\x06", "tildagon.json": own}), app_of())
    assert plan.manifest is None and dict(plan.writes())["tildagon.json"] == own


def test_a_tarball_without_directory_entries_still_has_one_root():
    plan = appstore.unpack(tarball({"app.py": APP_PY, "sub/a.py": b"a"}, dirs=False), app_of())
    assert [r for r, _d in plan.files] == ["app.py", "sub/a.py"] and plan.dirs == ["sub"]


def test_files_outside_the_apps_folder_are_skipped_as_the_badge_does():
    stray = tarfile.TarInfo("README.md")
    stray.size = 2
    plan = appstore.unpack(tarball({"app.py": APP_PY}, extra=[(stray, b"hi")]), app_of())
    assert [r for r, _d in plan.files] == ["app.py"]


def _member(name, kind=tarfile.REGTYPE, data=b"x", link=""):
    info = tarfile.TarInfo(name)
    info.type, info.linkname = kind, link
    info.size = len(data) if kind == tarfile.REGTYPE else 0
    return info, (data if kind == tarfile.REGTYPE else None)


@pytest.mark.parametrize("bad, message", [
    ([_member("owner-repo-abc1234/../../etc/evil")], "unsafe path"),
    ([_member("/abs/app.py")], "unsafe path"),
    ([_member("owner-repo-abc1234/C:evil")], "unsafe path"),
    ([_member("owner-repo-abc1234/back\\slash")], "unsafe path"),
    ([_member("owner-repo-abc1234/link", tarfile.SYMTYPE, link="/etc/passwd")], "link or special"),
    ([_member("owner-repo-abc1234/hard", tarfile.LNKTYPE, link="owner-repo-abc1234/app.py")], "link or special"),
    ([_member("second-root", tarfile.DIRTYPE)], "one folder"),
])
def test_unsafe_tarballs_are_refused_before_anything_is_written(bad, message):
    with pytest.raises(AppStoreError, match=message):
        appstore.unpack(tarball({"app.py": APP_PY}, extra=bad), app_of())


def test_a_tarball_with_no_app_py_or_not_a_tarball_is_refused(monkeypatch):
    with pytest.raises(AppStoreError, match="no app.py"):
        appstore.unpack(tarball({"main.py": b"x"}), app_of())
    with pytest.raises(AppStoreError, match="not a gzip"):
        appstore.unpack(b"<html>404</html>", app_of())
    with pytest.raises(AppStoreError, match="not a tar"):
        appstore.unpack(gzip.compress(b"plain text, not a tar archive" * 20), app_of())
    monkeypatch.setattr(appstore, "MAX_UNPACKED", 1000)
    with pytest.raises(AppStoreError, match="too large"):
        appstore.unpack(tarball({"app.py": APP_PY, "big.bin": b"\0" * 5000}), app_of())


# -- on the badge (a local folder standing in for its file system) -----------------------------------------------
def badge(tmp_path):
    root = tmp_path / "badge"
    root.mkdir(exist_ok=True)
    return LocalBackend(), str(root)


def test_install_writes_the_app_where_the_launcher_looks(tmp_path):
    e = entry(version="1.0")
    store, _web = store_for(tmp_path, (e, tarball({"app.py": APP_PY, "img/a.png": b"P", "tildagon.toml": b"t"})))
    app = store.refresh()[0]
    be, root = badge(tmp_path)
    seen = []
    assert appstore.install(store, be, app, root, on_progress=lambda a, b: seen.append((a, b))) == (4, 0)
    folder = tmp_path / "badge" / "apps" / "alice_neat_app"
    assert (folder / "app.py").read_bytes() == APP_PY and (folder / "img" / "a.png").read_bytes() == b"P"
    assert not (folder / "tildagon.toml").exists()
    meta = json.loads((folder / "metadata.json").read_bytes())
    assert meta == {"name": "neat-app", "hidden": False, "version": "1.0"}
    assert seen[-1] == (4, 4)
    assert appstore.install(store, be, app, root) == (0, 4)          # the same again: nothing re-sent


def test_an_update_copies_over_the_old_folder_as_the_badge_does(tmp_path):
    old, new = entry(version="1.0"), entry(version="1.1", release="beef111")
    be, root = badge(tmp_path)
    store, _web = store_for(tmp_path, (old, tarball({"app.py": APP_PY, "gone.py": b"old file"})))
    appstore.install(store, be, store.refresh()[0], root)
    folder = tmp_path / "badge" / "apps" / "alice_neat_app"
    (folder / "tildagon.toml").write_bytes(b"left by a failed badge install")
    store2, _web = store_for(tmp_path, (new, tarball({"app.py": APP_PY + b"# v1.1\n"})))
    written, skipped = appstore.install(store2, be, store2.refresh()[0], root)
    assert written == 3 and skipped == 0                               # app.py, tildagon.json, metadata.json changed
    assert (folder / "app.py").read_bytes().endswith(b"# v1.1\n")
    assert (folder / "gone.py").read_bytes() == b"old file"            # decision 4: left, as on the badge
    assert not (folder / "tildagon.toml").exists()
    assert json.loads((folder / "metadata.json").read_bytes())["version"] == "1.1"


def test_an_app_too_big_for_the_free_space_is_refused_before_anything_is_written(tmp_path):
    # the largest live app unpacks to 2.9 MB, more than a badge has free (2026-11-08)
    store, _web = store_for(tmp_path, (entry(), tarball({"app.py": APP_PY, "big.bin": b"\0" * 300000})))
    app = store.refresh()[0]

    class Small(LocalBackend):
        def free_space(self, p):
            return 100000
    be, root = Small(), str(tmp_path / "badge")
    os.makedirs(root)
    with pytest.raises(AppStoreError, match=r"needs 293 KB more space on the badge, and it has 98 KB free"):
        appstore.install(store, be, app, root)
    assert not (tmp_path / "badge" / "apps").exists()

    class Unknown(LocalBackend):
        def free_space(self, p):
            raise OSError("no statvfs")
    assert appstore.install(store, Unknown(), app, root)[0] == 4        # cannot say: installs anyway
    assert appstore.install(store, Small(), app, root) == (0, 4)        # an update needs no more room
    assert appstore.kib(512) == "1 KB" and appstore.kib(3 << 20) == "3.0 MB"


def test_background_and_pattern_apps_go_to_their_own_folders(tmp_path):
    bg = entry(code="1", title="glow", category="Background")
    pat = entry(code="2", title="swirl", category=["Pattern"])
    store, _web = store_for(tmp_path, (bg, tarball({"app.py": APP_PY})), (pat, tarball({"app.py": APP_PY})))
    be, root = badge(tmp_path)
    for app in store.refresh():
        appstore.install(store, be, app, root)
    assert (tmp_path / "badge" / "backgrounds" / "alice_glow" / "app.py").exists()
    assert (tmp_path / "badge" / "pattern" / "alice_swirl" / "app.py").exists()


def test_installed_reads_what_the_launcher_reads(tmp_path):
    be, root = badge(tmp_path)
    apps = tmp_path / "badge" / "apps"
    (apps / "alice_neat_app").mkdir(parents=True)
    (apps / "alice_neat_app" / "metadata.json").write_bytes(b'{"name": "Neat", "hidden": false, "version": "1.9"}')
    (apps / "hand_made").mkdir()                                        # copied by hand: no metadata.json
    (apps / "broken").mkdir()
    (apps / "broken" / "metadata.json").write_bytes(b"{not json")
    (apps / "stray.py").write_bytes(b"x")                               # a file, not an app
    (tmp_path / "badge" / "backgrounds" / "x_glow").mkdir(parents=True)
    got = {(i.target, i.folder): (i.name, i.version) for i in appstore.installed(be, root)}
    assert got == {("apps", "alice_neat_app"): ("Neat", "1.9"), ("apps", "hand_made"): ("hand_made", "0.0.0"),
                   ("apps", "broken"): ("broken", "0.0.0"), ("backgrounds", "x_glow"): ("x_glow", "0.0.0")}
    assert appstore.installed(be, str(tmp_path / "empty")) == []


def test_remove_deletes_the_apps_folder(tmp_path):
    store, _web = store_for(tmp_path, (entry(), tarball({"app.py": APP_PY, "sub/b.py": b"b"})))
    be, root = badge(tmp_path)
    appstore.install(store, be, store.refresh()[0], root)
    [inst] = appstore.installed(be, root)
    appstore.remove(be, inst)
    assert not (tmp_path / "badge" / "apps" / "alice_neat_app").exists() and appstore.installed(be, root) == []


def test_without_hashlib_on_the_board_files_are_compared_in_small_reads(tmp_path, monkeypatch):
    class NoHash(LocalBackend):
        reads = []

        def checksum(self, p):
            return None

        def open_read(self, p):
            f = super().open_read(p)
            real = f.read
            f.read = lambda n=-1: (NoHash.reads.append(n), real(n))[1]
            return f
    monkeypatch.setattr(appstore, "READ_CHUNK", 16)
    store, _web = store_for(tmp_path, (entry(), tarball({"app.py": APP_PY})))
    app = store.refresh()[0]
    root = str(tmp_path / "badge")
    os.makedirs(root)
    appstore.install(store, NoHash(), app, root)
    NoHash.reads.clear()
    assert appstore.install(store, NoHash(), app, root)[0] == 0
    assert NoHash.reads and max(NoHash.reads) <= 16


# -- versions, updates and favourites -------------------------------------------------------------------------------
@pytest.mark.parametrize("a, b", [("1.10", "1.9"), ("2", "1.9.9"), ("v2.0.0", "2.0.0-rc1"), ("1", "0"),
                                  ("0.0.10", "0.0.2"), ("1.0.1", "1.0")])
def test_newer_compares_numbers_as_numbers(a, b):
    assert appstore.newer(a, b) and not appstore.newer(b, a)


def test_equal_versions_are_not_newer():
    assert not appstore.newer("1.0", "1.0.0") and not appstore.newer("v1.2", "1.2") and not appstore.newer("x", "x")


def test_updates_and_install_all_favourites(tmp_path):
    a, b, c = (appstore.App(entry(code=str(n), title=t, version=v))
               for n, t, v in ((1, "a", "1.10"), (2, "b", "2.0"), (3, "c", "1.0")))
    on_badge = [appstore.Installed("apps", "alice_a", "/apps/alice_a", "a", "1.9", False),     # older: update
                appstore.Installed("apps", "alice_b", "/apps/alice_b", "b", "2.0", False),     # current
                appstore.Installed("apps", "zed_hand", "/apps/zed_hand", "hand", "0.0.0", False)]
    assert [(i.folder, s.version) for i, s in appstore.updates([a, b, c], on_badge)] == [("alice_a", "1.10")]
    favs = {a.key, b.key, c.key}
    assert [x.title for x in appstore.favourites_to_install([a, b, c], favs, on_badge)] == ["a", "c"]
    assert appstore.favourites_to_install([a, b, c], {b.key}, on_badge) == []


# -- requirements -----------------------------------------------------------------------------------------------
def test_requirements_are_described_and_the_os_minimum_is_checked():
    caps = [{"required": True, "feature": {"type": "TildagonOSMinimumVersion", "version": "2.0.0"}},
            {"required": True, "feature": {"type": "TildagonOsMinimumVersion", "version": "1.5"}},   # badge's spelling
            {"required": False, "feature": {"name": "GPS Hexpansion", "vid": "0x7CAB", "pid": "0xBEAC"}},
            {"required": False, "feature": {"type": "2026 Frontboard"}},
            {"required": False, "feature": {
                "type": "Capability", "identifier": "https://tildagon.badge.emfcamp.org/capabilities/registry/position/"}},
            {"required": True, "feature": {"type": "SomethingNew"}}]
    app = app_of(caps=caps)
    reqs = appstore.requirements(app, os_version="v1.9.0")
    assert [(r.kind, r.text, r.required, r.met) for r in reqs] == [
        ("os", "Tildagon OS 2.0.0 or later", True, False), ("os", "Tildagon OS 1.5 or later", True, True),
        ("hexpansion", "the GPS Hexpansion", False, None), ("frontboard", "the 2026 front board", False, None),
        ("capability", "position", False, None), ("other", "SomethingNew", True, None)]
    assert [r.text for r in appstore.unmet(reqs)] == ["Tildagon OS 2.0.0 or later"]
    assert all(r.met is None for r in appstore.requirements(app) if r.kind == "os")       # OS unknown: not judged
    assert appstore.requirements(app_of()) == []


def test_board_os_version_asks_ota_and_is_none_elsewhere():
    class Badge:
        def _run(self, code):
            assert "ota.get_version()" in code
            return b"v2.0.0\r\n"

    class Pico:
        def _run(self, code):
            raise RuntimeError("ImportError: no module named 'ota'")
    assert appstore.board_os_version(Badge()) == "v2.0.0"
    assert appstore.board_os_version(Pico()) is None and appstore.board_os_version(LocalBackend()) is None


# -- where the store lives ------------------------------------------------------------------------------------------
def test_the_store_lives_in_kept_data_not_the_cache(tmp_path):
    assert paths.data_dir("linux", {"XDG_DATA_HOME": "/x/data"}, "/home/b", exe_folder=None) == "/x/data/mpexplorer"
    assert paths.data_dir("linux", {}, "/home/b", exe_folder=None) == os.path.join("/home/b", ".local", "share",
                                                                                  "mpexplorer")
    assert paths.data_dir("win32", {"LOCALAPPDATA": "C:\\L"}, "C:\\Users\\b", exe_folder=None) == os.path.join(
        "C:\\L", "mpexplorer")
    exe = tmp_path / "portable"
    exe.mkdir()
    (exe / "portable.txt").write_text("")
    assert paths.data_dir("win32", {}, "C:\\Users\\b", exe_folder=str(exe)) == str(exe / "data")
    assert appstore.default_folder().endswith("appstore")
