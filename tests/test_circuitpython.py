import os

import pytest

from mpexplorer import circuitpython as cp
from mpexplorer.circuitpython import DriveBackend, DriveNotFound

MOUNTS = ("/dev/nvme0n1p2 / ext4 rw 0 0\n"
          "/dev/sda1 /media/pi/CIRCUITPY vfat rw,nosuid 0 0\n"
          "/dev/sdb1 /media/pi/CIRCUITPY1 vfat rw 0 0\n"
          "/dev/sdc1 /media/pi/RP2350 vfat rw 0 0\n"
          "/dev/sdd1 /media/pi/CIRCUITPY\\040X vfat rw 0 0\n")
BOOT = ("Adafruit CircuitPython 10.3.1 on 2026-09-14; Raspberry Pi Pico 2 with rp2350a\n"
        "Board ID:raspberry_pi_pico2\nUID:559CBFCF6BBE8A64\n")


class FakeRepl:
    def __init__(self, reply=b""):
        self.reply, self.sent, self.resets = reply, [], 0

    def exec(self, code, timeout=10):
        self.sent.append(code)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply

    def soft_reset(self):
        self.resets += 1

    def close(self, reset=False):
        pass


def drive(tmp_path, uid="559CBFCF6BBE8A64", name="CIRCUITPY"):
    d = tmp_path / name
    d.mkdir()
    (d / "boot_out.txt").write_text(BOOT.replace("559CBFCF6BBE8A64", uid))
    return str(d)


def test_mounts_finds_only_circuitpy_drives():
    assert cp.mounts(MOUNTS) == ["/media/pi/CIRCUITPY", "/media/pi/CIRCUITPY1"]


def test_boot_out_is_read(tmp_path):
    assert cp.read_boot_out(drive(tmp_path)) == {"version": "10.3.1", "board": "Raspberry Pi Pico 2 with rp2350a",
                                                 "board_id": "raspberry_pi_pico2", "uid": "559CBFCF6BBE8A64"}
    assert cp.read_boot_out(str(tmp_path / "none")) == {}


def test_find_drive_matches_the_boards_serial_number(tmp_path):
    a, b = drive(tmp_path, "AAAA1111", "one"), drive(tmp_path, "BBBB2222", "two")
    assert cp.find_drive("bbbb2222", [a, b]) == b
    with pytest.raises(DriveNotFound, match="not mounted"):
        cp.find_drive("CCCC3333", [a, b])
    with pytest.raises(DriveNotFound):
        cp.find_drive(None, [a, b])                  # two drives and no serial: do not guess
    assert cp.find_drive(None, [a]) == a


def test_detect_names_the_firmware():
    assert cp.detect(FakeRepl(b"circuitpython\n10.3.1\n")) == ("circuitpython", "10.3.1")
    assert cp.detect(FakeRepl(b"micropython\n1.28.0\n")) == ("micropython", "1.28.0")
    assert cp.detect(FakeRepl(OSError("gone"))) == (None, "")


def test_drive_backend_stays_inside_the_drive_and_maps_paths(tmp_path):
    mount = drive(tmp_path)
    repl = FakeRepl(b"hello\n")
    be = DriveBackend(mount, repl)
    assert be.root == mount and be.key == "device"
    assert be.parent(mount) == mount and be.parent(mount + "/lib") == mount
    assert be.board_path(mount) == "/" and be.board_path(mount + "/lib/x.py") == "/lib/x.py"
    assert be.run(mount + "/code.py") == "hello\n" and "'/code.py'" in repl.sent[-1]
    be.soft_reset()
    assert repl.resets == 1


def test_drive_backend_writes_and_copies(tmp_path):
    be = DriveBackend(drive(tmp_path), FakeRepl())
    target = os.path.join(be.root, "a.txt")
    with be.open_write(target) as f:
        f.write(b"abc")
    copied = []
    be.copy_file(target, os.path.join(be.root, "b.txt"), copied.append)
    assert open(os.path.join(be.root, "b.txt"), "rb").read() == b"abc" and copied == [3]


def test_clock_is_read_and_set_through_the_rtc_module(tmp_path):
    repl = FakeRepl(b"2026 11 1 12 30 45\n")
    be = DriveBackend(drive(tmp_path), repl)
    assert be.clock() == (2026, 11, 1, 12, 30, 45)
    assert be.set_clock(1793536245) == (2026, 11, 1, 12, 30, 45)                # 2026-11-01 12:30:45 UTC
    sent = [c for c in repl.sent if "rtc" in c][0]
    assert "rtc.RTC().datetime=time.struct_time((2026,11,1,12,30,45,6,-1,-1))" in sent     # a Sunday: tm_wday 6


def test_a_build_without_rtc_says_so(tmp_path):
    from mpexplorer.errors import BackendError
    from mpexplorer.transport import DeviceError
    repl = FakeRepl(DeviceError("Traceback (most recent call last):\nImportError: no module named 'rtc'\n"))
    with pytest.raises(BackendError, match="no rtc module"):
        DriveBackend(drive(tmp_path), repl).set_clock()
