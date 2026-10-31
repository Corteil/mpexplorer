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


@pytest.fixture
def setup(tmp_path, monkeypatch):
    drive_dir = tmp_path / "RPI-RP2"
    drive_dir.mkdir()
    (drive_dir / fw.INFO_FILE).write_text("UF2 Bootloader v3.0\nModel: Raspberry Pi RP2\nBoard-ID: RPI-RP2\n")
    drives = [fw.read_drive(str(drive_dir))]
    monkeypatch.setattr(fw, "detect", lambda: list(drives))
    # the real function must never run here: it would look for and talk to real boards
    monkeypatch.setattr(fw, "install_and_verify", lambda *a, **k: pytest.fail("install not stubbed"))
    monkeypatch.setattr(dialog, "ask_yes_no", lambda *a: True)
    errors = []
    monkeypatch.setattr(dialog, "error", lambda parent, title, text: errors.append(text))
    app = App(settings=Settings.load(str(tmp_path / "cfg.json")))
    dlgs = []

    def make(image=None):
        d = dialog.FirmwareDialog(app)
        dlgs.append(d)
        if image:
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
    assert d.board_box.get_active_text().startswith("RP2040 at ")
    assert "Choose a firmware" in d.note.get_text() and not d.install_btn.get_sensitive()


def test_says_when_no_board_is_there_and_finds_one_plugged_in_later(setup):
    make, drives, *_ = setup
    drive = drives.pop()
    d = make()
    assert "No board" in d.note.get_text()
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
