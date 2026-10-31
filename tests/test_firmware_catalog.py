"""The micropython.org catalog, against pages shaped like the real site (checked by hand on 2026-10-31). No network."""
import io
import json
import os
import urllib.error

import pytest

from mpexplorer import firmware_catalog as cat
from mpexplorer.errors import Cancelled
from mpexplorer.firmware import FirmwareError

INDEX = """
<a href="?mcu=rp2040" class="filter-option">rp2040</a>
<a class="board-card" href="RPI_PICO2_W">
  <div class="board-image"><img src="x.jpg"></div>
  <div class="board-product">Pico 2 W</div>
  <div class="board-vendor">Raspberry Pi</div>
</a>
<a class="board-card" href="ADAFRUIT_FEATHER_RP2350">
  <div class="board-product">Feather &amp; Friends</div>
  <div class="board-vendor">Adafruit</div>
</a>
"""
PAGE = """
<h2>Firmware</h2><h3>Releases</h3>
<a href="/resources/firmware/RPI_PICO2-20260406-v1.28.0.uf2">v1.28.0 (2026-04-06) .uf2</a>
<a href="/resources/firmware/RPI_PICO2-20260824-v1.29.0.uf2">v1.29.0 (2026-08-24) .uf2</a>
<a href="https://github.com/micropython/micropython/releases/tag/v1.29.0">[Release notes]</a>
<h3>Preview builds</h3>
<a href="/resources/firmware/RPI_PICO2-20261030-v1.30.0-preview.206.g8ee4798dc2.uf2">preview</a>
<h2>Firmware (RISC-V CPU mode)</h2>
<a href="/resources/firmware/RPI_PICO2-RISCV-20260824-v1.29.0.uf2">riscv</a>
<a href="/resources/firmware/RPI_PICO2_W-20260824-v1.29.0.uf2">another board's file</a>
<a href="/resources/firmware/RPI_PICO2-20260824-v1.29.0.app-bin">not a UF2</a>
"""


class Reply(io.BytesIO):
    def __init__(self, data, url, length=True):
        super().__init__(data)
        self.url = url
        self.headers = {"Content-Length": str(len(data))} if length else {}

    def geturl(self):
        return self.url


class Site:
    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def __call__(self, url):
        self.asked.append(url)
        page = self.pages.get(url)
        if page is None:
            raise urllib.error.URLError("offline")
        return page(url) if callable(page) else Reply(page, url)


def index_url(chip="rp2350"):
    return "%s/download/?mcu=%s" % (cat.BASE, chip)


def page_url(board="RPI_PICO2"):
    return "%s/download/%s/" % (cat.BASE, board)


def file_url(name):
    return cat.BASE + cat.FIRMWARE_PATH + name


def test_parse_boards_reads_cards_and_sorts_them():
    boards = cat.parse_boards(INDEX)
    assert [(b.id, b.label) for b in boards] == [("ADAFRUIT_FEATHER_RP2350", "Adafruit Feather & Friends"),
                                                 ("RPI_PICO2_W", "Raspberry Pi Pico 2 W")]


def test_parse_builds_keeps_this_boards_uf2s_newest_first():
    builds = cat.parse_builds(PAGE, "RPI_PICO2")
    assert [b.filename for b in builds] == ["RPI_PICO2-20261030-v1.30.0-preview.206.g8ee4798dc2.uf2",
                                            "RPI_PICO2-20260824-v1.29.0.uf2",
                                            "RPI_PICO2-RISCV-20260824-v1.29.0.uf2",
                                            "RPI_PICO2-20260406-v1.28.0.uf2"]
    assert builds[0].preview and builds[2].variant == "RISCV" and builds[1].date == "2026-08-24"
    assert "RISC-V" in builds[2].label


def test_the_default_is_the_newest_plain_release():
    assert cat.default_build(cat.parse_builds(PAGE, "RPI_PICO2")).version == "v1.29.0"
    only_preview = cat.parse_builds('<a href="/resources/firmware/X-20261030-v1.30.0-preview.1.gabc.uf2">', "X")
    assert only_preview and cat.default_build(only_preview) is None


def test_lists_are_saved_and_used_when_offline(tmp_path):
    site = Site({index_url(): INDEX.encode(), page_url(): PAGE.encode()})
    c = cat.Catalog(str(tmp_path), site)
    boards, saved = c.boards("rp2350")
    builds, saved2 = c.builds("RPI_PICO2")
    assert len(boards) == 2 and saved is None and saved2 is None
    site.pages.clear()
    again, saved = c.boards("rp2350")
    assert again == boards and saved is not None
    assert c.builds("RPI_PICO2")[0] == builds


def test_offline_with_nothing_saved_is_an_error(tmp_path):
    with pytest.raises(FirmwareError, match="Cannot read"):
        cat.Catalog(str(tmp_path), Site({})).boards("rp2350")


def test_a_changed_page_uses_the_saved_list_or_says_so(tmp_path):
    site = Site({index_url(): b"<html>new design</html>"})
    c = cat.Catalog(str(tmp_path), site)
    with pytest.raises(FirmwareError, match="page may have changed"):
        c.boards("rp2350")
    (tmp_path / "boards-rp2350.json").write_text(json.dumps([{"id": "A", "product": "P", "vendor": "V"}]))
    assert c.boards("rp2350")[0][0].id == "A"


def test_a_damaged_saved_list_does_not_hide_the_real_error(tmp_path):
    (tmp_path / "boards-rp2350.json").write_text("not json")
    with pytest.raises(FirmwareError, match="Cannot read"):
        cat.Catalog(str(tmp_path), Site({})).boards("rp2350")


def build(name="RPI_PICO2-20260824-v1.29.0.uf2"):
    return cat.Build(name, "v1.29.0", "2026-08-24", "", False)


def test_download_saves_reports_progress_and_reuses_the_copy(tmp_path):
    data = b"x" * 200000
    site = Site({file_url(build().filename): data})
    c = cat.Catalog(str(tmp_path), site)
    seen = []
    path = c.download(build(), on_progress=lambda a, b: seen.append((a, b)))
    assert open(path, "rb").read() == data and seen[-1] == (200000, 200000)
    assert c.download(build()) == path and len(site.asked) == 1
    c.forget(build())
    assert not os.path.exists(path)
    c.forget(build())                               # nothing there: no error


def test_a_short_download_leaves_no_file(tmp_path):
    def short(url):
        r = Reply(b"xxx", url)
        r.headers = {"Content-Length": "10"}
        return r
    c = cat.Catalog(str(tmp_path), Site({file_url(build().filename): short}))
    with pytest.raises(FirmwareError, match="stopped early"):
        c.download(build())
    assert not os.path.exists(c.path_of(build()))


def test_download_can_be_cancelled_and_leaves_no_file(tmp_path):
    c = cat.Catalog(str(tmp_path), Site({file_url(build().filename): b"x" * 10}))
    with pytest.raises(Cancelled):
        c.download(build(), cancelled=lambda: True)
    assert not os.path.exists(c.path_of(build()))


def test_a_redirect_off_the_site_is_refused(tmp_path):
    site = Site({file_url(build().filename): lambda url: Reply(b"x", "https://evil.example/fw.uf2")})
    with pytest.raises(FirmwareError, match="redirected"):
        cat.Catalog(str(tmp_path), site).download(build())


def test_an_oversized_download_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(cat, "MAX_IMAGE", 5)
    c = cat.Catalog(str(tmp_path), Site({file_url(build().filename): b"x" * 10}))
    with pytest.raises(FirmwareError, match="too large"):
        c.download(build())


@pytest.mark.parametrize("name", ["../evil.uf2", "a/b.uf2", "x.exe"])
def test_a_bad_name_from_a_saved_list_cannot_leave_the_cache(tmp_path, name):
    site = Site({})
    with pytest.raises(FirmwareError, match="Bad firmware name"):
        cat.Catalog(str(tmp_path), site).download(build(name))
    assert not site.asked


def test_the_tildagon_comes_from_the_table_without_the_network(tmp_path):
    c = cat.Catalog(str(tmp_path), Site({}))
    boards, saved = c.boards("esp32s3")             # the site is out of reach: the badge is still there
    assert [b.id for b in boards] == ["TILDAGON"] and saved is None
    site = Site({})
    builds, _ = cat.Catalog(str(tmp_path), site).builds("TILDAGON")
    assert cat.default_build(builds).url == cat.EMF_FLASHER and not site.asked


S3_INDEX = """
<a class="board-card" href="ESP32_GENERIC_S3"><div class="board-product">ESP32-S3</div>
<div class="board-vendor">Espressif</div></a>
<a class="board-card" href="ARDUINO_NANO_ESP32"><div class="board-product">Nano ESP32</div>
<div class="board-vendor">Arduino</div></a>
"""
S3_PAGE = """
<a href="/resources/firmware/ESP32_GENERIC_S3-20260824-v1.29.0.uf2">uf2</a>
<a href="/resources/firmware/ESP32_GENERIC_S3-20260824-v1.29.0.app-bin">app</a>
<a href="/resources/firmware/ESP32_GENERIC_S3-20260824-v1.29.0.bin">bin</a>
<a href="/resources/firmware/ESP32_GENERIC_S3-20260824-v1.29.0.meta.zip">meta</a>
<a href="/resources/firmware/ESP32_GENERIC_S3-SPIRAM_OCT-20260824-v1.29.0.bin">octal</a>
"""


def test_esp32s3_boards_follow_the_badge_and_a_chip_that_differs_gets_no_badge(tmp_path):
    c = cat.Catalog(str(tmp_path), Site({index_url("esp32s3"): S3_INDEX.encode()}))
    assert [b.id for b in c.boards("esp32s3")[0]] == ["TILDAGON", "ESP32_GENERIC_S3"]
    assert [b.id for b in c.boards("esp32s3", badges=False)[0]] == ["ESP32_GENERIC_S3"]


def test_a_chip_that_differs_is_told_when_the_site_is_out_of_reach(tmp_path):
    with pytest.raises(FirmwareError, match="Cannot read"):
        cat.Catalog(str(tmp_path), Site({})).boards("esp32s3", badges=False)


def test_esp32s3_builds_are_the_full_bin_and_never_the_uf2_or_app_bin(tmp_path):
    url = page_url("ESP32_GENERIC_S3")
    builds, _ = cat.Catalog(str(tmp_path), Site({url: S3_PAGE.encode()})).builds("ESP32_GENERIC_S3", "esp32s3")
    assert sorted(b.filename for b in builds) == ["ESP32_GENERIC_S3-20260824-v1.29.0.bin",
                                                  "ESP32_GENERIC_S3-SPIRAM_OCT-20260824-v1.29.0.bin"]
    assert cat.default_build(builds).filename == "ESP32_GENERIC_S3-20260824-v1.29.0.bin"


def test_a_file_that_changes_under_one_name_is_read_again_and_the_copy_is_the_fallback(tmp_path):
    b = cat.BADGE_BUILDS["TILDAGON"][0]
    site = Site({cat.EMF_FLASHER: b"one"})
    c = cat.Catalog(str(tmp_path), site)
    path = c.download(b)
    site.pages[cat.EMF_FLASHER] = b"two"
    assert open(c.download(b), "rb").read() == b"two" and len(site.asked) == 2
    site.pages.clear()
    assert c.download(b) == path and open(path, "rb").read() == b"two"
    c.forget(b)
    with pytest.raises(FirmwareError, match="Cannot read"):
        c.download(b)
