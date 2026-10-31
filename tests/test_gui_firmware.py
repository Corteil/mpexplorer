"""Install firmware dialog against fake bootloader drives. Never looks at the real mounts or writes to a real board."""
import os
import struct
import time

import pytest

from guiskip import unavailable

try:
    import gi  # noqa: F401
except ImportError:
    unavailable("PyGObject (python3-gi) is not installed")
if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
    unavailable("no display (run under xvfb-run)")
try:
    from mpexplorer.gtkui import common, firmware as dialog
    from mpexplorer.gtkui.app import App
except (ImportError, ValueError) as e:
    unavailable("GTK 3 not available: %s" % e)

from mpexplorer import firmware as fw
from mpexplorer.errors import Cancelled
from mpexplorer.firmware_catalog import Board, Build
from mpexplorer.settings import Settings

Gtk = common.Gtk
RP2040 = 0xE48BFF56
ARM = 0xE48BFF59


def pump(ms=100):
    end = time.time() + ms / 1000
    while time.time() < end:
        while Gtk.events_pending():
            Gtk.main_iteration()
        time.sleep(0.005)


def wait_until(test, ms=3000):
    end = time.time() + ms / 1000
    while not test() and time.time() < end:
        pump(20)
    assert test()


def uf2(path, family=RP2040, total=4):
    head = "<8I"
    data = b"".join(struct.pack(head, fw.MAGIC_START0, fw.MAGIC_START1, fw.FLAG_HAS_FAMILY,
                                fw.FLASH_START + i * 256, 256, i, total, family)
                    + bytes(476) + struct.pack("<I", fw.MAGIC_END) for i in range(total))
    path.write_bytes(data)
    return str(path)


class FakeCatalog:
    """The site, without the network."""
    BOARDS = [Board("RPI_PICO", "Pico", "Raspberry Pi"), Board("RPI_PICO_W", "Pico W", "Raspberry Pi")]
    BUILDS = [Build("RPI_PICO-20261030-v1.30.0-preview.1.gabc.uf2", "v1.30.0-preview.1.gabc", "2026-10-30", "", True),
              Build("RPI_PICO-20260824-v1.29.0.uf2", "v1.29.0", "2026-08-24", "", False),
              Build("RPI_PICO-20260406-v1.28.0.uf2", "v1.28.0", "2026-04-06", "", False)]
    image, boards_error, saved, forgotten = None, None, None, []

    def boards(self, chip, badges=True):
        if self.boards_error:
            raise fw.FirmwareError(self.boards_error)
        return [b for b in self.BOARDS if badges or b.id != "TILDAGON"], self.saved

    def builds(self, board_id, chip=None):
        return self.BUILDS, self.saved

    def download(self, build, on_progress=None, cancelled=None):
        self.asked = build
        return self.image

    def forget(self, build):
        self.forgotten = self.forgotten + [build]


@pytest.fixture
def setup(tmp_path, monkeypatch):
    drive_dir = tmp_path / "RPI-RP2"
    drive_dir.mkdir()
    (drive_dir / fw.INFO_FILE).write_text("UF2 Bootloader v3.0\nModel: Raspberry Pi RP2\nBoard-ID: RPI-RP2\n")
    drives = [fw.read_drive(str(drive_dir))]
    monkeypatch.setattr(fw, "detect_all", lambda: list(drives))
    # the real function must never run here: it would look for and talk to real boards
    monkeypatch.setattr(fw, "install_and_verify", lambda *a, **k: pytest.fail("install not stubbed"))
    monkeypatch.setattr(dialog, "Catalog", FakeCatalog)
    monkeypatch.setattr(dialog, "ask_yes_no", lambda *a: True)
    errors = []
    monkeypatch.setattr(dialog, "error", lambda parent, title, text: errors.append(text))
    app = App(settings=Settings.load(str(tmp_path / "cfg.json")))
    dlgs = []

    def make(image=None):
        d = dialog.FirmwareDialog(app)
        dlgs.append(d)
        if image:
            d.use_file.set_active(True)
            monkeypatch.setattr(d.chooser, "get_filename", lambda: image, raising=False)
            d._check()
        return d
    yield make, drives, errors, app, tmp_path
    for d in dlgs:
        d.running = False
        d.destroy()
    app.destroy()
    pump(50)


def test_lists_the_board_and_waits_for_a_file(setup):
    make, *_ = setup
    d = make()
    d.use_file.set_active(True)
    assert d.board_box.get_active_text().startswith("RP2040 at ")
    assert "Choose a firmware" in d.note.get_text() and not d.install_btn.get_sensitive()


def test_says_when_no_board_is_there_and_finds_one_plugged_in_later(setup):
    make, drives, *_ = setup
    drive = drives.pop()
    d = make()
    assert "No board" in d.note.get_text() and not d.install_btn.get_sensitive()
    drives.append(drive)
    d.scan()
    assert d.drive() == drive


def test_an_image_for_the_board_enables_install_and_shows_its_hash(setup):
    make, _, _, _, tmp = setup
    path = uf2(tmp / "good.uf2")
    d = make(path)
    assert d.install_btn.get_sensitive()
    assert "Arm firmware for the RP2040" in d.note.get_text() and fw.sha256_of(path) in d.note.get_text()


def test_a_wrong_image_is_refused_before_install(setup):
    make, _, _, _, tmp = setup
    d = make(uf2(tmp / "pico2.uf2", family=ARM))
    assert not d.install_btn.get_sensitive() and d.note.get_text().startswith("Refused:")


def test_an_unsupported_bootloader_can_be_seen_but_not_written(setup):
    make, drives, _, _, tmp = setup
    drives[0] = fw.BootDrive(drives[0].path, "X", "SAMD21", None)
    d = make(uf2(tmp / "x.uf2"))
    assert "Unsupported (SAMD21)" in d.board_box.get_active_text() and not d.install_btn.get_sensitive()


def test_declining_the_question_writes_nothing(setup, monkeypatch):
    make, _, _, _, tmp = setup
    d = make(uf2(tmp / "good.uf2"))
    monkeypatch.setattr(dialog, "ask_yes_no", lambda *a: False)
    d.install()                                     # the fixture's stub fails the test if the install starts
    pump()
    assert not d.running


def test_success_reports_the_version_and_offers_connect(setup, monkeypatch):
    make, _, errors, app, tmp = setup
    path = uf2(tmp / "good.uf2")
    calls = []

    def fake(image, drive, on_progress, on_stage, cancelled):
        calls.append((image, drive))
        on_stage("Copying the firmware")
        on_progress(1, 2)
        return fw.Installed("/dev/ttyACM9", "1.29.0", "Raspberry Pi Pico with RP2040")
    monkeypatch.setattr(fw, "install_and_verify", fake)
    connected = []
    monkeypatch.setattr(app, "connect_device", lambda: connected.append(app.port_box.get_child().get_text()))
    d = make(path)
    drive = d.drive()
    d.install()
    wait_until(lambda: not d.running)
    assert calls == [(path, drive)] and not errors
    assert "MicroPython 1.29.0 on Raspberry Pi Pico with RP2040, now at /dev/ttyACM9" in d.note.get_text()
    assert d.connect_btn.get_visible() and not d.install_btn.get_sensitive()
    d._response(d, Gtk.ResponseType.APPLY)
    assert connected == ["/dev/ttyACM9"]


def test_failure_reports_and_lets_you_try_again(setup, monkeypatch):
    make, _, errors, _, tmp = setup

    def fake(*a, **k):
        raise fw.FirmwareError("The board did not restart")
    monkeypatch.setattr(fw, "install_and_verify", fake)
    d = make(uf2(tmp / "good.uf2"))
    d.install()
    wait_until(lambda: not d.running)
    assert errors == ["The board did not restart"]
    assert d.install_btn.get_sensitive() and not d.connect_btn.get_visible()


def test_cancel_stops_the_copy_and_warns_about_part_written_flash(setup, monkeypatch):
    make, _, errors, _, tmp = setup

    def fake(image, drive, on_progress, on_stage, cancelled):
        while not cancelled():
            time.sleep(0.005)
        raise Cancelled()
    monkeypatch.setattr(fw, "install_and_verify", fake)
    d = make(uf2(tmp / "good.uf2"))
    d.install()
    assert d.running and d.cancel_btn.get_visible()
    d._response(d, Gtk.ResponseType.NONE)
    wait_until(lambda: not d.running)
    assert "part written" in d.note.get_text() and not errors


def test_the_window_cannot_close_while_installing(setup, monkeypatch):
    make, _, _, _, tmp = setup
    monkeypatch.setattr(fw, "install_and_verify", lambda *a, **k: time.sleep(0.2) or fw.Installed("p", "r", "RP2040"))
    d = make(uf2(tmp / "good.uf2"))
    d.install()
    assert d.emit("delete-event", common.Gdk.Event.new(common.Gdk.EventType.DELETE)) is True
    d._response(d, Gtk.ResponseType.CLOSE)
    assert d.running and d.get_visible()
    wait_until(lambda: not d.running)


def test_install_and_verify_runs_the_steps_in_order(tmp_path, monkeypatch):
    order = []
    monkeypatch.setattr(fw, "rp2_ports", lambda: order.append("ports") or {"/dev/a"})
    monkeypatch.setattr(fw, "install", lambda *a: order.append("install"))
    monkeypatch.setattr(fw, "wait_for_new_port", lambda before: order.append(before) or "/dev/b")
    monkeypatch.setattr(fw, "identify", lambda port, chip: fw.Installed(port, "1", chip))
    drive = fw.BootDrive("/x", "m", "RPI-RP2", "rp2040")
    assert fw.install_and_verify("img", drive).port == "/dev/b"
    assert order == ["ports", "install", {"/dev/a"}]


def site_ready(d, product=0):
    wait_until(lambda: d.boards)
    d.product_box.set_active(product)
    wait_until(lambda: d.build())


def test_the_site_list_is_for_the_found_chip_and_no_board_is_chosen_for_you(setup):
    make, *_ = setup
    d = make()
    wait_until(lambda: d.boards)
    assert d.product() is None and not d.install_btn.get_sensitive() and "Choose your board" in d.note.get_text()


def test_the_default_version_is_the_latest_release_and_the_rest_are_opt_in(setup):
    make, *_ = setup
    d = make()
    site_ready(d)
    assert d.build().version == "v1.29.0" and len(d.shown_builds) == 1 and d.install_btn.get_sensitive()
    assert "RPI_PICO-20260824-v1.29.0.uf2" in d.note.get_text()
    d.all_builds.set_active(True)
    assert len(d.shown_builds) == 3 and d.build().version == "v1.29.0"
    assert "(latest)" in d.build_box.get_active_text()


def test_no_list_says_why_and_a_file_can_still_be_used(setup, monkeypatch):
    make, _, _, _, tmp = setup
    monkeypatch.setattr(FakeCatalog, "boards_error", "Cannot read the site")
    d = make()
    wait_until(lambda: "Cannot read the site" in d.note.get_text())
    assert not d.install_btn.get_sensitive()
    monkeypatch.setattr(d.chooser, "get_filename", lambda: uf2(tmp / "good.uf2"), raising=False)
    d.use_file.set_active(True)
    assert d.install_btn.get_sensitive()


def test_an_offline_list_says_so(setup, monkeypatch):
    make, *_ = setup
    monkeypatch.setattr(FakeCatalog, "saved", 1000000000.0)
    d = make()
    site_ready(d)
    assert "Offline: using the list saved on" in d.note.get_text() and d.install_btn.get_sensitive()


def test_install_from_the_site_downloads_checks_then_installs(setup, monkeypatch):
    make, _, errors, _, tmp = setup
    image = uf2(tmp / "site.uf2")
    calls = []

    def fake(path, drive, on_progress, on_stage, cancelled):
        calls.append(path)
        return fw.Installed("/dev/ttyACM9", "1.29.0", "Raspberry Pi Pico with RP2040")
    monkeypatch.setattr(fw, "install_and_verify", fake)
    d = make()
    site_ready(d)
    d.catalog.image = image
    d.install()
    wait_until(lambda: not d.running)
    assert calls == [image] and d.catalog.asked.version == "v1.29.0" and not errors
    assert fw.sha256_of(image) in d.note.get_text()


def test_a_list_that_arrives_late_cannot_reenable_install_or_hide_the_result(setup, monkeypatch):
    make, _, _, _, tmp = setup
    monkeypatch.setattr(fw, "install_and_verify", lambda *a, **k: time.sleep(0.2) or fw.Installed("p", "1", "RP2040"))
    d = make(uf2(tmp / "good.uf2"))
    d.install()
    d._boards_loaded((FakeCatalog.BOARDS, None), None)
    assert d.running and not d.install_btn.get_sensitive()
    wait_until(lambda: not d.running)
    d._boards_loaded((FakeCatalog.BOARDS, None), None)
    assert "Installed" in d.note.get_text() and not d.install_btn.get_sensitive()


def test_a_bad_download_is_forgotten_and_never_written(setup):
    make, _, errors, _, tmp = setup
    d = make()
    site_ready(d)
    d.catalog.image = uf2(tmp / "wrong.uf2", family=ARM)
    d.install()                                     # the fixture's stub fails the test if the install starts
    wait_until(lambda: not d.running)
    assert len(errors) == 1 and "not for the" in errors[0]
    assert [b.version for b in d.catalog.forgotten] == ["v1.29.0"]


def test_a_failed_install_from_the_site_can_be_tried_again(setup, monkeypatch):
    make, _, errors, _, tmp = setup

    def fake(*a, **k):
        raise fw.FirmwareError("The board did not restart")
    monkeypatch.setattr(fw, "install_and_verify", fake)
    d = make()
    site_ready(d)
    d.catalog.image = uf2(tmp / "site.uf2")
    d.install()
    wait_until(lambda: not d.running)
    assert errors and d.site_rows.get_sensitive()
    wait_until(lambda: d.boards)


def test_a_badge_in_its_rom_is_offered_the_tildagon_and_needs_esptool(setup, monkeypatch):
    make, drives, *_ = setup
    drives[0] = fw.BootDrive("/dev/ttyACM0", "ESP32-S3 ROM bootloader", "ESP32-S3", "esp32s3", "esptool")
    monkeypatch.setattr(FakeCatalog, "BOARDS", [Board("TILDAGON", "Tildagon", "EMF Camp")])
    monkeypatch.setattr(FakeCatalog, "BUILDS", [Build("T-merged.bin", "Latest", "", "", False, "https://x/")])

    def missing():
        raise fw.FirmwareError(fw.INSTALL_ESPTOOL)
    monkeypatch.setattr(fw, "find_esptool", missing)
    monkeypatch.setattr(fw, "read_esp_chip", lambda port: fw.EspChip("ESP32-S3-PICO-1", 8, 2))
    d = make()
    assert d.board_box.get_active_text() == "ESP32-S3 at /dev/ttyACM0"
    assert "pipx install esptool" in d.note.get_text() and not d.install_btn.get_sensitive()
    monkeypatch.setattr(fw, "find_esptool", lambda: ["esptool"])
    d._drive_changed()
    site_ready(d)
    assert "T-merged.bin" in d.note.get_text() and d.install_btn.get_sensitive()


def rom_with(monkeypatch, drives, chip):
    drives[0] = fw.BootDrive("/dev/ttyACM0", "ESP32-S3 ROM bootloader", "ESP32-S3", "esp32s3", "esptool")
    monkeypatch.setattr(FakeCatalog, "BOARDS", [Board("TILDAGON", "Tildagon", "EMF Camp")])
    monkeypatch.setattr(fw, "find_esptool", lambda: ["esptool"])
    monkeypatch.setattr(fw, "read_esp_chip", chip)


def test_a_breakout_is_offered_its_boards_not_the_tildagon_and_says_what_it_found(setup, monkeypatch):
    make, drives, *_ = setup
    rom_with(monkeypatch, drives, lambda port: fw.EspChip("ESP32-S3", 16, 8))
    monkeypatch.setattr(FakeCatalog, "BOARDS", [Board("TILDAGON", "Tildagon", "EMF Camp"),
                                                Board("ESP32_GENERIC_S3", "ESP32-S3", "Espressif")])
    d = make()
    wait_until(lambda: d.boards)
    assert [b.id for b in d.boards] == ["ESP32_GENERIC_S3"] and d.product() is None
    assert "does not match a Tildagon" in d.note.get_text() and "16 MB flash, 8 MB PSRAM" in d.note.get_text()
    assert not d.install_btn.get_sensitive()
    d.product_box.set_active(0)
    wait_until(lambda: d.build())
    assert d.install_btn.get_sensitive()


def tildagon_chip_with_both_boards(monkeypatch, drives):
    rom_with(monkeypatch, drives, lambda port: fw.EspChip("ESP32-S3-PICO-1", 8, 2))
    monkeypatch.setattr(FakeCatalog, "BOARDS", [Board("TILDAGON", "Tildagon", "EMF Camp"),
                                                Board("WAVESHARE", "ESP32-S3-Pico", "Waveshare")])


@pytest.mark.parametrize("product, expected", [(0, True), (1, False)])
def test_the_chosen_board_decides_whether_a_tildagon_is_waited_for(setup, monkeypatch, product, expected):
    make, drives, _, _, tmp = setup
    tildagon_chip_with_both_boards(monkeypatch, drives)
    got = {}

    def fake(image, drive, on_progress, on_stage, cancelled, expect_tildagon=None, erase=False):
        got.update(expect_tildagon=expect_tildagon, erase=erase)
        return fw.Installed("", "", "")
    monkeypatch.setattr(fw, "install_and_verify", fake)
    monkeypatch.setattr(fw, "check_image", lambda *a: None)
    d = make()
    site_ready(d, product)
    d.catalog.image = uf2(tmp / "x.uf2")
    d.install()
    wait_until(lambda: not d.running)
    assert got == {"expect_tildagon": expected, "erase": False}
    assert "cannot follow this board" in d.note.get_text()


def test_erase_is_off_by_default_only_for_esp_and_asks_again(setup, monkeypatch):
    make, drives, _, _, tmp = setup
    d = make()
    assert not d.erase.get_sensitive()              # a UF2 drive cannot do it
    tildagon_chip_with_both_boards(monkeypatch, drives)
    asked = []

    def answer(parent, title, text):
        asked.append(title)
        return title != "Erase the flash"
    monkeypatch.setattr(dialog, "ask_yes_no", answer)
    d2 = make()
    site_ready(d2)
    assert d2.erase.get_sensitive() and not d2.erase.get_active()
    d2.erase.set_active(True)
    d2.install()                                    # declined: the fixture's stub fails the test if it starts
    pump()
    assert asked == ["Install firmware", "Erase the flash"] and not d2.running


def test_a_breakout_can_still_use_a_file_that_fits(setup, monkeypatch):
    make, drives, _, _, tmp = setup
    rom_with(monkeypatch, drives, lambda port: fw.EspChip("ESP32-S3", 16, 8))
    image = bytearray(0x10100)
    image[0], image[3], image[12], image[0x8000], image[0x8001], image[0x10000] = 0xE9, 0x4F, 9, 0xAA, 0x50, 0xE9
    path = tmp / "generic.bin"
    path.write_bytes(image)
    d = make()
    wait_until(lambda: d.drive().esp_chip)
    monkeypatch.setattr(d.chooser, "get_filename", lambda: str(path), raising=False)
    d.use_file.set_active(True)
    assert d.install_btn.get_sensitive()            # 16 MB image, 16 MB flash


def test_nothing_is_offered_until_the_chip_has_been_read(setup, monkeypatch):
    make, drives, *_ = setup

    def fails(port):
        raise fw.FirmwareError("Cannot read the chip on /dev/ttyACM0. Is it in its bootloader?")
    rom_with(monkeypatch, drives, fails)
    d = make()
    assert not d.install_btn.get_sensitive()
    wait_until(lambda: "Cannot read the chip" in d.note.get_text())
    assert not d.boards and not d.install_btn.get_sensitive()
