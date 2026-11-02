"""Backing up and erasing flash, with a fake board and a fake esptool. Never opens a real port."""
import base64
import hashlib
import os
import re
import stat
import struct
import sys

import pytest

from mpexplorer import firmware as fw
from mpexplorer.errors import Cancelled
from mpexplorer.transport import TransportError

ROM = fw.BootDrive("/dev/fake", "m", "ESP32-S3", "esp32s3", "esptool", fw.EspChip("ESP32-S3-PICO-1", 8, 2))


@pytest.fixture(autouse=True)
def private_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


class FlashRepl:
    """Answers the two snippets backup_repl sends, from a bytes 'flash'."""
    def __init__(self, flash, machine="Raspberry Pi Pico with RP2040", size_mb=None, corrupt=False):
        self.flash, self.machine, self.corrupt, self.closed = flash, machine, corrupt, None
        self.size_mb = len(flash) // (1024 * 1024) if size_mb is None else size_mb
        self.reads = 0

    def exec(self, code, timeout=0):
        if "print(n)" in code:
            return ("%d\r\n%s\r\n1.29.0\r\n" % (self.size_mb, self.machine)).encode()
        self.reads += 1
        offset, n = map(int, re.search(r"0x10000000\+(\d+),(\d+)", code).groups())
        piece = self.flash[offset:offset + n]
        sha = hashlib.sha256(b"x" if self.corrupt else piece).hexdigest()
        return (base64.b64encode(piece).decode() + "\r\n" + sha + "\r\n").encode()

    def close(self, reset=False):
        self.closed = reset


def opener(repl):
    return lambda port: (repl, False)


def flash_of(mb=1):
    return bytes(i % 251 for i in range(mb * 1024 * 1024))


def test_a_backup_is_a_uf2_the_install_check_accepts_and_holds_the_whole_flash(tmp_path):
    flash, out = flash_of(), str(tmp_path / "b.uf2")
    repl = FlashRepl(flash)
    seen = []
    got = fw.backup_repl("/dev/x", out, lambda a, b: seen.append((a, b)), open_board=opener(repl))
    assert got.size == len(flash) and "RP2040" in got.source and seen[-1] == (len(flash), len(flash))
    info = fw.check_uf2(out, "rp2040")
    assert info.size == len(flash) and repl.closed is True
    data = open(out, "rb").read()
    back = b"".join(data[i + 32:i + 32 + 256] for i in range(0, len(data), 512))
    assert back == flash


def test_a_pico2_backup_is_for_the_rp2350(tmp_path):
    out = str(tmp_path / "b.uf2")
    fw.backup_repl("/dev/x", out, open_board=opener(FlashRepl(flash_of(), "Raspberry Pi Pico2 with RP2350")))
    assert fw.check_uf2(out, "rp2350").cpu == "Arm"


def test_a_backup_is_private(tmp_path):
    out = str(tmp_path / "b.uf2")
    fw.backup_repl("/dev/x", out, open_board=opener(FlashRepl(flash_of())))
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600


def test_a_read_that_does_not_check_out_leaves_no_file_and_resets(tmp_path):
    out, repl = str(tmp_path / "b.uf2"), FlashRepl(flash_of(), corrupt=True)
    with pytest.raises(fw.FirmwareError, match="did not check out"):
        fw.backup_repl("/dev/x", out, open_board=opener(repl))
    assert not os.path.exists(out) and repl.closed is True


@pytest.mark.parametrize("repl, message", [
    (FlashRepl(b"", size_mb=0), "Cannot work out the flash size"),
    (FlashRepl(flash_of(), "ESP32 thing"), "not supported"),
])
def test_a_board_it_cannot_read_is_refused(tmp_path, repl, message):
    with pytest.raises(fw.FirmwareError, match=message):
        fw.backup_repl("/dev/x", str(tmp_path / "b.uf2"), open_board=opener(repl))
    assert repl.closed is True


def test_a_backup_can_be_cancelled(tmp_path):
    out, repl = str(tmp_path / "b.uf2"), FlashRepl(flash_of())
    with pytest.raises(Cancelled):
        fw.backup_repl("/dev/x", out, cancelled=lambda: repl.reads >= 2, open_board=opener(repl))
    assert not os.path.exists(out) and repl.closed is True


def test_a_silent_board_is_reported(tmp_path):
    def silent(port):
        raise TransportError("nothing")
    with pytest.raises(fw.FirmwareError, match="did not answer"):
        fw.backup_repl("/dev/x", str(tmp_path / "b.uf2"), open_board=silent)


def fake_tool(tmp_path, body):
    script = tmp_path / "fake_esptool.py"
    script.write_text("import sys\nargs = sys.argv[1:]\n" + body)
    return [sys.executable, str(script)]


def test_an_esp_backup_reads_the_whole_flash_stays_in_the_rom_and_is_private(tmp_path):
    log = tmp_path / "args"
    tool = fake_tool(tmp_path, "open(%r, 'w').write(' '.join(args))\n"
                               "print('Reading from 0x00001000 [] 50.0%% 1/2')\n"
                               "open(args[-1], 'r+b').write(b'x' * 8388608)\n" % str(log))
    out, seen = str(tmp_path / "b.bin"), []
    got = fw.backup_esptool(ROM, out, lambda a, b: seen.append(a / b), tool=tool)
    args = log.read_text()
    assert "--before no-reset --after no-reset read-flash 0 8388608" in args
    assert got.size == 8388608 and os.path.getsize(out) == 8388608 and seen == [0.5]
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600


def test_an_esp_backup_of_the_wrong_size_is_not_kept(tmp_path):
    tool = fake_tool(tmp_path, "open(args[-1], 'r+b').write(b'x' * 10)\n")
    out = str(tmp_path / "b.bin")
    with pytest.raises(fw.FirmwareError, match="wrong size"):
        fw.backup_esptool(ROM, out, tool=tool)
    assert not os.path.exists(out)


def test_an_esp_backup_failing_is_not_kept(tmp_path):
    tool = fake_tool(tmp_path, "print('fatal')\nsys.exit(2)\n")
    out = str(tmp_path / "b.bin")
    with pytest.raises(fw.FirmwareError, match="esptool failed"):
        fw.backup_esptool(ROM, out, tool=tool)
    assert not os.path.exists(out)


def test_an_unknown_flash_size_is_refused(tmp_path):
    drive = fw.BootDrive("/dev/fake", "m", "ESP32-S3", "esp32s3", "esptool", fw.EspChip("ESP32-S3", None, None))
    with pytest.raises(fw.FirmwareError, match="not known"):
        fw.backup_esptool(drive, str(tmp_path / "b.bin"), tool=["unused"])


def test_esp_erase_stays_in_the_rom_and_shows_its_stage(tmp_path):
    log = tmp_path / "args"
    tool = fake_tool(tmp_path, "open(%r, 'w').write(' '.join(args))\n"
                               "print('Erasing flash memory (this may take a while)...')\n" % str(log))
    seen = []
    fw.erase_esptool(ROM, seen.append, tool=tool)
    assert log.read_text().endswith("--before no-reset --after no-reset erase-flash") and seen == ["Erasing flash"]


def nuke_block(number, total, family, addr=0x20000000):
    head = struct.pack("<8I", fw.MAGIC_START0, fw.MAGIC_START1, fw.FLAG_HAS_FAMILY, addr + number * 256, 256, number,
                       total, family)
    return head + bytes(476) + struct.pack("<I", fw.MAGIC_END)


def nuke(tmp_path, data=None):
    p = tmp_path / "flash_nuke.uf2"
    p.write_bytes(data if data is not None else b"".join(
        nuke_block(n, 2, fam) for fam in (0xE48BFF56, 0xE48BFF57) for n in range(2)))   # as Raspberry Pi's file is
    return str(p)


def test_the_real_shape_of_the_erase_tool_passes(tmp_path):
    fw.check_nuke_uf2(nuke(tmp_path))


@pytest.mark.parametrize("data, message", [
    (b"", "not a UF2"),
    (b"\0" * 512, "bad block"),
    (nuke_block(0, 2, 0xE48BFF56), "incomplete"),
    (nuke_block(0, 1, 0xE48BFF56, addr=fw.FLASH_START), "outside RAM"),      # would write flash, not erase it
])
def test_a_bad_erase_tool_is_refused(tmp_path, data, message):
    with pytest.raises(fw.FirmwareError, match=message):
        fw.check_nuke_uf2(nuke(tmp_path, data))


def uf2_drive(tmp_path):
    d = tmp_path / "RPI-RP2"
    d.mkdir()
    (d / fw.INFO_FILE).write_text("UF2 Bootloader v3.0\nModel: Raspberry Pi RP2\nBoard-ID: RPI-RP2\n")
    return fw.read_drive(str(d))


def test_uf2_erase_copies_the_tool_and_waits_for_the_drive_to_come_back(tmp_path, monkeypatch):
    drive = uf2_drive(tmp_path)
    copied = []
    monkeypatch.setattr(fw, "_copy_to_drive", lambda image, d, p, c: copied.append(image))
    monkeypatch.setattr(fw, "wait_gone", lambda d: True)
    answers = iter([[drive], [], [drive]])
    monkeypatch.setattr(fw, "detect", lambda: next(answers))
    image = nuke(tmp_path)
    fw.erase_uf2(image, drive, poll=0)
    assert copied == [image]


def test_uf2_erase_reports_a_board_that_does_not_start_or_return(tmp_path, monkeypatch):
    drive = uf2_drive(tmp_path)
    image = nuke(tmp_path)
    monkeypatch.setattr(fw, "_copy_to_drive", lambda *a: None)
    monkeypatch.setattr(fw, "detect", lambda: [])
    monkeypatch.setattr(fw, "wait_gone", lambda d: False)
    with pytest.raises(fw.FirmwareError, match="did not start"):
        fw.erase_uf2(image, drive)
    monkeypatch.setattr(fw, "wait_gone", lambda d: True)
    with pytest.raises(fw.FirmwareError, match="did not come back"):
        fw.erase_uf2(image, drive, timeout=0)


def test_uf2_erase_refuses_a_changed_drive(tmp_path):
    drive = uf2_drive(tmp_path)
    image = nuke(tmp_path)
    os.unlink(os.path.join(drive.path, fw.INFO_FILE))
    with pytest.raises(fw.FirmwareError, match="gone or changed"):
        fw.erase_uf2(image, drive)


def test_running_picos_are_listed_after_boards_in_a_bootloader(monkeypatch):
    monkeypatch.setattr(fw, "rp2_ports", lambda: {"/dev/p"})
    monkeypatch.setattr(fw, "rp2_any_ports", lambda: {"/dev/p"})        # no CircuitPython board besides it
    monkeypatch.setattr(fw, "detect", lambda m: [])
    monkeypatch.setattr(fw, "detect_rom", lambda: [ROM])
    monkeypatch.setattr(fw, "badge_ports", lambda name: {"/dev/t"} if name == "TIDAL" else set())
    assert [d.method for d in fw.detect_all()] == ["esptool", "repl", "badge"]


def test_read_running_says_what_the_board_is_and_resets_it():
    repl = FlashRepl(flash_of(2), "Raspberry Pi Pico with RP2040")
    got = fw.read_running("/dev/x", open_board=opener(repl))
    assert got == fw.RunningInfo("Raspberry Pi Pico with RP2040", "1.29.0", 2) and repl.closed is True


def test_read_running_on_a_busy_port_says_it_may_be_connected():
    def busy(port):
        raise OSError("busy")
    with pytest.raises(fw.FirmwareError, match="connected in the app"):
        fw.read_running("/dev/x", open_board=busy)


@pytest.mark.parametrize("drive, running, label", [
    (fw.BootDrive("/dev/a", "m", "MicroPython", None, "repl"), None, "MicroPython board at /dev/a"),
    (fw.BootDrive("/dev/a", "m", "MicroPython", None, "repl"), "cannot read", "MicroPython board at /dev/a"),
    (fw.BootDrive("/dev/a", "m", "MicroPython", None, "repl"), fw.RunningInfo("Raspberry Pi Pico with RP2040", "1", 2),
     "Pico (RP2040) at /dev/a"),
    (ROM, None, "Tildagon (2024, 2026) at /dev/fake"),
    (fw.BootDrive("/dev/b", "m", "E", "esp32s3", "esptool"), None, "ESP32-S3 at /dev/b"),
    (fw.BootDrive("/m/d", "m", "RP2350", "rp2350"), None, "RP2350 at /m/d"),
    (fw.BootDrive("/m/d", "m", "X", None), None, "Unsupported (X) at /m/d"),
])
def test_device_labels(drive, running, label):
    assert fw.device_label(drive, running) == label


def test_uf2_erase_returns_the_drive_that_came_back(tmp_path, monkeypatch):
    drive = uf2_drive(tmp_path)
    image = nuke(tmp_path)
    back = fw.BootDrive("/m/new", "m", "RPI-RP2", "rp2040")
    monkeypatch.setattr(fw, "_copy_to_drive", lambda *a: None)
    monkeypatch.setattr(fw, "wait_gone", lambda d: True)
    answers = iter([[], [back]])
    monkeypatch.setattr(fw, "detect", lambda: next(answers))
    assert fw.erase_uf2(image, drive, poll=0) == back


class BootRepl:
    def __init__(self, on_exec=None):
        self.on_exec, self.code = on_exec, None

    def exec(self, code, timeout=0):
        self.code = code
        if self.on_exec:
            raise self.on_exec

    def close(self, reset=False):
        assert not reset                            # a reset now would undo it
        if isinstance(self.on_exec, OSError):
            raise OSError("gone")


@pytest.mark.parametrize("lost", [None, OSError("device disconnected"), TransportError("no reply")])
def test_entering_the_bootloader_finds_the_new_drive_even_as_the_port_vanishes(monkeypatch, lost):
    old = fw.BootDrive("/m/old", "m", "RP2350", "rp2350")
    new = fw.BootDrive("/m/new", "m", "RPI-RP2", "rp2040")
    answers = iter([[old], [old], [old, new]])
    monkeypatch.setattr(fw, "detect", lambda: next(answers))
    repl = BootRepl(lost)
    assert fw.enter_bootloader("/dev/x", poll=0, open_board=opener(repl)) == new
    assert "machine.bootloader()" in repl.code


def test_a_board_that_does_not_enter_the_bootloader_says_what_to_do(monkeypatch):
    monkeypatch.setattr(fw, "detect", lambda: [])
    with pytest.raises(fw.FirmwareError, match="Use its buttons"):
        fw.enter_bootloader("/dev/x", timeout=0, open_board=opener(BootRepl()))


def test_a_busy_port_is_reported_when_entering_the_bootloader(monkeypatch):
    monkeypatch.setattr(fw, "detect", lambda: [])

    def busy(port):
        raise OSError("busy")
    with pytest.raises(fw.FirmwareError, match="connected in the app"):
        fw.enter_bootloader("/dev/x", open_board=busy)


def test_a_running_badge_is_named_by_usb_without_opening_its_port(monkeypatch):
    monkeypatch.setattr(fw, "badge_ports", lambda name: {"/dev/t"} if name == "TILDAGON" else {"/dev/o"})
    monkeypatch.setattr(fw.RawREPL, "open_board", lambda *a, **k: pytest.fail("opened a badge"))
    found = fw.detect_running_badges()
    assert [fw.device_label(d) for d in found] == ["Tildagon (2024, 2026) at /dev/t", "TiDAL (2022 badge) at /dev/o"]
    assert [fw.can_enter_bootloader(d) for d in found] == [True, False]
    assert "Start bootloader" in fw.bootloader_hint(found[0])
    assert "Start bootloader" not in fw.bootloader_hint(found[1]) and "bottom right" in fw.bootloader_hint(found[1])


def test_a_badge_entering_its_bootloader_is_watched_for_as_a_rom_port():
    answers = iter([[], [ROM]])
    repl = BootRepl()
    assert fw.enter_bootloader("/dev/x", poll=0, open_board=opener(repl), find=lambda: next(answers)) == ROM


@pytest.mark.parametrize("drive, button, words", [
    (fw.BootDrive("/dev/a", "m", "MicroPython", None, "repl"), True, "BOOTSEL"),
    (fw.BootDrive("/dev/t", "m", "TILDAGON", None, "badge"), True, "hold boop"),
    (fw.BootDrive("/dev/t", "m", "TIDAL", None, "badge"), False, "bottom right"),
])
def test_each_running_board_gets_its_own_way_to_the_bootloader(drive, button, words):
    hint = fw.bootloader_hint(drive)
    assert words in hint and ("Start bootloader. If that does not work" in hint) == button


def test_the_details_do_not_repeat_the_steps():
    for drive in (fw.BootDrive("/dev/t", "m", "TILDAGON", None, "badge"), ROM,
                  fw.BootDrive("/dev/a", "m", "MicroPython", None, "repl"),
                  fw.BootDrive("/m/d", "m", "RP2350", "rp2350")):
        text = str(fw.device_details(drive))
        assert "boop" not in text and "hold" not in text.lower()


class FakeSerial:
    def __init__(self, reply=b"MicroPython\r\n>>> ", drop_on=None):
        self.buffer, self.sent, self.drop_on, self.closed = reply, [], drop_on, False

    @property
    def in_waiting(self):
        return len(self.buffer)

    def read(self, n):
        out, self.buffer = self.buffer[:n], self.buffer[n:]
        return out

    def write(self, data):
        if self.drop_on and self.drop_on in data:
            raise OSError("gone")
        self.sent.append(data)

    def close(self):
        self.closed = True


class Holder:
    def __init__(self, ser):
        self.ser = ser


def test_a_badge_is_asked_from_its_normal_repl_never_the_raw_one():
    ser, answers = FakeSerial(), iter([[], [ROM]])
    got = fw.enter_bootloader("/dev/x", poll=0, find=lambda: next(answers), normal_repl=True,
                              open_board=lambda port: pytest.fail("raw REPL"), open_port=lambda port: Holder(ser))
    assert got == ROM and b"machine.bootloader()\r\n" in ser.sent and ser.closed
    assert b"\x01" not in b"".join(ser.sent)        # Ctrl-A would enter the raw REPL


def test_a_badge_going_away_as_it_is_told_is_fine():
    ser, answers = FakeSerial(drop_on=b"machine.bootloader"), iter([[], [ROM]])
    assert fw.enter_bootloader("/dev/x", poll=0, find=lambda: next(answers), normal_repl=True,
                               open_port=lambda port: Holder(ser)) == ROM


def test_a_badge_with_no_prompt_is_reported_and_not_sent_the_command(monkeypatch):
    ser = FakeSerial(reply=b"")
    monkeypatch.setattr(fw.time, "monotonic", iter(range(0, 1000, 5)).__next__)
    with pytest.raises(fw.FirmwareError, match="did not give its prompt"):
        fw.enter_bootloader("/dev/x", find=lambda: [], normal_repl=True, open_port=lambda port: Holder(ser))
    assert not any(b"bootloader" in d for d in ser.sent) and ser.closed


def test_a_board_that_refuses_says_why(monkeypatch):
    monkeypatch.setattr(fw, "detect", lambda: [])
    with pytest.raises(fw.FirmwareError, match="refused.*not allowed"):
        fw.enter_bootloader("/dev/x", timeout=0, open_board=opener(BootRepl(fw.DeviceError("not allowed"))))


def test_the_copy_waits_for_the_board_after_every_chunk_so_progress_is_real(tmp_path, monkeypatch):
    """A write only reaches the OS cache, so without an fsync per chunk the bar fills long before the board has it."""
    import os
    from mpexplorer import firmware as fw
    image = tmp_path / "x.uf2"
    image.write_bytes(bytes(512 * 200))                              # 200 blocks: four chunks of 64
    drive = type("D", (), {"path": str(tmp_path)})()
    events = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: events.append("sync") or real(fd))
    fw._copy_to_drive(str(image), drive, lambda done, total: events.append(done))
    assert events.count("sync") >= 4 and events.index("sync") < events.index(32768)       # synced, then reported
