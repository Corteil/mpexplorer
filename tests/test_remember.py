import os
import types

import pytest

from fakedevice import FakeSerial
from mpexplorer import ports
from mpexplorer.backends import DeviceBackend
from mpexplorer.remember import (chip_key, device_start, host_start, move_folder_to_chip, port_choice,
                                 remember_board_rts, remember_folder, remember_port)
from mpexplorer.settings import Settings, segment
from mpexplorer.transport import DeviceError, RawREPL, TransportError


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
             P("/dev/ttyACM0", 0x1234, 0x5678, "7AC00000", product="Gizmo"),        # unknown id: the product string
             P("/dev/ttyACM2", 0x2E8A, 0x0005, "15EDD94F06289842", description="Board in FS mode - Board CDC")]
    monkeypatch.setattr("serial.tools.list_ports.comports", lambda: found)
    assert ports.list_ports() == ["/dev/ttyACM2  Pico / Pico 2  #9842",
                                  "/dev/ttyACM0  Gizmo  #0000"]
    assert ports.device_of(ports.list_ports()[0]) == "/dev/ttyACM2"


class _WinPort:
    """What pyserial gives on Windows: no product string, and "USB Serial Device (COMn)" for every board."""
    def __init__(self, device, vid, pid, serial):
        self.device, self.vid, self.pid, self.serial_number = device, vid, pid, serial
        self.product, self.manufacturer, self.description = None, None, "USB Serial Device (%s)" % device


def test_windows_port_list_names_boards_by_usb_id_not_usb_serial_device(monkeypatch):
    # 2026-11-08: the owner picked the wrong board because every row said "USB Serial Device"
    found = [_WinPort("COM13", 0x16D0, 0x120E, "7C4FAD337AC00000"),     # Tildagon
             _WinPort("COM15", 0x2E8A, 0x0005, "E6614104034E1931"),     # MicroPython Pico W
             _WinPort("COM16", 0x2E8A, 0x000B, "99122E5748243C1E"),     # CircuitPython Pico 2
             _WinPort("COM17", 0x239A, 0x8120, "E6614C311B6F5837"),     # CircuitPython Pico W
             _WinPort("COM20", 0x10C4, 0xEA60, "0001")]                 # a USB-serial adapter: nothing known
    monkeypatch.setattr("serial.tools.list_ports.comports", lambda: found)
    labels = ports.list_ports()
    assert labels == ["COM15  Pico / Pico 2  #1931", "COM16  CircuitPython board  #3c1e",
                      "COM13  Tildagon  #0000", "COM17  CircuitPython board  #5837", "COM20  USB serial device  #0001"]
    assert not any("USB Serial Device" in t for t in labels)
    assert [ports.device_of(t) for t in labels] == ["COM15", "COM16", "COM13", "COM17", "COM20"]


def test_the_esp32s3_bootloader_port_says_no_repl_and_press_reboop(monkeypatch):
    # a hung Tildagon showed up as 303a:1001 on Windows (2026-11-08); a model learned there earlier must not hide it
    rom = _WinPort("COM14", 0x303A, 0x1001, "7C:4F:AD:33:7A:C0")
    monkeypatch.setattr("serial.tools.list_ports.comports", lambda: [rom])
    assert ports.list_ports() == ["COM14  ESP32-S3 bootloader: no REPL, press reboop  #a:c0"]
    learned = {"303a:1001:7C:4F:AD:33:7A:C0": "TiLDAGON with ESP32S3"}
    assert ports.list_ports(learned)[0].startswith("COM14  ESP32-S3 bootloader")
    assert ports.in_bootloader("303a:1001:7C:4F:AD:33:7A:C0") and ports.in_bootloader("303A:1001:x")
    assert not ports.in_bootloader("303a:4001:x") and not ports.in_bootloader("") and not ports.in_bootloader(None)
    assert ports.generic_name("USB Serial Device (COM5)") and ports.generic_name("USB-Serial Device")
    assert not ports.generic_name("Board in FS mode - Board CDC")


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


class ChipRepl:
    def __init__(self, reply=b"f412fa4e4da0\r\n", error=None):
        self.reply, self.error = reply, error

    def exec(self, code, timeout=0):
        assert "unique_id" in code
        if self.error:
            raise self.error
        return self.reply


def test_chip_key_reads_the_boards_own_id():
    assert chip_key(ChipRepl()) == "chip:f412fa4e4da0"


@pytest.mark.parametrize("repl", [ChipRepl(error=DeviceError("ImportError: no module named 'machine'")),
                                  ChipRepl(error=TransportError("silent")), ChipRepl(error=OSError("gone")),
                                  ChipRepl(reply=b"odd reply\r\n"), ChipRepl(reply=b"")])
def test_a_board_that_cannot_give_a_chip_id_falls_back(repl):
    assert chip_key(repl) is None


def chip_settings(tmp_path, folders):
    s = Settings.load(str(tmp_path / "settings.json"))
    s.set("folders.boards", folders)
    return s


PICO = "2e8a:0005:e6614c311b5d8e23"
BADGE = "16d0:1114:123456"


class StartDev:
    root = "/"

    def stat(self, path):
        return types.SimpleNamespace(is_dir=True)


def test_a_folder_saved_under_a_usb_id_is_found_then_moved_to_the_chip(tmp_path):
    s = chip_settings(tmp_path, {segment(PICO): "/lib"})
    assert device_start(s, StartDev(), "chip:aa", PICO) == "/lib"
    move_folder_to_chip(s, PICO, "chip:aa")
    assert s.get("folders.boards") == {segment("chip:aa"): "/lib"}


def test_a_shared_usb_serial_is_never_given_to_either_badge(tmp_path):
    s = chip_settings(tmp_path, {segment(BADGE): "/apps"})
    assert device_start(s, StartDev(), "chip:aa", BADGE) == "/"
    move_folder_to_chip(s, BADGE, "chip:aa")
    assert s.get("folders.boards") == {segment(BADGE): "/apps"}


def test_two_badges_with_one_usb_serial_remember_their_own_folders(tmp_path):
    s = chip_settings(tmp_path, {})
    remember_folder(s, True, "/one", "chip:a")
    remember_folder(s, True, "/two", "chip:b")
    assert device_start(s, StartDev(), "chip:a", BADGE) == "/one"
    assert device_start(s, StartDev(), "chip:b", BADGE) == "/two"


def test_a_chip_entry_wins_over_an_old_usb_one(tmp_path):
    s = chip_settings(tmp_path, {segment(PICO): "/old", segment("chip:aa"): "/new"})
    assert device_start(s, StartDev(), "chip:aa", PICO) == "/new"
    move_folder_to_chip(s, PICO, "chip:aa")
    assert s.get("folders.boards") == {segment("chip:aa"): "/new"}


def test_with_no_chip_id_the_usb_id_goes_on_being_used(tmp_path):
    s = chip_settings(tmp_path, {segment(PICO): "/lib"})
    move_folder_to_chip(s, PICO, PICO)
    assert s.get("folders.boards") == {segment(PICO): "/lib"}
