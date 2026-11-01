import io
import json

import pytest

from mpexplorer import cp_firmware as cpf
from mpexplorer import firmware as fw

PAGE = '---\nlayout: download\nboard_id: "%s"\nname: "%s"\nmanufacturer: "%s"\nfamily: %s\ntags:\n  - x\n---\nText\n'
FILES = [
    {"id": "raspberry_pi_pico2", "versions": [
        {"extensions": ["uf2"], "languages": ["en_US", "de_DE"], "stable": True, "version": "10.3.1"},
        {"extensions": ["uf2"], "languages": ["en_US"], "stable": False, "version": "11.0.0-alpha.2"},
        {"extensions": ["uf2"], "languages": ["en_US"], "stable": True, "version": "9.2.9"}]},
    {"id": "raspberry_pi_pico", "versions": [{"extensions": ["uf2"], "languages": ["en_US"], "stable": True,
                                               "version": "10.3.1"}]},
    {"id": "esp_only", "versions": [{"extensions": ["bin"], "languages": ["en_US"], "stable": True,
                                     "version": "10.3.1"}]},
]
PAGES = {"raspberry_pi_pico2": ("Pico 2", "Raspberry Pi", "rp2350"),
         "raspberry_pi_pico": ("Pico", "Raspberry Pi", "rp2040")}


class Reply(io.BytesIO):
    def __init__(self, data, url):
        super().__init__(data)
        self.url, self.headers = url, {"Content-Length": str(len(data))}

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass


def site(down=False):
    folders = ("raspberry_pi_pico2", "raspberry_pi_pico", "esp_only", "not_listed")
    pages = {cpf.FILES_JSON: json.dumps(FILES).encode(),
             cpf.RP_BOARDS: json.dumps([{"name": n, "type": "dir"} for n in folders]).encode()}
    for board_id, (name, maker, family) in PAGES.items():
        pages[cpf.BOARD_PAGE % board_id] = (PAGE % (board_id, name, maker, family)).encode()
    pages[cpf.DOWNLOAD % ("raspberry_pi_pico2", "raspberry_pi_pico2", "10.3.1")] = b"UF2 bytes"
    asked = []

    def opener(url):
        asked.append(url)
        if down or url not in pages:
            raise OSError("404 " + url)
        return Reply(pages[url], url)
    return opener, asked


def test_board_page_front_matter():
    assert cpf.parse_board_page(PAGE % ("x", "Pico 2", "Raspberry Pi", "rp2350")) == {
        "family": "rp2350", "name": "Pico 2", "manufacturer": "Raspberry Pi"}
    assert cpf.parse_board_page("no front matter") == {}


def test_boards_are_split_by_chip_and_need_a_uf2(tmp_path):
    opener, _ = site()
    cat = cpf.CircuitPythonCatalog(str(tmp_path), opener)
    boards, saved = cat.boards("rp2350")
    assert [b.id for b in boards] == ["raspberry_pi_pico2"] and saved is None
    assert boards[0].label == "Raspberry Pi Pico 2"
    # esp_only has no uf2 and not_listed has no entry
    assert [b.id for b in cat.boards("rp2040")[0]] == ["raspberry_pi_pico"]


def test_builds_are_stable_first_and_newest_first(tmp_path):
    opener, _ = site()
    cat = cpf.CircuitPythonCatalog(str(tmp_path), opener)
    builds, _ = cat.builds("raspberry_pi_pico2")
    assert [b.version for b in builds] == ["10.3.1", "9.2.9", "11.0.0-alpha.2"] and builds[2].preview
    assert builds[0].filename == "adafruit-circuitpython-raspberry_pi_pico2-en_US-10.3.1.uf2"
    assert builds[0].url.startswith("https://downloads.circuitpython.org/bin/raspberry_pi_pico2/en_US/")
    with pytest.raises(fw.FirmwareError):
        cat.builds("nope")


def test_the_saved_lists_are_used_offline(tmp_path):
    opener, _ = site()
    cpf.CircuitPythonCatalog(str(tmp_path), opener).boards("rp2350")
    boards, saved = cpf.CircuitPythonCatalog(str(tmp_path), site(down=True)[0]).boards("rp2350")
    assert saved and [b.id for b in boards] == ["raspberry_pi_pico2"]


def test_download_is_kept_and_not_asked_for_twice(tmp_path):
    opener, asked = site()
    cat = cpf.CircuitPythonCatalog(str(tmp_path), opener)
    build = cat.builds("raspberry_pi_pico2")[0][0]
    path = cat.download(build)
    assert open(path, "rb").read() == b"UF2 bytes"
    n = len(asked)
    assert cat.download(build) == path and len(asked) == n


def test_identify_accepts_circuitpythons_lowercase_machine_and_names_the_firmware(tmp_path):
    class Repl:
        def exec(self, code, timeout=10):
            return b"10.3.1\nRaspberry Pi Pico 2 with rp2350a\n"

        def close(self, reset=True):
            pass
    got = fw.identify("/dev/ttyACM9", "rp2350", open_board=lambda port: (Repl(), False), firmware="CircuitPython")
    assert got.release == "10.3.1"
    with pytest.raises(fw.FirmwareError, match="CircuitPython did not answer"):
        fw.identify("/dev/ttyACM9", "rp2350", timeout=0, firmware="CircuitPython",
                    open_board=lambda port: (_ for _ in ()).throw(OSError("silent")))


def test_wait_for_new_port_names_the_firmware():
    with pytest.raises(fw.FirmwareError, match="no CircuitPython serial port"):
        fw.wait_for_new_port(set(), timeout=0, ports=lambda: set(), firmware="CircuitPython")


def test_install_and_verify_for_circuitpython_waits_for_any_new_rp2_port(tmp_path, monkeypatch):
    order = []
    monkeypatch.setattr(fw, "rp2_any_ports", lambda: order.append("ports") or {"/dev/a"})
    monkeypatch.setattr(fw, "install", lambda *a: order.append("install"))

    def waited(before, ports, firmware, timeout):
        order.append((before, ports is fw.rp2_any_ports, firmware, timeout))
        return "/dev/b"
    monkeypatch.setattr(fw, "wait_for_new_port", waited)
    monkeypatch.setattr(fw, "identify", lambda port, chip, firmware: fw.Installed(port, "10.3.1", chip))
    drive = fw.BootDrive("/x", "m", "RPI-RP2", "rp2350")
    assert fw.install_and_verify("img", drive, firmware="CircuitPython").port == "/dev/b"
    assert order == ["ports", "install", ({"/dev/a"}, True, "CircuitPython", 60.0)]


def test_a_running_circuitpython_board_is_listed_as_circuitpython_not_micropython(monkeypatch):
    monkeypatch.setattr(fw, "rp2_any_ports", lambda: {"/dev/ttyACM3", "/dev/ttyACM4"})
    monkeypatch.setattr(fw, "rp2_ports", lambda: {"/dev/ttyACM4"})              # that one is MicroPython
    found = fw.detect_running_circuitpython()
    assert [(d.path, d.model, d.method) for d in found] == [("/dev/ttyACM3", "CircuitPython", "repl")]
    micro = fw.BootDrive("/dev/x", "MicroPython", "MicroPython", None, "repl")
    assert fw.is_circuitpython(found[0]) and not fw.is_circuitpython(micro)
    assert fw.device_label(found[0]) == "CircuitPython board at /dev/ttyACM3"
    assert ("State", "running CircuitPython") in fw.device_details(found[0])


def test_circuitpython_is_read_without_uctypes_and_sent_to_its_bootloader_its_own_way():
    sent = []

    class Repl:
        def exec(self, code, timeout=10):
            sent.append(code)
            return b"0\nRaspberry Pi Pico W with rp2040\n10.3.1\n"

        def close(self, reset=False):
            pass
    info = fw.read_running("/dev/ttyACM3", open_board=lambda port: (Repl(), False), circuitpython=True)
    assert (info.machine, info.release, info.flash_mb) == ("Raspberry Pi Pico W with rp2040", "10.3.1", None)
    assert "uctypes" not in sent[0]
    sent.clear()
    fw._bootloader_in_raw_repl("/dev/ttyACM3", lambda port: (Repl(), False), fw.BOOTLOADER_CODE_CIRCUITPYTHON)
    assert "RunMode.UF2" in sent[0] and "machine" not in sent[0]


def test_circuitpython_ports_include_adafruits_vendor_id_but_not_bootloaders_or_other_makers(monkeypatch):
    from types import SimpleNamespace as P
    ports = [P(device="/dev/pico2", vid=0x2E8A, pid=0x000B), P(device="/dev/picow", vid=0x239A, pid=0x8120),
             P(device="/dev/boot", vid=0x2E8A, pid=0x0003), P(device="/dev/badge", vid=0x16D0, pid=0x120E),
             P(device="/dev/uart", vid=None, pid=None)]
    import serial.tools.list_ports as lp
    monkeypatch.setattr(lp, "comports", lambda: ports)
    assert fw.rp2_any_ports() == {"/dev/pico2", "/dev/picow"}
    monkeypatch.setattr(fw, "rp2_ports", lambda: set())              # none of them is MicroPython
    assert [d.path for d in fw.detect_running_circuitpython()] == ["/dev/pico2", "/dev/picow"]
