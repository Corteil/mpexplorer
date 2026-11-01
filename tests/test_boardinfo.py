from types import SimpleNamespace as P

import pytest

from mpexplorer import boardinfo as bi
from mpexplorer import ports
from mpexplorer.settings import Settings

# What the OS reported for the bench on 2026-11-01 (see docs/BOARD_LABELS_PLAN.md).
BENCH = [
    P(device="/dev/ttyACM2", vid=0x2E8A, pid=0x0005, manufacturer="MicroPython", product="Board in FS mode",
      serial_number="15edd94f06289842", description="Board in FS mode"),
    P(device="/dev/ttyACM5", vid=0x2E8A, pid=0x0005, manufacturer="MicroPython", product="Board in FS mode",
      serial_number="000000000000", description="Board in FS mode"),
    P(device="/dev/ttyACM4", vid=0x2E8A, pid=0x000B, manufacturer="Raspberry Pi", product="Pico 2",
      serial_number="559CBFCF6BBE8A64", description="Pico 2"),
    P(device="/dev/ttyACM3", vid=0x239A, pid=0x8120, manufacturer="Raspberry Pi", product="Pico W",
      serial_number="E6614104034E1931", description="Pico W"),
    P(device="/dev/ttyACM0", vid=0x16D0, pid=0x120E, manufacturer="Electromagnetic Field", product="TiLDAGON",
      serial_number="64e83372252c0000", description="TiLDAGON"),
    P(device="/dev/ttyAMA10", vid=None, pid=None, manufacturer=None, product=None, serial_number=None,
      description="n/a"),
]


@pytest.fixture
def settings():
    return Settings()


def test_the_usb_guess_separates_the_families_on_the_bench():
    kinds = {p.device: bi.guess_kind(p.vid, p.pid, p.manufacturer) for p in BENCH[:5]}
    assert kinds == {"/dev/ttyACM2": bi.MICROPYTHON, "/dev/ttyACM5": bi.MICROPYTHON, "/dev/ttyACM4": bi.CIRCUITPYTHON,
                     "/dev/ttyACM3": bi.CIRCUITPYTHON, "/dev/ttyACM0": bi.MICROPYTHON}
    assert bi.guess_kind(0x0403, 0x6001, "FTDI") is None             # a USB-serial adapter is not a board we know


def test_what_was_learned_beats_the_guess_and_can_change(settings):
    key = bi.usb_key(0x2E8A, 0x000B, "559CBFCF6BBE8A64")
    assert bi.firmware_kind(settings, key, 0x2E8A, 0x000B, "Raspberry Pi") == bi.CIRCUITPYTHON      # guessed
    bi.remember_firmware(settings, key, bi.MICROPYTHON)                                  # the owner reflashed it
    assert bi.firmware_kind(settings, key, 0x2E8A, 0x000B, "Raspberry Pi") == bi.MICROPYTHON
    bi.remember_firmware(settings, key, "nonsense")                                       # ignored
    assert settings.get("boards.firmware") == {"2e8a:000b:559CBFCF6BBE8A64": "micropython"}


def test_usb_serials_that_name_no_single_board():
    assert [bi.unique_usb_serial(s) for s in ("15edd94f06289842", "123456", "000000000000", "", None)] == [
        True, False, False, False, False]


def test_nicknames_are_cleaned_set_cleared_and_checked_for_duplicates(settings):
    assert bi.clean_nickname("  Bench\tCP \x07 board  ") == "Bench CP board"
    assert bi.clean_nickname("x" * 40) == "x" * bi.MAX_NICKNAME and bi.clean_nickname("\x00\x1f ") == ""
    assert bi.set_nickname(settings, "chip:e661", "Bench CP") == "Bench CP"
    assert bi.nickname_of(settings, "chip:e661") == "Bench CP"
    assert bi.other_boards_named(settings, "bench cp", "chip:aaaa") == ["chip:e661"]       # case is ignored
    assert bi.other_boards_named(settings, "bench cp", "chip:e661") == []                   # itself does not count
    assert bi.set_nickname(settings, "chip:e661", "  ") == "" and bi.nickname_of(settings, "chip:e661") == ""


def test_a_nickname_set_on_the_chip_shows_before_connecting_only_when_the_usb_id_is_unique(settings):
    usb, chip = bi.usb_key(0x2E8A, 0x000B, "559CBFCF6BBE8A64"), "chip:559cbfcf"
    bi.remember_chip(settings, usb, chip)
    bi.set_nickname(settings, chip, "Bench CP")
    assert bi.nickname_before_connect(settings, usb) == "Bench CP"
    shared = bi.usb_key(0x2E8A, 0x0005, "000000000000")
    bi.remember_chip(settings, shared, "chip:1111")                  # not recorded: the USB key does not name a board
    bi.set_nickname(settings, shared, "Wrong board")                 # even if someone named the USB key itself
    assert settings.get("boards.ids") == {"2e8a:000b:559CBFCF6BBE8A64": "chip:559cbfcf"}
    assert bi.nickname_before_connect(settings, shared) == ""
    assert bi.quoted("Bench CP") == '"Bench CP"' and bi.quoted("") == ""


def test_the_port_list_carries_kind_and_nickname_and_keeps_the_port_findable(settings, monkeypatch):
    import serial.tools.list_ports as lp
    monkeypatch.setattr(lp, "comports", lambda: list(BENCH))
    usb, chip = bi.usb_key(0x2E8A, 0x000B, "559CBFCF6BBE8A64"), "chip:559cbfcf"
    bi.remember_chip(settings, usb, chip)
    bi.set_nickname(settings, chip, "Bench CP")
    entries = ports.list_boards(settings)
    # Raspberry Pi vendor id first (by port), then the rest by port; the UART without a USB id is left out
    devices = [e.device for e in entries]
    assert devices == ["/dev/ttyACM2", "/dev/ttyACM4", "/dev/ttyACM5", "/dev/ttyACM0", "/dev/ttyACM3"]
    by_dev = {e.device: e for e in entries}
    assert by_dev["/dev/ttyACM4"].kind == bi.CIRCUITPYTHON and by_dev["/dev/ttyACM4"].nickname == "Bench CP"
    assert by_dev["/dev/ttyACM4"].text == '/dev/ttyACM4  "Bench CP"  Pico 2  #8a64'
    assert by_dev["/dev/ttyACM2"].text == "/dev/ttyACM2  Pico / Pico 2  #9842"
    assert by_dev["/dev/ttyACM2"].nickname == ""
    assert by_dev["/dev/ttyACM5"].kind == bi.MICROPYTHON
    assert all(ports.device_of(e.text) == e.device for e in entries)             # the port is still the first field


def test_nothing_changes_for_the_old_string_list(monkeypatch):
    import serial.tools.list_ports as lp
    monkeypatch.setattr(lp, "comports", lambda: list(BENCH))
    assert "/dev/ttyACM4  Pico 2  #8a64" in ports.list_ports()
