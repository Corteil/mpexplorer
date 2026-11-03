"""The seams the Android app plugs into (docs/ANDROID_PLAN.md, Phase 1): serial opener, port lister, host folder."""
from types import SimpleNamespace as P

from fakedevice import FakeSerial
from mpexplorer import ports
from mpexplorer.backends import DeviceBackend, LocalBackend
from mpexplorer.settings import Settings
from mpexplorer.transport import RawREPL

PICO = P(device="usb:1", vid=0x2E8A, pid=0x0005, serial_number="e66118604b992a35", manufacturer="MicroPython",
         product="Board in FS mode", description="Board CDC")
BADGE = P(device="usb:2", vid=0x303A, pid=0x4001, serial_number="9842", manufacturer="EMF", product="TiLDAGON",
          description="Board CDC")
UART = P(device="/dev/ttyAMA0", vid=None, pid=None, serial_number=None, manufacturer=None, product=None,
         description="n/a")


def test_open_uses_the_given_opener_and_passes_port_baud_and_rts():
    seen = []

    def opener(port, baud, rts):
        seen.append((port, baud, rts))
        return FakeSerial()

    repl = RawREPL.open("usb:1", baud=9600, rts=True, opener=opener)
    assert seen == [("usb:1", 9600, True)]
    assert isinstance(repl.ser, FakeSerial)


def test_a_board_opened_through_an_opener_works_end_to_end():
    repl, rts = RawREPL.open_board("usb:1", opener=lambda port, baud, rts: FakeSerial())
    assert rts is False
    dev = DeviceBackend(repl)
    assert dev.stat("/") is not None
    repl.close()


def test_a_silent_board_is_retried_with_the_other_rts_setting():
    tried = []

    class Silent:
        in_waiting = 0

        def read(self, n=1):
            return b""

        def write(self, data):
            return len(data)

        def flush(self):
            pass

        def close(self):
            pass

    def opener(port, baud, rts):
        tried.append(rts)
        return FakeSerial() if rts else Silent()

    repl, used = RawREPL.open_board("usb:1", opener=opener)
    assert tried == [False, True] and used is True
    repl.close()


def test_the_port_list_comes_from_the_given_lister(tmp_path):
    def lister():
        return [BADGE, UART, PICO]

    assert ports.list_ports(lister=lister) == [
        "usb:1  Pico / Pico 2  #2a35",              # RP2 first, the UART without USB ids left out
        "usb:2  EMF badge  #9842",
    ]
    entries = ports.list_boards(Settings(str(tmp_path / "s.json")), lister=lister)
    assert [e.device for e in entries] == ["usb:1", "usb:2"]
    assert all(ports.device_of(e.text) == e.device for e in entries)


def test_board_id_uses_the_lister_and_falls_back_to_the_device_name():
    def lister():
        return [PICO, UART]

    assert ports.board_id("usb:1", lister=lister) == "2e8a:0005:e66118604b992a35"
    assert ports.board_id("/dev/ttyAMA0", lister=lister) == "/dev/ttyAMA0"
    assert ports.board_id("usb:9", lister=lister) == "usb:9"


def test_local_backend_root_can_be_the_apps_folder(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    be = LocalBackend(str(tmp_path))
    assert be.root == str(tmp_path)
    assert [e.name for e in be.listdir(be.root)] == ["a.txt"]
    assert LocalBackend().root       # the default is still the home folder
