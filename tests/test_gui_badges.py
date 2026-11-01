"""The CP / MP firmware badges in the source box and beside it (docs/BOARD_LABELS_PLAN.md, phase 2)."""
import os
import time
import types

from guiskip import unavailable

try:
    import gi  # noqa: F401
except ImportError:
    unavailable("PyGObject (python3-gi) is not installed")
if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
    unavailable("no display (run under xvfb-run)")
try:
    from mpexplorer.gtkui import common
    from mpexplorer.gtkui.app import App
    from mpexplorer.gtkui.pane import HOST
except (ImportError, ValueError) as e:
    unavailable("GTK 3 not available: %s" % e)

from mpexplorer import boardinfo as bi  # noqa: E402
from mpexplorer.circuitpython import DriveBackend  # noqa: E402
from mpexplorer.ports import Entry  # noqa: E402
from mpexplorer.settings import Settings  # noqa: E402

Gtk = common.Gtk
ENTRIES = [Entry("/dev/ttyACM2", "/dev/ttyACM2  Pico / Pico 2  #9842", bi.MICROPYTHON, ""),
           Entry("/dev/ttyACM4", "/dev/ttyACM4  Pico 2  #8a64", bi.CIRCUITPYTHON, ""),
           Entry("/dev/ttyACM6", "/dev/ttyACM6  Mystery  #0001", None, "")]


def pump(ms=100):
    end = time.time() + ms / 1000
    while time.time() < end:
        while Gtk.events_pending():
            Gtk.main_iteration()
        time.sleep(0.005)


def make(tmp_path, monkeypatch):
    monkeypatch.setattr("mpexplorer.gtkui.app.list_boards", lambda settings: list(ENTRIES))
    app = App(settings=Settings.load(str(tmp_path / "cfg.json")))
    pump()
    return app


def badge(pane):
    ctx = pane.badge.get_style_context()
    classes = [c for c in ("fw-mp", "fw-cp", "fw-unknown") if ctx.has_class(c)]
    return pane.badge.get_visible(), pane.badge.get_text(), classes


def test_the_badge_follows_the_chosen_port(tmp_path, monkeypatch):
    app = make(tmp_path, monkeypatch)
    try:
        pane = app.right
        pane.source_box.get_child().set_text(HOST)
        assert not pane.badge.get_visible()                                  # Host is not a board
        for text, expected in ((ENTRIES[0].text, (True, "MP", ["fw-mp"])), (ENTRIES[1].text, (True, "CP", ["fw-cp"])),
                               (ENTRIES[2].text, (True, "??", ["fw-unknown"]))):
            pane.source_box.get_child().set_text(text)
            assert badge(pane) == expected, text
        pane.source_box.get_child().set_text(HOST)
        assert not pane.badge.get_visible()
    finally:
        app.destroy()


def test_the_drop_down_rows_carry_a_badge_cell_except_host(tmp_path, monkeypatch):
    app = make(tmp_path, monkeypatch)
    try:
        pane = app.right
        rows = {row[0]: pane.source_box.get_model().get_iter(i) for i, row in enumerate(pane.source_box.get_model())}
        model, cell = pane.source_box.get_model(), pane.badge_cell

        def cell_for(text):
            pane._badge_data(None, cell, model, rows[text])
            return cell.get_property("text"), cell.get_property("background-set")
        assert cell_for(HOST) == ("", False)
        assert cell_for(ENTRIES[0].text) == ("MP", True) and cell_for(ENTRIES[1].text) == ("CP", True)
        assert cell_for(ENTRIES[2].text) == ("??", True)
    finally:
        app.destroy()


def test_connecting_teaches_the_badge_and_the_label_names_the_firmware(tmp_path, monkeypatch):
    app = make(tmp_path, monkeypatch)
    mount = tmp_path / "CIRCUITPY"
    mount.mkdir()
    try:
        pane = app.right
        pane.source_box.get_child().set_text(ENTRIES[2].text)             # guessed unknown
        assert badge(pane)[1] == "??"
        be = DriveBackend(str(mount), types.SimpleNamespace(exec=lambda *a, **k: b"", close=lambda *a: None))
        app.connected(be, str(mount), "/dev/ttyACM6", "chip:abcd", "Raspberry Pi Pico 2 with rp2350a",
                      usb="2e8a:000b:559CBFCF6BBE8A64", pane=pane, firmware=("circuitpython", "10.3.1"))
        pane.source_box.get_child().set_text(ENTRIES[2].text)
        assert badge(pane) == (True, "CP", ["fw-cp"]) and "CircuitPython 10.3.1" in pane.detail
        learned = app.settings.get("boards.firmware")
        assert learned == {"2e8a:000b:559CBFCF6BBE8A64": "circuitpython", "chip:abcd": "circuitpython"}
        assert app.settings.get("boards.ids") == {"2e8a:000b:559CBFCF6BBE8A64": "chip:abcd"}
        app.conns.clear()
        pane.clear()
        assert pane.live_kind is None and badge(pane)[1] == "??"                    # back to the scan's guess
    finally:
        app.conns.clear()
        app.destroy()


def test_a_micropython_board_is_named_too_and_a_shared_serial_is_not_learned_by_usb(tmp_path, monkeypatch):
    app = make(tmp_path, monkeypatch)
    try:
        be = DriveBackend(str(tmp_path), types.SimpleNamespace(exec=lambda *a, **k: b"", close=lambda *a: None))
        app.connected(be, str(tmp_path), "/dev/ttyACM5", "chip:1111", "Raspberry Pi Pico2 with RP2350",
                      usb="2e8a:0005:000000000000", pane=app.right, firmware=("micropython", "1.29.0"))
        assert "MicroPython 1.29.0" in app.right.detail
        assert app.settings.get("boards.firmware") == {"chip:1111": "micropython"}      # not under the shared USB key
        assert app.settings.get("boards.ids") == {}
    finally:
        app.conns.clear()
        app.destroy()


# -- nicknames (docs/BOARD_LABELS_PLAN.md, phase 3) ------------------------------------------------------------
def connect_cp(app, tmp_path, pane=None, board="chip:559cbfcf", usb="2e8a:000b:559CBFCF6BBE8A64", port="/dev/ttyACM4"):
    mount = tmp_path / "CIRCUITPY"
    mount.mkdir(exist_ok=True)
    be = DriveBackend(str(mount), types.SimpleNamespace(exec=lambda *a, **k: b"", close=lambda *a: None))
    app.connected(be, str(mount), port, board, "Raspberry Pi Pico 2 with rp2350a", usb=usb, pane=pane or app.right,
                  firmware=("circuitpython", "10.3.1"))
    return (pane or app.right)


def ask(monkeypatch, answers, confirm=True):
    from mpexplorer.gtkui import app as appmod
    shown = []
    monkeypatch.setattr(appmod, "prompt", lambda parent, title, label, initial="", allow_empty=False:
                        shown.append((label, initial)) or answers.pop(0))
    monkeypatch.setattr(appmod, "ask_yes_no", lambda *a: confirm)
    monkeypatch.setattr(appmod, "info", lambda parent, title, text: shown.append(("info", text)))
    return shown


def test_naming_a_board_updates_every_label_and_the_port_list(tmp_path, monkeypatch):
    app = make(tmp_path, monkeypatch)
    try:
        pane = connect_cp(app, tmp_path)
        assert pane.name_btn.get_sensitive() and '"' not in pane.detail
        shown = ask(monkeypatch, ["  Bench\tCP  "])
        app.name_board(pane)
        assert app.settings.get("boards.nicknames") == {"chip:559cbfcf": "Bench CP"}
        assert pane.detail == '/dev/ttyACM4  "Bench CP"  Pico 2 (rp2350a)  CircuitPython 10.3.1'
        assert app.conn.label == pane.detail and 'Named /dev/ttyACM4 "Bench CP"' in app.status.get_text()
        assert "up to 24 characters" in shown[0][0] and shown[0][1] == ""
        assert app.settings.get("boards.ids") == {"2e8a:000b:559CBFCF6BBE8A64": "chip:559cbfcf"}
    finally:
        app.conns.clear()
        app.destroy()


def test_a_name_survives_reconnecting_and_shows_in_the_port_list(tmp_path, monkeypatch):
    app = make(tmp_path, monkeypatch)
    try:
        from mpexplorer import ports
        pane = connect_cp(app, tmp_path)
        ask(monkeypatch, ["Bench CP"])
        app.name_board(pane)
        app.conns.clear()
        pane.clear()
        listed = ports.Entry("/dev/ttyACM4", '/dev/ttyACM4  "Bench CP"  Pico 2  #8a64', bi.CIRCUITPYTHON, "Bench CP")
        monkeypatch.setattr("mpexplorer.gtkui.app.list_boards", lambda settings: [listed])
        app.scan_ports()
        assert listed.text in app._ports
        pane = connect_cp(app, tmp_path)                                                      # a later session
        assert '"Bench CP"' in pane.detail and 'Connected to "Bench CP"' in app.status.get_text()
    finally:
        app.conns.clear()
        app.destroy()


def test_the_source_box_keeps_pointing_at_the_renamed_port(tmp_path, monkeypatch):
    app = make(tmp_path, monkeypatch)
    try:
        from mpexplorer import ports
        app.right.source_box.get_child().set_text(ENTRIES[1].text)                 # what the owner picked to connect
        pane = connect_cp(app, tmp_path)
        before = pane.source()
        renamed = [ports.Entry("/dev/ttyACM4", '/dev/ttyACM4  "Bench CP"  Pico 2  #8a64', bi.CIRCUITPYTHON,
                               "Bench CP")]
        monkeypatch.setattr("mpexplorer.gtkui.app.list_boards", lambda settings: list(renamed))
        ask(monkeypatch, ["Bench CP"])
        app.name_board(pane)
        assert pane.source() == renamed[0].text != before and badge(pane)[1] == "CP"
    finally:
        app.conns.clear()
        app.destroy()


def test_clearing_a_name_and_the_duplicate_and_shared_serial_cases(tmp_path, monkeypatch):
    app = make(tmp_path, monkeypatch)
    try:
        pane = connect_cp(app, tmp_path)
        bi.set_nickname(app.settings, "chip:other", "Bench CP")                               # another board has it
        shown = ask(monkeypatch, ["bench cp"], confirm=False)
        app.name_board(pane)                                                                  # refused at the question
        assert bi.nickname_of(app.settings, "chip:559cbfcf") == ""
        ask(monkeypatch, ["Bench CP", ""])
        app.name_board(pane)                                                                  # accepted: both may share
        assert bi.nickname_of(app.settings, "chip:559cbfcf") == "Bench CP"
        app.name_board(pane)                                                                  # then removed with ''
        assert bi.nickname_of(app.settings, "chip:559cbfcf") == "" and '"' not in pane.detail
        app.conns.clear()
        pane.clear()
        assert not pane.name_btn.get_sensitive()
        shown = ask(monkeypatch, [])
        app.name_board(pane)                                                                  # nothing connected
        assert shown and shown[0][0] == "info" and "Connect to the board first" in shown[0][1]
        pane = connect_cp(app, tmp_path, board="2e8a:0005:000000000000", usb="2e8a:0005:000000000000")
        shown = ask(monkeypatch, [None])
        app.name_board(pane)                                                       # no chip id, shared serial
        assert "would apply to every board" in shown[0][0]
    finally:
        app.conns.clear()
        app.destroy()
