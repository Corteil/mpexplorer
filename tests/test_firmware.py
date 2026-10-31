"""Firmware install for UF2 boards: the checks that stop a bad write, against fake bootloader drives."""
import errno
import os
import struct
import threading
from pathlib import Path

import pytest

from mpexplorer import firmware as fw
from mpexplorer.errors import Cancelled
from mpexplorer.transport import TransportError

RP2040 = 0xE48BFF56
ARM = 0xE48BFF59
RISCV = 0xE48BFF5A
RP2040_INFO = "UF2 Bootloader v3.0\nModel: Raspberry Pi RP2\nBoard-ID: RPI-RP2\n"     # as read from a real Pico
RP2350_INFO = "UF2 Bootloader v1.0\nModel: Raspberry Pi RP2350\nBoard-ID: RP2350\n"   # the bootrom source's defaults


def block(number, total, family, addr=None, size=256, flags=fw.FLAG_HAS_FAMILY, end=fw.MAGIC_END):
    addr = fw.FLASH_START + number * 256 if addr is None else addr
    head = struct.pack("<8I", fw.MAGIC_START0, fw.MAGIC_START1, flags, addr, size, number, total, family)
    return head + bytes(476) + struct.pack("<I", end)


def uf2(tmp_path, blocks=None, family=RP2040, total=4, name="fw.uf2"):
    data = blocks if blocks is not None else b"".join(block(i, total, family) for i in range(total))
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def make_drive(tmp_path, info=RP2040_INFO, name="RPI-RP2"):
    d = tmp_path / name
    d.mkdir()
    (d / fw.INFO_FILE).write_text(info)
    return fw.read_drive(str(d))


def test_reads_the_real_rp2040_info_and_the_rp2350_defaults(tmp_path):
    d = make_drive(tmp_path)
    assert (d.model, d.board_id, d.chip) == ("Raspberry Pi RP2", "RPI-RP2", "rp2040")
    assert make_drive(tmp_path, RP2350_INFO, "RP2350").chip == "rp2350"


def test_an_unknown_board_id_is_a_drive_with_no_chip(tmp_path):
    assert make_drive(tmp_path, "UF2 Bootloader\nModel: X\nBoard-ID: SAMD21-X\n").chip is None


def test_a_folder_without_a_usable_info_file_is_not_a_drive(tmp_path):
    assert fw.read_drive(str(tmp_path)) is None
    (tmp_path / fw.INFO_FILE).write_text("nothing useful\n")
    assert fw.read_drive(str(tmp_path)) is None


def test_detect_goes_by_the_info_file_not_the_name(tmp_path):
    good = make_drive(tmp_path, name="MY DISK")
    fake_name = tmp_path / "RPI-RP2"
    fake_name.mkdir()                       # looks right, holds no info file
    other = make_drive(tmp_path, name="ext")
    mounts = tmp_path / "mounts"
    mounts.write_text("/dev/sda1 %s vfat rw 0 0\n/dev/sdb1 %s vfat rw 0 0\n"
                      "/dev/x %s ext4 rw 0 0\n/dev/y /gone vfat rw 0 0\n"
                      % (good.path.replace(" ", "\\040"), fake_name, other.path))
    assert [d.path for d in fw.detect(str(mounts))] == [good.path]


def test_detect_with_no_mount_table_finds_nothing(tmp_path):
    assert fw.detect(str(tmp_path / "missing")) == []


def test_check_accepts_a_good_image(tmp_path):
    info = fw.check_uf2(uf2(tmp_path), "rp2040")
    assert (info.family, info.cpu, info.blocks, info.size) == (RP2040, "Arm", 4, 1024)


def test_check_skips_the_sdk_absolute_block_as_in_the_pico2_firmware(tmp_path):
    # MicroPython's RPI_PICO2 v1.29.0: one E48BFF57 block at 0x10FFFF00 with its own total, then the Arm blocks
    first = block(0, 2, fw.FAMILY_ABSOLUTE, addr=0x10FFFF00)
    path = uf2(tmp_path, first + b"".join(block(i, 3, ARM) for i in range(3)))
    assert fw.check_uf2(path, "rp2350").blocks == 3


def test_check_names_risc_v(tmp_path):
    assert fw.check_uf2(uf2(tmp_path, family=RISCV), "rp2350").cpu == "RISC-V"


@pytest.mark.parametrize("family, chip", [(RP2040, "rp2350"), (ARM, "rp2040"), (RISCV, "rp2040"),
                                          (0xE48BFF58, "rp2350")])
def test_check_refuses_the_wrong_chip(tmp_path, family, chip):
    with pytest.raises(fw.FirmwareError, match="not for the"):
        fw.check_uf2(uf2(tmp_path, family=family), chip)


@pytest.mark.parametrize("blocks, message", [
    (b"", "not a UF2"),
    (b"x" * 100, "not a UF2"),
    (b"\0" * 512, "bad block"),
    (block(0, 1, RP2040, end=0), "bad block"),
    (block(0, 1, RP2040, flags=0), "does not say"),
    (block(0, 3, RP2040) + block(1, 3, RP2040), "incomplete"),
    (block(0, 2, RP2040) + block(0, 2, RP2040), "bad block"),
    (block(5, 2, RP2040), "bad block"),
    (block(0, 1, RP2040, addr=0x20000000), "bad block"),       # a RAM image: not an install
    (block(0, 1, RP2040, size=477), "bad block"),
    (block(0, 1, RP2040, size=0), "bad block"),
    (block(0, 2, fw.FAMILY_ABSOLUTE), "holds no firmware"),
    (block(0, 2, RP2040) + block(1, 3, RP2040), "more than one"),
])
def test_check_refuses_a_damaged_image(tmp_path, blocks, message):
    with pytest.raises(fw.FirmwareError, match=message):
        fw.check_uf2(uf2(tmp_path, blocks), "rp2040")


def test_check_refuses_an_esp_app_image(tmp_path):
    with pytest.raises(fw.FirmwareError, match="not a UF2"):
        fw.check_uf2(uf2(tmp_path, b"\xe9" * 1024, name="micropython.bin"), "rp2040")


def test_check_refuses_a_missing_file_and_an_unknown_chip(tmp_path):
    with pytest.raises(fw.FirmwareError, match="Cannot read"):
        fw.check_uf2(str(tmp_path / "nope.uf2"), "rp2040")
    with pytest.raises(fw.FirmwareError, match="No firmware rule"):
        fw.check_uf2(uf2(tmp_path), "samd21")


def restart_when_written(drive, size):
    """The fake board: its drive goes away once the whole image has arrived, like the real bootloader."""
    target = Path(drive.path) / "firmware.uf2"
    info = Path(drive.path) / fw.INFO_FILE
    stop = threading.Event()

    def watch():
        while not stop.wait(0.01):
            if target.exists() and target.stat().st_size >= size:
                info.unlink()
                return
    t = threading.Thread(target=watch, daemon=True)
    t.start()
    return stop


def test_install_copies_every_byte_and_waits_for_the_restart(tmp_path):
    image, drive = uf2(tmp_path, total=200), make_drive(tmp_path)
    seen = []
    stop = restart_when_written(drive, os.path.getsize(image))
    try:
        info = fw.install(image, drive, on_progress=lambda a, b: seen.append((a, b)), gone_timeout=5)
    finally:
        stop.set()
    assert info.blocks == 200
    assert (Path(drive.path) / "firmware.uf2").read_bytes() == Path(image).read_bytes()
    assert seen[-1] == (200 * 512, 200 * 512) and len(seen) > 1


def test_install_writes_nothing_when_the_image_is_wrong(tmp_path):
    image, drive = uf2(tmp_path, family=ARM), make_drive(tmp_path)
    with pytest.raises(fw.FirmwareError, match="not for the"):
        fw.install(image, drive)
    assert not (Path(drive.path) / "firmware.uf2").exists()


def test_install_refuses_a_bootloader_with_no_rule(tmp_path):
    image, drive = uf2(tmp_path), make_drive(tmp_path, "T\nBoard-ID: OTHER\n")
    with pytest.raises(fw.FirmwareError, match="No firmware rule"):
        fw.install(image, drive)


def test_install_refuses_a_drive_that_changed_since_it_was_chosen(tmp_path):
    image, drive = uf2(tmp_path), make_drive(tmp_path)
    (Path(drive.path) / fw.INFO_FILE).write_text(RP2350_INFO)
    with pytest.raises(fw.FirmwareError, match="gone or changed"):
        fw.install(image, drive)
    os.unlink(os.path.join(drive.path, fw.INFO_FILE))
    with pytest.raises(fw.FirmwareError, match="gone or changed"):
        fw.install(image, drive)


def test_install_reports_a_board_that_never_restarts(tmp_path):
    image, drive = uf2(tmp_path), make_drive(tmp_path)
    with pytest.raises(fw.FirmwareError, match="did not restart"):
        fw.install(image, drive, gone_timeout=0.05)


def test_install_can_be_cancelled(tmp_path):
    image, drive = uf2(tmp_path, total=200), make_drive(tmp_path)
    with pytest.raises(Cancelled):
        fw.install(image, drive, cancelled=lambda: True)


class FailingFile:
    """A write to the drive that fails on the chosen chunk, as when the board drops off USB."""
    def __init__(self, f, fail_on):
        self.f, self.fail_on, self.n = f, fail_on, 0

    def write(self, data):
        self.n += 1
        if self.n == self.fail_on:
            raise OSError(errno.EIO, "I/O error")
        return self.f.write(data)

    def fileno(self):
        return self.f.fileno()

    def close(self):
        self.f.close()


def fail_writes(monkeypatch, fail_on):
    real = open

    def fake(path, mode="r", **kw):
        if str(path).endswith("firmware.uf2") and "w" in mode:
            return FailingFile(real(path, mode, **kw), fail_on)
        return real(path, mode, **kw)
    monkeypatch.setattr("builtins.open", fake)


def test_install_reports_a_write_that_fails_early(tmp_path, monkeypatch):
    image, drive = uf2(tmp_path, total=200), make_drive(tmp_path)
    fail_writes(monkeypatch, 1)
    with pytest.raises(fw.FirmwareError, match="failed after 0 of"):
        fw.install(image, drive)


def test_an_error_on_the_last_chunk_is_fine_if_the_board_restarts(tmp_path, monkeypatch):
    image, drive = uf2(tmp_path, total=64), make_drive(tmp_path)        # exactly one chunk
    fail_writes(monkeypatch, 1)
    gone = []
    monkeypatch.setattr(fw, "wait_gone", lambda d, t: gone.append(d) or True)
    assert fw.install(image, drive).blocks == 64 and gone == [drive]


def test_an_error_on_the_last_chunk_fails_if_the_board_stays(tmp_path, monkeypatch):
    image, drive = uf2(tmp_path, total=64), make_drive(tmp_path)
    fail_writes(monkeypatch, 1)
    with pytest.raises(fw.FirmwareError, match="did not restart"):
        fw.install(image, drive, gone_timeout=0.05)


def test_wait_for_new_port_ignores_ports_that_were_already_there():
    answers = iter([{"/dev/ttyACM1"}, {"/dev/ttyACM1", "/dev/ttyACM2"}])
    assert fw.wait_for_new_port({"/dev/ttyACM1"}, poll=0, ports=lambda: next(answers)) == "/dev/ttyACM2"


def test_wait_for_new_port_gives_up():
    with pytest.raises(fw.FirmwareError, match="no MicroPython serial port"):
        fw.wait_for_new_port({"/dev/ttyACM1"}, timeout=0, ports=lambda: {"/dev/ttyACM1"})


class FakeRepl:
    def __init__(self, reply=b"1.29.0\r\nRaspberry Pi Pico with RP2040\r\n", error=None):
        self.reply, self.error, self.closed = reply, error, None

    def exec(self, code):
        if self.error:
            raise self.error
        return self.reply

    def close(self, reset=False):
        self.closed = reset


def test_identify_retries_until_the_port_answers_and_resets_the_board():
    repl = FakeRepl()
    tries = []

    def open_board(port):
        tries.append(port)
        if len(tries) < 3:
            raise OSError("not ready")
        return repl, False
    got = fw.identify("/dev/ttyACM3", "rp2040", poll=0, open_board=open_board)
    assert (got.port, got.release, got.machine) == ("/dev/ttyACM3", "1.29.0", "Raspberry Pi Pico with RP2040")
    assert len(tries) == 3 and repl.closed is True


def test_identify_refuses_the_wrong_chip():
    repl = FakeRepl()
    with pytest.raises(fw.FirmwareError, match="not the expected RP2350"):
        fw.identify("/dev/ttyACM3", "rp2350", open_board=lambda port: (repl, False))
    assert repl.closed is True


def test_identify_gives_up_on_a_silent_port():
    def open_board(port):
        raise TransportError("silent")
    with pytest.raises(fw.FirmwareError, match="did not answer"):
        fw.identify("/dev/ttyACM3", "rp2040", timeout=0, open_board=open_board)


@pytest.mark.parametrize("repl", [FakeRepl(error=TransportError("gone")), FakeRepl(reply=b"odd\r\n")])
def test_identify_reports_a_bad_reply_and_still_closes(repl):
    with pytest.raises(fw.FirmwareError):
        fw.identify("/dev/ttyACM3", "rp2040", open_board=lambda port: (repl, False))
    assert repl.closed is True
