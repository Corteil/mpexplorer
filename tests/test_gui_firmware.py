"""Install firmware dialog against fake bootloader drives. Never looks at the real mounts or writes to a real board."""
import os
import struct
import time
import types

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

    def boards(self, chip, badges=()):
        if self.boards_error:
            raise fw.FirmwareError(self.boards_error)
        return [b for b in self.BOARDS if b.id not in fw.BADGES or b.id in badges], self.saved

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
    monkeypatch.setattr(fw, "read_running", lambda port: fw.RunningInfo("Raspberry Pi Pico2 with RP2350", "1.29.0", 4))
    monkeypatch.setattr(dialog, "ask_yes_no", lambda *a: True)
    errors = []
    monkeypatch.setattr(dialog, "error", lambda parent, title, text: errors.append(text))
    app = App(settings=Settings.load(str(tmp_path / "cfg.json")))
    dlgs = []

    def make(image=None, cls=dialog.FirmwareDialog):
        d = cls(app)
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
    monkeypatch.setattr(app, "connect_device", lambda pane: connected.append(pane.source()))
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
    assert d.product() is None and not d.install_btn.get_sensitive() and "Choose your exact board" in d.note.get_text()


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
    assert d.board_box.get_active_text() == "ESP32-S3 at /dev/ttyACM0"          # not read yet
    assert "pipx install esptool" in d.note.get_text() and not d.install_btn.get_sensitive()
    monkeypatch.setattr(fw, "find_esptool", lambda: ["esptool"])
    d._picked()
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
    assert "does not match a badge" in d.note.get_text() and "16 MB flash, 8 MB PSRAM" in d.note.get_text()
    assert not d.install_btn.get_sensitive()
    d.product_box.set_active(0)
    wait_until(lambda: d.build())
    assert d.install_btn.get_sensitive()


def tildagon_chip_with_both_boards(monkeypatch, drives):
    rom_with(monkeypatch, drives, lambda port: fw.EspChip("ESP32-S3-PICO-1", 8, 2))
    monkeypatch.setattr(FakeCatalog, "BOARDS", [Board("TILDAGON", "Tildagon", "EMF Camp"),
                                                Board("WAVESHARE", "ESP32-S3-Pico", "Waveshare")])


@pytest.mark.parametrize("product, expected", [(0, "TILDAGON"), (1, "")])
def test_the_chosen_board_decides_whether_a_tildagon_is_waited_for(setup, monkeypatch, product, expected):
    make, drives, _, _, tmp = setup
    tildagon_chip_with_both_boards(monkeypatch, drives)
    got = {}

    def fake(image, drive, on_progress, on_stage, cancelled, expect_badge=None, erase=False):
        got.update(expect_badge=expect_badge, erase=erase)
        return fw.Installed("", "", "")
    monkeypatch.setattr(fw, "install_and_verify", fake)
    monkeypatch.setattr(fw, "check_image", lambda *a: None)
    d = make()
    site_ready(d, product)
    d.catalog.image = uf2(tmp / "x.uf2")
    d.install()
    wait_until(lambda: not d.running)
    assert got == {"expect_badge": expected, "erase": False}
    assert "cannot follow this board" in d.note.get_text()


def test_erase_is_off_by_default_only_for_esp_and_asks_again(setup, monkeypatch):
    make, drives, _, _, tmp = setup
    d = make()
    assert d.erase.get_sensitive() and not d.erase.get_active()     # a UF2 drive can too
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


def test_a_2022_badge_is_offered_its_own_firmware_not_the_tildagons(setup, monkeypatch):
    make, drives, *_ = setup
    rom_with(monkeypatch, drives, lambda port: fw.EspChip("ESP32-S3", 8, None))
    monkeypatch.setattr(FakeCatalog, "BOARDS", [Board("TILDAGON", "Tildagon", "EMF Camp"),
                                                Board("TIDAL", "TiDAL", "EMF Camp"),
                                                Board("ESP32_GENERIC_S3", "ESP32-S3", "Espressif")])
    d = make()
    wait_until(lambda: d.boards)
    assert [b.id for b in d.boards] == ["TIDAL", "ESP32_GENERIC_S3"] and d.product() is None


RUNNING = fw.BootDrive("/dev/ttyACM1", "MicroPython", "MicroPython", None, "repl")


def test_a_running_pico_is_named_described_and_can_only_be_backed_up(setup):
    make, drives, *_ = setup
    drives[0] = RUNNING
    d = make(cls=dialog.BackupDialog)
    assert d.board_box.get_active_text() == "MicroPython board at /dev/ttyACM1" and not d.backup_btn.get_sensitive()
    wait_until(lambda: d.backup_btn.get_sensitive())
    assert d.board_box.get_active_text() == "Pico 2 (RP2350) at /dev/ttyACM1"
    assert "Raspberry Pi Pico2 with RP2350" in d.details.get_text() and "4 MB" in d.details.get_text()
    assert not d.restore_btn.get_sensitive() and "BOOTSEL" in d.restore_btn.get_tooltip_text()


def test_a_running_pico_is_read_once_because_that_restarts_its_program(setup, monkeypatch):
    make, drives, *_ = setup
    drives[0] = RUNNING
    reads = []
    monkeypatch.setattr(fw, "read_running", lambda port: reads.append(port) or fw.RunningInfo("Pico", "1", 2))
    d = make(cls=dialog.BackupDialog)
    wait_until(lambda: d.backup_btn.get_sensitive())
    d._picked()
    d.scan()
    pump(50)
    assert reads == ["/dev/ttyACM1"]


def test_a_board_that_cannot_be_read_says_why(setup, monkeypatch):
    make, drives, *_ = setup
    drives[0] = RUNNING

    def busy(port):
        raise fw.FirmwareError("Cannot read /dev/ttyACM1: is it connected in the app?")
    monkeypatch.setattr(fw, "read_running", busy)
    d = make(cls=dialog.BackupDialog)
    wait_until(lambda: "connected in the app" in d.details.get_text())
    assert d.board_box.get_active_text() == "MicroPython board at /dev/ttyACM1"


def test_a_badge_is_named_by_its_chip(setup, monkeypatch):
    make, drives, *_ = setup
    rom_with(monkeypatch, drives, lambda port: fw.EspChip("ESP32-S3", 8, None))
    d = make(cls=dialog.BackupDialog)
    wait_until(lambda: d.drive().esp_chip)
    assert d.board_box.get_active_text() == "TiDAL (2022 badge) at /dev/ttyACM0"
    text = d.details.get_text()
    assert "ESP32-S3" in text and "8 MB" in text and "PSRAM: none" in text


def test_a_bootsel_drive_shows_what_it_says_and_that_it_only_knows_the_chip(setup):
    make, *_ = setup
    d = make(cls=dialog.EraseDialog)
    assert "Board ID: RPI-RP2" in d.details.get_text() and "only the chip" in d.details.get_text()


def test_nothing_found_says_so_in_each_tool(setup):
    make, drives, *_ = setup
    drives.clear()
    for cls in (dialog.BackupDialog, dialog.EraseDialog):
        d = make(cls=cls)
        assert d.details.get_text() == dialog.NO_DEVICE


def test_a_uf2_drive_can_be_restored_and_erased_but_not_backed_up(setup):
    make, *_ = setup
    d = make(cls=dialog.BackupDialog)
    assert d.restore_btn.get_sensitive() and not d.backup_btn.get_sensitive()
    assert "start it normally" in d.backup_btn.get_tooltip_text()
    assert make(cls=dialog.EraseDialog).erase_btn.get_sensitive()


def test_a_running_board_cannot_be_erased(setup):
    make, drives, *_ = setup
    drives[0] = RUNNING
    d = make(cls=dialog.EraseDialog)
    wait_until(lambda: "BOOTSEL" in d.note.get_text() and d.drive().path in d.running_info)
    assert not d.erase_btn.get_sensitive()


def test_tools_wait_for_the_esp_chip_to_be_read(setup, monkeypatch):
    make, drives, *_ = setup

    def fails(port):
        raise fw.FirmwareError("Cannot read the chip")
    rom_with(monkeypatch, drives, fails)
    d = make(cls=dialog.BackupDialog)
    wait_until(lambda: "Cannot read the chip" in d.note.get_text())
    assert not d.backup_btn.get_sensitive() and not d.restore_btn.get_sensitive()


def test_backing_up_a_running_pico_reports_where_it_went(setup, monkeypatch):
    make, drives, errors, _, tmp = setup
    drives[0] = RUNNING
    out = tmp / "b.uf2"

    def fake(port, path, progress, cancelled):
        out.write_bytes(b"data")
        return fw.Backup(path, 4, "Raspberry Pi Pico with RP2040")
    monkeypatch.setattr(fw, "backup_repl", fake)
    d = make(cls=dialog.BackupDialog)
    wait_until(lambda: d.backup_btn.get_sensitive())
    monkeypatch.setattr(d, "_ask_backup_path", lambda drive: str(out))
    d.backup()
    wait_until(lambda: not d.running)
    assert not errors and str(out) in d.note.get_text() and fw.sha256_of(str(out)) in d.note.get_text()


def test_cancelling_the_file_box_starts_nothing(setup, monkeypatch):
    make, drives, *_ = setup
    drives[0] = RUNNING
    monkeypatch.setattr(fw, "backup_repl", lambda *a: pytest.fail("started"))
    d = make(cls=dialog.BackupDialog)
    monkeypatch.setattr(d, "_ask_backup_path", lambda drive: None)
    d.backup()
    assert not d.running


def test_restoring_checks_the_file_asks_then_writes_and_reports(setup, monkeypatch):
    make, _, errors, _, tmp = setup
    path = uf2(tmp / "backup.uf2")
    calls = []

    def fake(image, drive, on_progress, on_stage, cancelled):
        calls.append(image)
        return fw.Installed("/dev/ttyACM9", "1.29.0", "Pico")
    monkeypatch.setattr(fw, "install_and_verify", fake)
    d = make(cls=dialog.BackupDialog)
    monkeypatch.setattr(d, "_ask_restore_path", lambda: path)
    d.restore()
    wait_until(lambda: not d.running)
    assert calls == [path] and not errors and "Restored: MicroPython 1.29.0" in d.note.get_text()


def test_a_backup_for_the_wrong_chip_is_never_restored(setup, monkeypatch):
    make, _, errors, _, tmp = setup             # the fixture's stub fails the test if a write starts
    d = make(cls=dialog.BackupDialog)
    monkeypatch.setattr(d, "_ask_restore_path", lambda: uf2(tmp / "pico2.uf2", family=ARM))
    d.restore()
    pump(50)
    assert not d.running and "not for the" in errors[0]


def test_a_badge_backup_on_a_pico_names_the_mismatch(setup, monkeypatch):
    make, _, errors, _, tmp = setup             # the fixture's stub fails the test if a write starts
    path = tmp / "tidal-backup.bin"
    path.write_bytes(bytes([0xE9]) + bytes(1023))
    d = make(cls=dialog.BackupDialog)
    monkeypatch.setattr(d, "_ask_restore_path", lambda: str(path))
    d.restore()
    pump(50)
    assert not d.running and "for an ESP32 board" in errors[0] and "Device list" in errors[0]


def test_declining_a_restore_writes_nothing(setup, monkeypatch):
    make, _, _, _, tmp = setup
    monkeypatch.setattr(dialog, "ask_yes_no", lambda *a: False)
    d = make(cls=dialog.BackupDialog)
    monkeypatch.setattr(d, "_ask_restore_path", lambda: uf2(tmp / "backup.uf2"))
    d.restore()
    assert not d.running


@pytest.mark.parametrize("answers", [[False], [True, False]])
def test_erase_asks_twice_and_either_no_stops_it(setup, monkeypatch, answers):
    make, *_ = setup
    replies = iter(answers)
    monkeypatch.setattr(dialog, "ask_yes_no", lambda *a: next(replies))
    monkeypatch.setattr(fw, "erase_uf2", lambda *a: pytest.fail("erased"))
    d = make(cls=dialog.EraseDialog)
    d.erase_flash()
    assert not d.running


def erase_tool_at(monkeypatch, tmp):
    tool = tmp / "nuke.uf2"
    tool.write_bytes(b"tool")
    monkeypatch.setattr(FakeCatalog, "erase_tool", lambda self, p, c: str(tool), raising=False)
    return str(tool)


def test_erasing_a_uf2_board_gets_the_tool_checks_it_and_says_it_is_empty(setup, monkeypatch):
    make, _, errors, _, tmp = setup
    tool = erase_tool_at(monkeypatch, tmp)
    ran = []
    monkeypatch.setattr(fw, "check_nuke_uf2", lambda path: ran.append("checked"))
    monkeypatch.setattr(fw, "erase_uf2", lambda image, drive, p, c: ran.append(image))
    d = make(cls=dialog.EraseDialog)
    d.erase_flash()
    wait_until(lambda: not d.running)
    assert ran == ["checked", tool] and not errors and "no firmware now" in d.note.get_text()


def test_erasing_a_badge_uses_esptool(setup, monkeypatch):
    make, drives, errors, *_ = setup
    rom_with(monkeypatch, drives, lambda port: fw.EspChip("ESP32-S3-PICO-1", 8, 2))
    ran = []
    monkeypatch.setattr(fw, "erase_esptool", lambda drive, s, c: ran.append(drive.path))
    d = make(cls=dialog.EraseDialog)
    wait_until(lambda: d.erase_btn.get_sensitive())
    d.erase_flash()
    wait_until(lambda: not d.running)
    assert ran == ["/dev/ttyACM0"] and not errors


def test_a_bad_erase_tool_is_dropped_and_nothing_is_erased(setup, monkeypatch):
    make, _, errors, _, tmp = setup
    erase_tool_at(monkeypatch, tmp)
    monkeypatch.setattr(fw, "erase_uf2", lambda *a: pytest.fail("erased"))
    d = make(cls=dialog.EraseDialog)
    d.erase_flash()
    wait_until(lambda: not d.running)
    assert len(errors) == 1 and d.catalog.forgotten[-1].filename == "flash_nuke.uf2"


def test_the_firmware_tool_can_erase_a_pico_first_then_installs_on_the_drive_that_comes_back(setup, monkeypatch):
    make, _, errors, _, tmp = setup
    erase_tool_at(monkeypatch, tmp)
    monkeypatch.setattr(fw, "check_nuke_uf2", lambda path: None)
    back = fw.BootDrive("/media/pi/NEW", "m", "RPI-RP2", "rp2040")
    monkeypatch.setattr(fw, "erase_uf2", lambda image, drive, p, c: back)
    got = []

    def fake(image, drive, on_progress, on_stage, cancelled):
        got.append(drive)
        return fw.Installed("/dev/ttyACM9", "1.29.0", "Pico")
    monkeypatch.setattr(fw, "install_and_verify", fake)
    d = make(uf2(tmp / "good.uf2"))
    d.erase.set_active(True)
    d.install()
    wait_until(lambda: not d.running)
    assert got == [back] and not errors


def test_the_tools_are_in_the_menu(setup):
    _, _, _, app, _ = setup
    menu = [c for c in app.get_child().get_children() if isinstance(c, Gtk.MenuBar)][0]
    tools = [i for i in menu.get_children() if i.get_label() == "Tools"][0].get_submenu()
    assert [i.get_label() for i in tools.get_children()] == [
        "Sync folders...", "Install firmware...", "Install libraries...",
        "Back up or restore flash...", "Erase flash..."]


@pytest.mark.parametrize("cls", [dialog.FirmwareDialog, dialog.BackupDialog, dialog.EraseDialog])
def test_the_bootloader_button_is_only_for_a_running_board(setup, cls):
    make, drives, *_ = setup
    d = make(cls=cls)
    assert not d.boot_btn.get_visible()             # already a drive
    drives[0] = RUNNING
    d.scan()
    wait_until(lambda: d.boot_btn.get_sensitive() and d.boot_btn.get_visible())


def test_the_bootloader_button_moves_the_tool_to_the_new_drive(setup, monkeypatch):
    make, drives, errors, *_ = setup
    drive = drives[0]
    drives[0] = RUNNING
    d = make(cls=dialog.EraseDialog)
    wait_until(lambda: d.boot_btn.get_sensitive())

    def fake(port, find=None, normal_repl=False):
        drives[0] = drive                           # the port goes, and the drive arrives
        return drive
    monkeypatch.setattr(fw, "enter_bootloader", fake)
    d._response(d, Gtk.ResponseType.YES)
    assert d.running and not d.boot_btn.get_sensitive()
    wait_until(lambda: not d.running)
    assert not errors and d.drive() == drive and not d.boot_btn.get_visible()
    assert drive.path in d.note.get_text() and d.erase_btn.get_sensitive()


def test_a_failed_bootloader_request_is_reported_and_the_board_stays_selected(setup, monkeypatch):
    make, drives, errors, *_ = setup
    drives[0] = RUNNING

    def fails(port, find=None, normal_repl=False):
        raise fw.FirmwareError("The board did not appear as a drive.")
    monkeypatch.setattr(fw, "enter_bootloader", fails)
    d = make(cls=dialog.BackupDialog)
    wait_until(lambda: d.boot_btn.get_sensitive())
    d.put_in_bootloader()
    wait_until(lambda: not d.running)
    assert errors == ["The board did not appear as a drive."] and d.drive().path == RUNNING.path


TILDAGON_RUNNING = fw.BootDrive("/dev/ttyACM0", "Tildagon", "TILDAGON", None, "badge")
TIDAL_RUNNING = fw.BootDrive("/dev/ttyACM2", "TiDAL", "TIDAL", None, "badge")


@pytest.mark.parametrize("cls", [dialog.FirmwareDialog, dialog.BackupDialog, dialog.EraseDialog])
def test_a_running_badge_is_listed_in_every_tool_says_what_to_do_and_is_not_touched(setup, monkeypatch, cls):
    make, drives, *_ = setup
    drives[0] = TILDAGON_RUNNING
    monkeypatch.setattr(fw, "read_running", lambda port: pytest.fail("read a badge"))
    monkeypatch.setattr(fw, "read_esp_chip", lambda port: pytest.fail("read a badge"))
    d = make(cls=cls)
    assert d.board_box.get_active_text() == "Tildagon (2024, 2026) at /dev/ttyACM0"
    assert "running normally" in d.details.get_text() and "Start bootloader" in d.note.get_text()
    assert d.boot_btn.get_visible() and d.boot_btn.get_sensitive()
    for name in ("install_btn", "backup_btn", "restore_btn", "erase_btn"):
        if hasattr(d, name):
            assert not getattr(d, name).get_sensitive(), name


def test_the_2022_badge_gets_its_buttons_not_the_bootloader_button(setup):
    make, drives, *_ = setup
    drives[0] = TIDAL_RUNNING
    d = make()
    assert d.board_box.get_active_text() == "TiDAL (2022 badge) at /dev/ttyACM2"
    assert not d.boot_btn.get_visible() and "bottom right" in d.note.get_text()
    assert "boop" not in d.note.get_text()          # only this device's steps


def test_the_steps_change_with_the_device_chosen(setup):
    make, drives, *_ = setup
    drives[:] = [TILDAGON_RUNNING, TIDAL_RUNNING]
    d = make(cls=dialog.BackupDialog)
    assert "If that does not work" in d.note.get_text() and "hold boop" in d.note.get_text()
    d.board_box.set_active(1)
    assert "bottom right" in d.note.get_text() and "boop" not in d.note.get_text()


def test_with_nothing_found_all_the_steps_are_shown(setup):
    make, drives, *_ = setup
    drives.clear()
    text = make(cls=dialog.EraseDialog).details.get_text()
    assert "BOOTSEL" in text and "boop" in text and "bottom right" in text and "BOOT button" in text


def test_a_running_badge_is_sent_to_its_rom_and_the_tool_moves_to_it(setup, monkeypatch):
    make, drives, errors, *_ = setup
    drives[0] = TILDAGON_RUNNING
    rom = fw.BootDrive("/dev/ttyACM1", "ROM", "ESP32-S3", "esp32s3", "esptool")
    monkeypatch.setattr(fw, "find_esptool", lambda: ["esptool"])
    monkeypatch.setattr(fw, "read_esp_chip", lambda port: fw.EspChip("ESP32-S3-PICO-1", 8, 2))
    seen = {}

    def fake(port, find=None, normal_repl=False):
        seen.update(port=port, find=find, normal_repl=normal_repl)
        drives[0] = rom
        return rom
    monkeypatch.setattr(fw, "enter_bootloader", fake)
    d = make(cls=dialog.EraseDialog)
    d.put_in_bootloader()
    wait_until(lambda: not d.running and d.drive() and d.drive().esp_chip)
    assert seen == {"port": "/dev/ttyACM0", "find": fw.detect_rom, "normal_repl": True} and not errors
    assert d.board_box.get_active_text() == "Tildagon (2024, 2026) at /dev/ttyACM1"


def fake_conn(app, pane, port):
    from mpexplorer.connection import Connection
    conn = Connection(port, "chip:" + port, types.SimpleNamespace(close=lambda reset=False: None, key="device",
                                                                  root="/", repl=None), None, label=port + "  Pico")
    app.conns[pane] = conn
    return conn


def test_a_job_closes_only_the_board_it_uses(setup, monkeypatch):
    make, drives, _, app, tmp = setup
    drives[0] = RUNNING
    left = fake_conn(app, app.left, "/dev/ttyACM9")
    fake_conn(app, app.right, RUNNING.path)
    closed = []
    monkeypatch.setattr(app, "disconnect", lambda pane, keep_copied=True: closed.append(pane) or app.conns.pop(pane))
    monkeypatch.setattr(fw, "backup_repl", lambda *a: fw.Backup("p", 1, "s"))
    monkeypatch.setattr(fw, "sha256_of", lambda p: "abc")
    d = make(cls=dialog.BackupDialog)
    monkeypatch.setattr(d, "_ask_backup_path", lambda drive: "p")
    d.backup()
    wait_until(lambda: not d.running)
    assert closed == [app.right] and app.conns == {app.left: left}
    app.conns.clear()


def test_a_board_connected_in_the_app_is_not_opened_again_to_read_it(setup, monkeypatch):
    make, drives, _, app, _ = setup
    drives[0] = RUNNING
    fake_conn(app, app.right, RUNNING.path)
    monkeypatch.setattr(fw, "read_running", lambda port: pytest.fail("opened it twice"))
    d = make(cls=dialog.BackupDialog)
    pump(50)
    assert "connected in the app" in d.details.get_text() and d.backup_btn.get_sensitive()
    app.conns.clear()


def test_a_job_on_a_bootloader_closes_nothing(setup, monkeypatch):
    make, _, _, app, _ = setup
    left = fake_conn(app, app.left, "/dev/ttyACM9")
    monkeypatch.setattr(app, "disconnect", lambda *a, **k: pytest.fail("closed a board"))
    monkeypatch.setattr(fw, "check_nuke_uf2", lambda p: None)
    monkeypatch.setattr(FakeCatalog, "erase_tool", lambda self, p, c: "x", raising=False)
    monkeypatch.setattr(fw, "erase_uf2", lambda *a: None)
    d = make(cls=dialog.EraseDialog)
    d.erase_flash()
    wait_until(lambda: not d.running)
    assert app.conns[app.left] is left
    app.conns.clear()


def test_connect_after_an_install_uses_a_free_pane(setup, monkeypatch):
    make, _, _, app, tmp = setup
    fake_conn(app, app.right, "/dev/ttyACM9")
    used = []
    monkeypatch.setattr(app, "connect_device", lambda pane: used.append(pane))
    d = make()
    d.result = fw.Installed("/dev/ttyACM3", "1", "Pico")
    d._connect_new_port()
    assert used == [app.left] and app.left.source() == "/dev/ttyACM3"
    app.conns.clear()
