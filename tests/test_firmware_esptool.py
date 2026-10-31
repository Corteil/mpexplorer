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


TILDAGON = fw.EspChip("ESP32-S3-PICO-1", 8, 2)
BREAKOUT = fw.EspChip("ESP32-S3", 16, 8)
CHECKED = fw.BootDrive("/dev/fake", "m", "ESP32-S3", "esp32s3", "esptool", TILDAGON)
# as printed by esptool 5.6.0 for a real Tildagon
FLASH_ID = """esptool v5.6.0
Connected to ESP32-S3 on /dev/ttyACM0:
Chip type:          ESP32-S3-PICO-1 (LGA56) (revision v0.2)
Features:           Wi-Fi, BT 5 (LE), Dual Core + LP Core, 240MHz, Embedded Flash 8MB (GD), Embedded PSRAM 2MB (AP_3v3)
Crystal frequency:  40MHz
Manufacturer: c8
Device: 4017
Detected flash size: 8MB
Staying in bootloader.
"""


@pytest.fixture(autouse=True)
def private_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


def esp_image(chip=9, flash=3, table=True, app=True, size=0x10100, magic=0xE9):
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
    (dict(flash=4), "is for 16 MB of flash; this chip has 8 MB"),
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


def test_erase_is_only_asked_for_when_chosen(tmp_path):
    log = tmp_path / "args.txt"
    tool = fake_tool(tmp_path, "open(%r, 'w').write(' '.join(args))\n" % str(log))
    image = write(tmp_path, esp_image())
    fw.flash_esptool(image, ROM, tool=tool)
    assert "--erase-all" not in log.read_text()
    fw.flash_esptool(image, ROM, tool=tool, erase=True)
    assert log.read_text().endswith("write-flash --erase-all 0 " + image)


def test_a_breakout_with_a_tildagons_chip_is_not_waited_for_when_its_own_firmware_was_chosen(monkeypatch):
    order = steps(monkeypatch)
    assert fw.install_and_verify("img", CHECKED, expect_tildagon=False).port == "" and order == ["ports", "flash"]


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
    monkeypatch.setattr(fw, "read_esp_chip", lambda port: TILDAGON)
    monkeypatch.setattr(fw, "tildagon_ports", lambda: order.append("ports") or set())
    monkeypatch.setattr(fw, "flash_esptool", lambda *a, **k: order.append("flash"))

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
    assert fw.install_and_verify("img", CHECKED).port == "/dev/new" and order == ["ports", "flash", "wait"]


def test_a_badge_that_does_not_come_back_says_to_replug_it_or_what_else_it_may_be(monkeypatch):
    steps(monkeypatch, wait_error=True)
    with pytest.raises(fw.FirmwareError, match="unplug it .*another board with the same chip"):
        fw.install_and_verify("img", CHECKED)


def test_a_board_swapped_after_the_check_is_not_written(monkeypatch):
    order = steps(monkeypatch)
    monkeypatch.setattr(fw, "read_esp_chip", lambda port: BREAKOUT)
    with pytest.raises(fw.FirmwareError, match="has changed. Nothing was written"):
        fw.install_and_verify("img", CHECKED)
    assert order == []


def test_another_board_is_written_but_not_waited_for_as_a_tildagon(monkeypatch):
    order = steps(monkeypatch)
    monkeypatch.setattr(fw, "read_esp_chip", lambda port: BREAKOUT)
    drive = fw.BootDrive("/dev/fake", "m", "ESP32-S3", "esp32s3", "esptool", BREAKOUT)
    assert fw.install_and_verify("img", drive).port == "" and order == ["ports", "flash"]


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


def test_reads_a_real_tildagons_output_as_a_tildagon():
    chip = fw.parse_esp_chip(FLASH_ID)
    assert chip == TILDAGON and chip.looks_like_tildagon() and chip.text == "ESP32-S3-PICO-1, 8 MB flash, 2 MB PSRAM"


@pytest.mark.parametrize("chip", [
    fw.EspChip("ESP32-S3", 8, 2),                   # a module, not the badge's PICO-1
    fw.EspChip("ESP32-S3-PICO-1", 4, 2),
    fw.EspChip("ESP32-S3-PICO-1", 8, None),
    fw.EspChip("ESP32-S3-PICO-1", None, 2),
])
def test_other_chips_do_not_look_like_a_tildagon(chip):
    assert not chip.looks_like_tildagon()


def test_a_breakout_with_no_psram_parses_and_says_so():
    chip = fw.parse_esp_chip("Chip type: ESP32-S3 (QFN56)\nFeatures: Wi-Fi\nDetected flash size: 16MB\n")
    assert chip == fw.EspChip("ESP32-S3", 16, None) and "no PSRAM" in chip.text


def test_output_that_does_not_name_the_chip_is_an_error():
    with pytest.raises(fw.FirmwareError, match="did not say"):
        fw.parse_esp_chip("Connecting...\n")


def test_read_chip_stays_in_the_rom_and_writes_nothing(tmp_path):
    log = tmp_path / "args.txt"
    tool = fake_tool(tmp_path, "open(%r, 'w').write(' '.join(args))\nprint(%r)\n" % (str(log), FLASH_ID))
    assert fw.read_esp_chip("/dev/fake", tool=tool) == TILDAGON
    args = log.read_text()
    assert "--before no-reset --after no-reset --no-stub flash-id" in args and "write" not in args


def test_read_chip_failing_asks_if_it_is_in_its_bootloader(tmp_path):
    tool = fake_tool(tmp_path, "print('fatal: no reply')\nsys.exit(2)\n")
    with pytest.raises(fw.FirmwareError, match="in its bootloader"):
        fw.read_esp_chip("/dev/fake", tool=tool)


def test_an_image_is_checked_against_the_flash_this_chip_really_has(tmp_path):
    path = write(tmp_path, esp_image(flash=3))
    with pytest.raises(fw.FirmwareError, match="is for 8 MB of flash; this chip has 4 MB"):
        fw.check_esp_image(path, 4 * 1024 * 1024)
    assert fw.check_esp_image(write(tmp_path, esp_image(flash=2), "small.bin")).cpu == "Xtensa"
