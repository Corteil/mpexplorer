import os

import pytest

from fakedevice import FakeSerial
from mpexplorer import ports
from mpexplorer.backends import DeviceBackend
from mpexplorer.remember import device_start, host_start, port_choice, remember_folder, remember_port
from mpexplorer.settings import Settings
from mpexplorer.transport import RawREPL


@pytest.fixture
def s():
    return Settings()


def test_host_start(s, tmp_path):
    assert host_start(s, "/fallback") == "/fallback"
    s.set("folders.host", str(tmp_path))
    assert host_start(s, "/fallback") == str(tmp_path)
    s.set("folders.host", str(tmp_path / "gone"))
    assert host_start(s, "/fallback") == "/fallback"                   # a folder that vanished is not used
    s.set("folders.host", str(tmp_path))
    s.set("general.remember_folders", False)
    assert host_start(s, "/fallback") == "/fallback"


def test_remember_folder_respects_the_switch(s):
    remember_folder(s, False, "/home/me/work")
    remember_folder(s, True, "/lib", board="1234:5678:ABC")
    remember_folder(s, True, "/x", board=None)                         # no board id: nothing to key it by
    assert s.get("folders.host") == "/home/me/work" and s.get("folders.boards") == {"1234:5678:ABC": "/lib"}
    s.set("general.remember_folders", False)
    remember_folder(s, False, "/elsewhere")
    assert s.get("folders.host") == "/home/me/work"


def test_device_start_uses_the_board_and_falls_back_to_root(s, tmp_path):
    dev = DeviceBackend(RawREPL(FakeSerial()))
    (tmp_path / "lib").mkdir()
    remember_folder(s, True, str(tmp_path / "lib"), board="a.b.c")     # dots in a serial number are made key-safe
    assert device_start(s, dev, "a.b.c") == str(tmp_path / "lib")
    assert device_start(s, dev, "other-board") == dev.root
    remember_folder(s, True, str(tmp_path / "removed"), board="a.b.c")
    assert device_start(s, dev, "a.b.c") == dev.root                   # folder no longer on the board
    (tmp_path / "file").write_text("x")
    remember_folder(s, True, str(tmp_path / "file"), board="a.b.c")
    assert device_start(s, dev, "a.b.c") == dev.root                   # a file is not a folder


def test_port_choice(s):
    entries = ["/dev/ttyACM0  Pico", "/dev/ttyACM1  TiLDAGON - Board CDC"]
    assert port_choice(s, entries) == 0 and port_choice(s, []) is None
    remember_port(s, "/dev/ttyACM1")
    assert port_choice(s, entries) == 1
    assert port_choice(s, ["/dev/ttyUSB0  Other"]) == 0                # remembered port not plugged in
    s.set("general.remember_port", False)
    assert port_choice(s, entries) == 0
    remember_port(s, "/dev/ttyACM0")
    assert s.get("general.last_port") == "/dev/ttyACM1"                # switched off: nothing recorded


def test_device_of_and_board_id(monkeypatch):
    assert ports.device_of("/dev/ttyACM0  TiLDAGON - Board CDC") == "/dev/ttyACM0"

    class P:
        device, vid, pid, serial_number = "/dev/ttyACM0", 0x303A, 0x4001, "A1B2"
    monkeypatch.setattr("serial.tools.list_ports.comports", lambda: [P()])
    assert ports.board_id("/dev/ttyACM0") == "303a:4001:A1B2"
    assert ports.board_id("/dev/ttyACM9") == "/dev/ttyACM9"            # unknown device: the path itself


def test_reset_all_notifies_and_keeps_unknown_keys(tmp_path):
    path = str(tmp_path / "s.json")
    s = Settings.load(path)
    s.set("window.width", 900)
    s.set("general.show_hidden", True)
    s._data["future"] = {"keep": 1}
    seen = []
    s.subscribe(lambda k, v: seen.append((k, v)))
    assert sorted(s.reset_all()) == ["general.show_hidden", "window.width"]
    assert sorted(seen) == [("general.show_hidden", False), ("window.width", 1180)]
    assert s.get("window.width") == 1180 and s.save()
    assert os.path.exists(path) and Settings.load(path)._data["future"] == {"keep": 1}
