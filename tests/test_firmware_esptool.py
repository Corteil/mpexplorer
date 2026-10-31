"""Writing a badge with esptool, against a fake esptool script. Never runs the real one or opens a real port."""
import stat
import struct
import sys
import time

import pytest

from mpexplorer import firmware as fw
from mpexplorer.errors import Cancelled
from mpexplorer.transport import TransportError

ROM = fw.BootDrive("/dev/fake", "ESP32-S3 ROM bootloader", "ESP32-S3", "esp32s3", "esptool")


@pytest.fixture(autouse=True)
def private_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


def esp_image(chip=9, flash=fw.ESP_FLASH_8MB, table=True, app=True, size=0x10100, magic=0xE9):
    data = bytearray(size)
    if size >= 16:
        data[0], data[3] = magic, flash << 4 | 0xF
        struct.pack_into("<H", data, 12, chip)
    if table and size > fw.ESP_PARTITION_TABLE + 2:
        data[fw.ESP_PARTITION_TABLE:fw.ESP_PARTITION_TABLE + 2] = b"\xaa\x50"
    if app and size > fw.ESP_APP_OFFSET:
        data[fw.ESP_APP_OFFSET] = 0xE9
    return bytes(data)


def write(tmp_path, data, name="merged.bin"):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def test_check_accepts_a_merged_image(tmp_path):
    info = fw.check_esp_image(write(tmp_path, esp_image()))
    assert info.size == 0x10100 and info.cpu == "Xtensa"


@pytest.mark.parametrize("kw, message", [
    (dict(magic=0), "not an ESP firmware"),
    (dict(size=4), "not an ESP firmware"),
    (dict(chip=0), "not for the ESP32-S3"),
    (dict(flash=2), "not for 8 MB"),
    (dict(table=False), "not a complete image"),       # an app-only micropython.bin
    (dict(app=False), "not a complete image"),
    (dict(size=0x8000), "not a complete image"),
    (dict(size=fw.ESP_FLASH_BYTES + 1), "larger than the flash"),
])
def test_check_refuses(tmp_path, kw, message):
    with pytest.raises(fw.FirmwareError, match=message):
        fw.check_esp_image(write(tmp_path, esp_image(**kw)))


def test_check_image_picks_the_rule_by_chip(tmp_path):
    path = write(tmp_path, esp_image())
    assert fw.check_image(path, "esp32s3").cpu == "Xtensa"
    with pytest.raises(fw.FirmwareError, match="not a UF2"):
        fw.check_image(path, "rp2040")


def fake_tool(tmp_path, body):
    script = tmp_path / "fake_esptool.py"
    script.write_text("import sys, time\nargs = sys.argv[1:]\n" + body)
    return [sys.executable, str(script)]


def test_flash_passes_the_port_and_offset_and_reports_progress(tmp_path):
    log = tmp_path / "args.txt"
    tool = fake_tool(tmp_path, "open(%r, 'w').write(' '.join(args))\n"
                               "for p in (10.5, 50, 100):\n"
                               "    sys.stdout.write('Writing at 0x00010000 [==] %%s%%%% 1/2 bytes\\r' %% p)\n"
                               "    sys.stdout.flush()\n"
                               "print()\nprint('Hash of data verified.')\n" % str(log))
    image = write(tmp_path, esp_image())
    seen = []
    assert fw.flash_esptool(image, ROM, on_progress=lambda a, b: seen.append(a / b), tool=tool).cpu == "Xtensa"
    assert seen == [0.105, 0.5, 1.0]
    args = log.read_text()
    assert "--chip esp32s3 --port /dev/fake" in args and args.endswith("write-flash 0 " + image)
    assert "--before no-reset --after watchdog-reset" in args      # what worked on a real Tildagon


def test_every_run_leaves_its_full_output_in_a_log(tmp_path):
    tool = fake_tool(tmp_path, "print('line one')\nprint('fatal: line two')\nsys.exit(2)\n")
    with pytest.raises(fw.FirmwareError, match="Full output: .*esptool.log"):
        fw.flash_esptool(write(tmp_path, esp_image()), ROM, tool=tool)
    text = open(fw.esptool_log_path()).read()
    assert "write-flash" in text and "line one" in text and "fatal: line two" in text


def test_flash_reports_esptool_failing_with_its_message_and_no_colour_codes(tmp_path):
    tool = fake_tool(tmp_path, "print('\\x1b[31mA fatal error occurred: no serial data\\x1b[0m')\nsys.exit(2)\n")
    with pytest.raises(fw.FirmwareError, match=r"esptool failed\.\nA fatal error occurred: no serial data\n"):
        fw.flash_esptool(write(tmp_path, esp_image()), ROM, tool=tool)


def test_flash_refuses_a_bad_image_without_running_esptool(tmp_path):
    log = tmp_path / "ran"
    tool = fake_tool(tmp_path, "open(%r, 'w').close()\n" % str(log))
    with pytest.raises(fw.FirmwareError, match="not a complete"):
        fw.flash_esptool(write(tmp_path, esp_image(table=False)), ROM, tool=tool)
    assert not log.exists()


def test_flash_refuses_a_port_that_has_gone(tmp_path, monkeypatch):
    monkeypatch.setattr(fw, "_ports_with", lambda usb: set())
    with pytest.raises(fw.FirmwareError, match="has gone"):
        fw.flash_esptool(write(tmp_path, esp_image()), ROM)


def test_cancel_stops_esptool(tmp_path):
    tool = fake_tool(tmp_path, "print('Connecting', flush=True)\ntime.sleep(60)\n")
    started = time.monotonic()
    calls = []
    with pytest.raises(Cancelled):
        fw.flash_esptool(write(tmp_path, esp_image()), ROM, cancelled=lambda: calls.append(1) or len(calls) > 3,
                         tool=tool)
    assert time.monotonic() - started < 10


def test_a_silent_esptool_is_stopped(tmp_path):
    tool = fake_tool(tmp_path, "print('Connecting', flush=True)\ntime.sleep(60)\n")
    with pytest.raises(fw.FirmwareError, match="stopped making progress"):
        fw.flash_esptool(write(tmp_path, esp_image()), ROM, tool=tool, stall=0.3)


def versioned_tool(tmp_path, name, version):
    p = tmp_path / name
    p.write_text("#!%s\nprint('esptool v%s')\nprint('%s')\n" % (sys.executable, version, version))
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


@pytest.fixture
def no_module(monkeypatch):
    monkeypatch.setattr(fw.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(fw.os.path, "expanduser", lambda p: "/nonexistent/esptool")


def test_find_esptool_uses_a_program_on_the_path(tmp_path, monkeypatch, no_module):
    program = versioned_tool(tmp_path, "esptool", "5.6.0")
    monkeypatch.setattr(fw.shutil, "which", lambda name: program)
    assert fw.find_esptool() == [program]


def test_find_esptool_refuses_another_major_version(tmp_path, monkeypatch, no_module):
    program = versioned_tool(tmp_path, "esptool", "4.8.1")
    monkeypatch.setattr(fw.shutil, "which", lambda name: program)
    with pytest.raises(fw.FirmwareError, match="pipx install esptool"):
        fw.find_esptool()


def test_find_esptool_says_how_to_install_it(monkeypatch, no_module):
    monkeypatch.setattr(fw.shutil, "which", lambda name: None)
    with pytest.raises(fw.FirmwareError, match="pipx install esptool"):
        fw.find_esptool()


def test_detect_rom_lists_only_the_rom_usb_id(monkeypatch):
    monkeypatch.setattr(fw, "_ports_with", lambda usb: {"/dev/b", "/dev/a"} if usb == fw.ESP32S3_ROM_USB else set())
    assert [(d.path, d.method, d.chip) for d in fw.detect_rom()] == [("/dev/a", "esptool", "esp32s3"),
                                                                     ("/dev/b", "esptool", "esp32s3")]


def steps(monkeypatch, ports=None, wait_error=None):
    order = []
    monkeypatch.setattr(fw, "tildagon_ports", lambda: order.append("ports") or set())
    monkeypatch.setattr(fw, "flash_esptool", lambda *a: order.append("flash"))

    def wait(before, timeout, ports):
        order.append("wait")
        if wait_error:
            raise fw.FirmwareError("no port")
        return "/dev/new"
    monkeypatch.setattr(fw, "wait_for_new_port", wait)
    monkeypatch.setattr(fw, "identify", lambda port, chip, timeout: fw.Installed(port, "1.28.0", "Tildagon"))
    return order


def test_install_and_verify_for_a_badge(monkeypatch):
    order = steps(monkeypatch)
    assert fw.install_and_verify("img", ROM).port == "/dev/new" and order == ["ports", "flash", "wait"]


def test_a_badge_that_does_not_come_back_says_to_replug_it(monkeypatch):
    steps(monkeypatch, wait_error=True)
    with pytest.raises(fw.FirmwareError, match="Unplug it"):
        fw.install_and_verify("img", ROM)


class Repl:
    def exec(self, code):
        return b"1.28.0\r\nTildagon with ESP32S3\r\n"

    def close(self, reset=False):
        pass


def test_identify_knows_how_a_badge_spells_its_chip():
    got = fw.identify("/dev/x", "esp32s3", open_board=lambda port: (Repl(), False))
    assert got.machine == "Tildagon with ESP32S3"


def test_identify_errors_are_still_transport_safe():
    def open_board(port):
        raise TransportError("silent")
    with pytest.raises(fw.FirmwareError, match="did not answer"):
        fw.identify("/dev/x", "esp32s3", timeout=0, open_board=open_board)
