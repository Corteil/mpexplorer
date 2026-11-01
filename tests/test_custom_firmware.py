import io
import json

import pytest

from mpexplorer import custom_firmware as cf
from mpexplorer import sources as S
from mpexplorer.firmware import FirmwareError
from mpexplorer.settings import Settings

API = "https://api.github.com/repos/pimoroni/pimoroni-pico/releases?per_page=30"
BASE = "https://github.com/pimoroni/pimoroni-pico/releases/download"


def release(tag, date, names, prerelease=False, draft=False):
    return {"tag_name": tag, "published_at": date + "T10:00:00Z", "prerelease": prerelease, "draft": draft,
            "assets": [{"name": n, "browser_download_url": "%s/%s/%s" % (BASE, tag, n)} for n in names]}


RELEASES = [
    release("v1.29.0-2", "2026-08-31", ["pico-v1.29.0-2-pimoroni-micropython.uf2",
                                        "picow-v1.29.0-2-pimoroni-micropython.uf2", "notes.txt"]),
    release("v1.30.0-rc", "2026-09-10", ["picow-v1.30.0-rc1-pimoroni-micropython.uf2"], prerelease=True),
    release("v1.29.0-1", "2026-08-28", ["picow-v1.29.0-1-pimoroni-micropython.uf2"]),
    release("v0.0.1", "2026-08-01", ["picow-v0.0.1-pimoroni-micropython.uf2"], draft=True),
]
UF2 = b"UF2\n" + b"\x00" * 508


class Reply(io.BytesIO):
    def __init__(self, data, url):
        super().__init__(data)
        self.url, self.headers = url, {"Content-Length": str(len(data))}

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass


def site(releases=RELEASES, file=UF2, down=False, redirect=None):
    pages = {API: json.dumps(releases).encode()}
    for rel in releases:
        for a in rel["assets"]:
            pages[a["browser_download_url"]] = file

    def opener(url):
        if down or url not in pages:
            raise OSError("404 " + url)
        return Reply(pages[url], redirect or url)
    return opener


SOURCE = S.FIRMWARE_PRESETS[0]


def test_releases_become_builds_newest_first_without_drafts_or_non_firmware(tmp_path):
    cat = cf.make_catalog(S.validate_firmware_source(SOURCE), str(tmp_path), site())
    boards, saved = cat.boards()
    assert [b.id for b in boards] == ["pico", "picow"] and saved is None and boards[0].vendor == "Pimoroni MicroPython"
    builds, _ = cat.builds("picow")
    assert [(b.version, b.preview) for b in builds] == [("v1.29.0-2", False), ("v1.29.0-1", False),
                                                         ("v1.30.0-rc1", True)]          # previews after the releases
    assert builds[0].url == "%s/v1.29.0-2/picow-v1.29.0-2-pimoroni-micropython.uf2" % BASE
    assert builds[0].date == "2026-08-31"
    from mpexplorer.firmware_catalog import default_build
    assert default_build(builds).version == "v1.29.0-2"
    assert "v0.0.1" not in [b.version for b in builds]


def test_the_saved_list_is_used_offline(tmp_path):
    cf.make_catalog(S.validate_firmware_source(SOURCE), str(tmp_path), site()).boards()
    boards, saved = cf.make_catalog(S.validate_firmware_source(SOURCE), str(tmp_path), site(down=True)).boards()
    assert saved and [b.id for b in boards] == ["pico", "picow"]


def test_a_download_stays_on_the_sources_repo_and_its_file_hosts(tmp_path):
    cat = cf.make_catalog(S.validate_firmware_source(SOURCE), str(tmp_path), site())
    build = cat.builds("pico")[0][0]
    assert cat.download(build) and open(cat.path_of(build), "rb").read() == UF2
    with pytest.raises(FirmwareError, match="Refusing"):
        cat._read("https://github.com/someone-else/repo/releases/download/x/a.uf2", 10)
    with pytest.raises(FirmwareError, match="Refusing"):
        cat._read("https://example.com/a.uf2", 10)


def test_check_passes_a_good_source_and_says_what_it_found(tmp_path):
    got = cf.check_source(SOURCE, Settings(), str(tmp_path), site())
    assert got.ok and got.message == "Pimoroni MicroPython: 2 boards, newest v1.29.0-2" and got.trust_host is None


@pytest.mark.parametrize("releases,file,why", [
    ([release("v1", "2026-01-01", ["readme.txt"])], UF2, "No release file"),
    (RELEASES, b"not a uf2 file", "not a UF2 file"),
    ([release("v1", "2026-01-01", ["board-v1.0.bin"])], UF2, "0xE9"),
])
def test_check_refuses_a_source_that_does_not_look_like_firmware(tmp_path, releases, file, why):
    source = dict(SOURCE)
    got = cf.check_source(source, Settings(), str(tmp_path), site(releases, file))
    assert not got.ok and why in got.message


def test_check_reports_bad_input_and_an_unreachable_site(tmp_path):
    st = Settings()
    def message(**src):
        return cf.check_source(dict({"name": "x", "kind": "github", "repo": "a/b"}, **src), st, str(tmp_path)).message
    assert "owner/name" in message(repo="nonsense")
    assert "https://" in message(kind="url", url="http://a.example/f.uf2")
    assert "name" in message(name="")
    assert "group" in message(pattern="(.*)")
    assert "not valid" in message(pattern="(")
    assert not cf.check_source(SOURCE, st, str(tmp_path), site(down=True)).ok


def test_a_site_that_is_not_github_must_be_trusted_and_a_redirect_elsewhere_is_refused(tmp_path):
    st = Settings()
    src = {"name": "Maker", "kind": "url", "url": "https://dl.maker.example/fw/board-v1.uf2"}
    pages = {src["url"]: UF2}

    def opener(url):
        return Reply(pages[url], url)
    got = cf.check_source(src, st, str(tmp_path), opener)
    assert got.ok and got.trust_host == "dl.maker.example"                # fine, but the owner is asked about the site
    S.trust_host(st, "dl.maker.example")
    assert cf.check_source(src, st, str(tmp_path), opener).trust_host is None

    def sneaky(url):
        return Reply(UF2, "https://evil.example/board-v1.uf2")             # the answer came from somewhere else
    bad = cf.check_source(src, st, str(tmp_path), sneaky)
    assert not bad.ok and "another site" in bad.message


def test_a_direct_url_is_one_board_with_one_build(tmp_path):
    url = "https://dl.maker.example/fw/my board.uf2"
    src = S.validate_firmware_source({"name": "Maker", "kind": "url", "url": url})
    cat = cf.make_catalog(src, str(tmp_path), lambda url: Reply(UF2, url))
    assert [b.id for b in cat.boards()[0]] == ["Maker"]
    assert cat.builds("Maker")[0][0].filename == "my_board.uf2"
