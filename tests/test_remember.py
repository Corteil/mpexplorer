import os

import pytest

from fakedevice import FakeSerial
from mpexplorer import ports
from mpexplorer.backends import DeviceBackend
from mpexplorer.remember import (device_start, host_start, port_choice, remember_board_rts, remember_folder,
                                 remember_port)
from mpexplorer.settings import Settings
from mpexplorer.transport import RawREPL, TransportError


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


def test_list_ports_labels_boards_and_skips_onboard_uarts(monkeypatch):
    class P:
        def __init__(self, device, vid, pid, serial, product=None, description="n/a"):
            self.device, self.vid, self.pid, self.serial_number = device, vid, pid, serial
            self.product, self.description = product, description

    found = [P("/dev/ttyAMA10", None, None, None),
             P("/dev/ttyACM0", 0x303A, 0x1001, "7AC00000", product="TiLDAGON"),
             P("/dev/ttyACM2", 0x2E8A, 0x0005, "15EDD94F06289842", description="Board in FS mode - Board CDC")]
    monkeypatch.setattr("serial.tools.list_ports.comports", lambda: found)
    assert ports.list_ports() == ["/dev/ttyACM2  Pico / Pico 2  #9842",
                                  "/dev/ttyACM0  TiLDAGON  #0000"]
    assert ports.device_of(ports.list_ports()[0]) == "/dev/ttyACM2"


def test_connecting_teaches_the_port_list_the_exact_model(monkeypatch, tmp_path):
    from mpexplorer.remember import remember_board_name
    from mpexplorer.settings import Settings

    class P:
        device, vid, pid, product, description = "/dev/ttyACM2", 0x2E8A, 0x0005, None, "x"
        serial_number = "15EDD94F06289842"

    monkeypatch.setattr("serial.tools.list_ports.comports", lambda: [P()])
    settings = Settings(str(tmp_path / "s.json"))
    # not connected yet: the generic name
    assert "Pico / Pico 2" in ports.list_ports(settings.get("boards.names"))[0]
    remember_board_name(settings, "2e8a:0005:15EDD94F06289842", " Raspberry Pi Pico2 with RP2350\n")
    named = ports.list_ports(settings.get("boards.names"))
    assert named == ["/dev/ttyACM2  Pico 2 (RP2350)  #9842"]
    remember_board_name(settings, "2e8a:0005:15EDD94F06289842", "")                    # no answer: keep what we knew
    assert "RP2350" in ports.list_ports(settings.get("boards.names"))[0]


def test_short_model():
    assert ports.short_model("Raspberry Pi Pico with RP2040") == "Pico (RP2040)"
    assert ports.short_model("Raspberry Pi Pico2 with RP2350") == "Pico 2 (RP2350)"
    assert ports.short_model("TiLDAGON with ESP32S3") == "TiLDAGON (ESP32S3)"
    assert ports.short_model("Unknown") == "Unknown"


class SilentUnlessRts(FakeSerial):
    """The 2022 badge: it says nothing at all unless RTS is asserted as well as DTR."""

    def __init__(self, rts):
        super().__init__()
        self.rts = rts

    def write(self, data):
        return len(data) if not self.rts else super().write(data)


def open_stub(monkeypatch, needs_rts, tried):
    def fake_open(cls, port, baud=115200, paste=None, adaptive=True, rts=False):
        tried.append(rts)
        return RawREPL(SilentUnlessRts(rts) if needs_rts else FakeSerial())
    monkeypatch.setattr(RawREPL, "open", classmethod(fake_open))
    monkeypatch.setattr("mpexplorer.transport.time.sleep", lambda s: None)


def test_open_board_falls_back_to_rts_for_a_silent_board(monkeypatch):
    tried = []
    open_stub(monkeypatch, True, tried)
    repl, rts = RawREPL.open_board("/dev/x")
    assert rts is True and tried == [False, True] and repl.raw        # low failed, high answered
    tried.clear()
    repl, rts = RawREPL.open_board("/dev/x", rts=True)                # remembered: straight to RTS high
    assert rts is True and tried == [True]


def test_open_board_keeps_rts_low_for_a_normal_board_and_gives_up_on_a_dead_one(monkeypatch):
    tried = []
    open_stub(monkeypatch, False, tried)
    assert RawREPL.open_board("/dev/x")[1] is False and tried == [False]    # the Tildagon path is unchanged
    monkeypatch.setattr(RawREPL, "enter_raw", lambda self: (_ for _ in ()).throw(TransportError("nothing")))
    with pytest.raises(TransportError, match="nothing"):
        RawREPL.open_board("/dev/x")


def test_remember_board_rts(s):
    remember_board_rts(s, "16d0:1114:123.456", True)
    assert s.get("boards.rts") == {"16d0:1114:123_456": True}
    remember_board_rts(s, "16d0:1114:123.456", False)                  # it answers without RTS now
    assert s.get("boards.rts") == {}
    remember_board_rts(s, "", True)
    assert s.get("boards.rts") == {}
