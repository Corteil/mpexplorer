"""GTK window with the settings wired in. Needs PyGObject + GTK 3 and a display (xvfb-run in CI/locally);
skipped otherwise. Uses a temporary settings file and the fake board: never touches ~/.config or a real port."""
import json
import os
import sys
import time
import types

import pytest

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
    from mpexplorer.gtkui.prefs import PreferencesDialog
except (ImportError, ValueError) as e:                       # GTK 3 typelib missing
    unavailable("GTK 3 not available: %s" % e)

import fakedisplay
from fakedevice import FakeSerial
from mpexplorer.backends import DeviceBackend
from mpexplorer.settings import Settings
from mpexplorer.transport import RawREPL

Gtk = common.Gtk


def pump(ms=200):
    end = time.time() + ms / 1000
    while time.time() < end:
        while Gtk.events_pending():
            Gtk.main_iteration()
        time.sleep(0.005)


@pytest.fixture
def cfg(tmp_path):
    return str(tmp_path / "cfg" / "settings.json")


@pytest.fixture
def make_app(cfg):
    apps = []

    def make(**preset):
        s = Settings.load(cfg)
        for k, v in preset.items():
            s.set(k.replace("__", "."), v)
        app = App(settings=s)
        apps.append(app)
        pump()
        return app
    yield make
    for a in apps:
        a.destroy()
    pump(50)


def width_for(app, wanted):
    """GTK never makes a window narrower than its toolbar needs (that depends on the theme and fonts)."""
    return max(wanted, app.get_preferred_width()[0])


def saved(cfg):
    return json.load(open(cfg))


def test_defaults_on_first_start(make_app):
    app = make_app()
    assert app.show_hidden is False and app.left._sort == ("name", False)
    assert app.left.cwd == app.local.root and app.status.get_text() == "Ready"


def test_remembered_state_is_restored(make_app, tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    app = make_app(general__show_hidden=True, folders__host=str(folder), window__width=900, window__height=520,
                   general__sort__host=["size", True])
    assert app.show_hidden is True and app.hidden_item.get_active() is True
    assert app.left.cwd == str(folder)
    assert app.left._sort == ("size", True) and app.right._sort == ("name", False)
    assert tuple(app.get_size()) == (width_for(app, 900), 520)


def test_missing_host_folder_falls_back_to_home(make_app, tmp_path):
    app = make_app(folders__host=str(tmp_path / "gone"))
    assert app.left.cwd == app.local.root


def test_remember_switches_turn_restoring_off(make_app, tmp_path):
    (tmp_path / "w").mkdir()
    app = make_app(folders__host=str(tmp_path / "w"), general__remember_folders=False, window__width=900,
                   general__remember_window=False)
    assert app.left.cwd == app.local.root and tuple(app.get_size()) == (width_for(app, 1180), 680)


def test_changes_are_saved_when_the_window_closes(make_app, cfg, tmp_path):
    (tmp_path / "proj").mkdir()
    app = make_app()
    app.left.go(str(tmp_path / "proj"))
    pump()
    app.left.sort_by("size")
    app.hidden_item.set_active(True)
    wide = width_for(app, 950) + 60                           # wider than the minimum, so it is honoured
    app.resize(wide, 540)
    pump(300)
    app._quit()
    data = saved(cfg)
    assert data["folders"]["host"] == str(tmp_path / "proj")
    assert data["general"]["sort"]["host"] == ["size", False] and data["general"]["show_hidden"] is True
    assert (data["window"]["width"], data["window"]["height"]) == (wide, 540) and data["version"] == 1


def test_sort_and_hidden_are_saved_immediately(make_app, cfg):
    app = make_app()
    app.right.sort_by("modified")
    app._set_hidden(True)
    data = saved(cfg)                                          # no quit needed for these two
    assert data["general"]["sort"]["device"] == ["modified", False] and data["general"]["show_hidden"] is True


def test_device_folder_is_remembered_per_board(make_app, tmp_path):
    (tmp_path / "lib").mkdir()
    repl = RawREPL(FakeSerial())
    dev = DeviceBackend(repl)
    app = make_app()
    app.connected(dev, dev.root, "/dev/ttyACM0", "303a:4001:AAA")
    app.right.go(str(tmp_path / "lib"))
    pump()
    assert app.settings.get("general.last_port") == "/dev/ttyACM0"
    assert app.settings.get("folders.boards") == {"303a:4001:AAA": str(tmp_path / "lib")}
    app.disconnect()
    app.connected(dev, dev.root, "/dev/ttyACM1", "303a:4001:BBB")   # a different board has its own folder
    pump()
    assert app.settings.get("folders.boards")["303a:4001:AAA"] == str(tmp_path / "lib")
    assert "303a:4001:BBB" in app.settings.get("folders.boards")


def test_damaged_settings_file_does_not_stop_the_app(cfg):
    os.makedirs(os.path.dirname(cfg))
    open(cfg, "w").write("{ this is not json")
    app = App(settings=Settings.load(cfg))
    pump()
    assert "Settings:" in app.status.get_text() and os.path.exists(cfg + ".bad")
    app.destroy()


def test_preferences_dialog_changes_settings_live(make_app, cfg, monkeypatch):
    app = make_app()
    dlg = PreferencesDialog(app)
    assert dlg.checks["general.remember_port"].get_active() is True
    dlg.checks["general.show_hidden"].set_active(True)          # same effect as ticking it
    pump()
    assert app.show_hidden is True and app.hidden_item.get_active() is True
    assert saved(cfg)["general"]["show_hidden"] is True
    app.hidden_item.set_active(False)                           # the View menu changes the dialog's tick too
    assert dlg.checks["general.show_hidden"].get_active() is False
    dlg.checks["general.remember_folders"].set_active(False)
    assert saved(cfg)["general"]["remember_folders"] is False
    monkeypatch.setattr("mpexplorer.gtkui.prefs.ask_yes_no", lambda *a: True)
    monkeypatch.setattr("mpexplorer.gtkui.prefs.info", lambda *a: None)
    dlg._reset()
    assert dlg.checks["general.remember_folders"].get_active() is True
    assert app.settings.get("general.remember_folders") is True and "No problems" in dlg.problems.get_text()
    dlg.destroy()
    assert app.settings._listeners == [app._setting_changed]    # the dialog unsubscribed itself


def test_reset_is_cancelled_when_the_user_says_no(make_app, monkeypatch):
    app = make_app(general__show_hidden=True)
    dlg = PreferencesDialog(app)
    monkeypatch.setattr("mpexplorer.gtkui.prefs.ask_yes_no", lambda *a: False)
    dlg._reset()
    assert app.settings.get("general.show_hidden") is True
    dlg.destroy()


def test_device_pane_hides_modified_column_until_asked(make_app, tmp_path):
    (tmp_path / "a.txt").write_bytes(b"hello")
    app = make_app()
    app.connected(DeviceBackend(RawREPL(FakeSerial())), str(tmp_path), "/dev/ttyACM0", "303a:4001:AAA")
    pump()
    cols = app.right._cols
    assert not cols["modified"].get_visible() and app.left._cols["modified"].get_visible()
    assert app.right.entry("a.txt").size == 5 and app.right.entry("a.txt").mtime == 0
    app.settings.set("general.device_mtimes", True)
    pump()
    assert cols["modified"].get_visible() and app.right.entry("a.txt").mtime > 1_000_000_000


def test_clock_is_set_on_connect_only_when_asked(make_app, tmp_path, monkeypatch):
    calls = []
    machine = types.ModuleType("machine")
    machine.RTC = lambda: types.SimpleNamespace(datetime=lambda *a: calls.append(a[0]))
    monkeypatch.setitem(sys.modules, "machine", machine)
    app = make_app()
    app.connected(DeviceBackend(RawREPL(FakeSerial())), str(tmp_path), "/dev/ttyACM0", "303a:4001:AAA")
    pump()
    assert calls == []                                          # off by default
    app.disconnect()
    app.settings.set("general.set_board_clock", True)
    app.connected(DeviceBackend(RawREPL(FakeSerial())), str(tmp_path), "/dev/ttyACM0", "303a:4001:AAA")
    pump()
    assert len(calls) == 1 and calls[0][0] == time.gmtime().tm_year


def test_send_time_button_sets_the_board_clock(make_app, tmp_path, monkeypatch):
    calls = []
    machine = types.ModuleType("machine")
    machine.RTC = lambda: types.SimpleNamespace(datetime=lambda *a: calls.append(a[0]))
    monkeypatch.setitem(sys.modules, "machine", machine)
    app = make_app()
    app.connected(DeviceBackend(RawREPL(FakeSerial())), str(tmp_path), "/dev/ttyACM0", "303a:4001:AAA")
    pump()
    assert calls == []                                          # connecting alone does not touch the clock
    app.send_time_btn.clicked()
    pump()
    assert len(calls) == 1 and calls[0][0] == time.gmtime().tm_year
    assert "board clock set to" in app.status.get_text()


def test_toolbar_wraps_onto_a_second_row_when_narrow(make_app):
    app = make_app()
    bar = app.send_time_btn.get_parent().get_parent().get_parent()          # button > group > FlowBoxChild > FlowBox
    app.resize(1800, 600)
    pump(600)
    one_row = bar.get_allocated_height()
    app.resize(width_for(app, 900), 600)                                     # below the width of both groups together
    pump(600)
    assert bar.get_allocated_height() > one_row * 1.5


def test_disconnect_resets_the_board_only_when_the_setting_is_on(make_app, tmp_path):
    for on, resets in ((False, 0), (True, 1)):
        serial = FakeSerial()
        app = make_app(general__reset_on_disconnect=on)
        app.connected(DeviceBackend(RawREPL(serial)), str(tmp_path), "/dev/ttyACM0", "303a:4001:AAA")
        pump()
        app.disconnect()
        pump()
        assert serial.closed and serial.resets == resets


def _buttons(widget):
    found = []
    if isinstance(widget, Gtk.Button):
        found.append(widget)
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            found += _buttons(child)
    return found


def test_icon_buttons_all_have_hover_hints(make_app):
    app = make_app()
    for b in _buttons(app.get_children()[0]):
        assert b.get_tooltip_text(), "a button has no hover hint: label=%r" % b.get_label()


def test_icon_button_falls_back_to_text_when_the_theme_lacks_the_icon():
    b = common.icon_button("no-such-icon-name", "Do the thing", lambda: None, "Thing")
    assert b.get_label() == "Thing" and b.get_tooltip_text() == "Do the thing"


def test_connect_button_swaps_its_hint_with_the_state(make_app, tmp_path):
    app = make_app()
    assert app.connect_btn.get_tooltip_text() == "Connect to the board"
    app.connected(DeviceBackend(RawREPL(FakeSerial())), str(tmp_path), "/dev/ttyACM0", "303a:4001:AAA")
    pump()
    assert app.connect_btn.get_tooltip_text() == "Disconnect from the board"
    app.disconnect()
    pump()
    assert app.connect_btn.get_tooltip_text() == "Connect to the board"


def test_badge_screen_follows_the_connection(make_app, tmp_path, monkeypatch):
    rec = fakedisplay.install(monkeypatch)
    app = make_app(general__reset_on_disconnect=False)
    app.connected(DeviceBackend(RawREPL(FakeSerial())), str(tmp_path), "/dev/ttyACM0", "303a:4001:AAA")
    pump()
    assert "CONNECTED" in rec.all_text() and "Ready" in rec.all_text()
    app.runner.submit(lambda prog: None, label="Uploading main.py ...")            # a non-quiet job shows what it is
    pump()
    assert "Uploading main.py" in rec.all_text() and rec.last[-2] == "Done"
    app.disconnect()
    pump()
    assert "DISCONNECTED" in rec.last


def test_badge_screen_is_not_drawn_when_switched_off_or_when_the_board_restarts(make_app, tmp_path, monkeypatch):
    rec = fakedisplay.install(monkeypatch)
    app = make_app(general__badge_screen=False)
    app.connected(DeviceBackend(RawREPL(FakeSerial())), str(tmp_path), "/dev/ttyACM0", "303a:4001:AAA")
    pump()
    app.disconnect()
    pump()
    assert rec.frames == []
    app = make_app(general__reset_on_disconnect=True)
    app.connected(DeviceBackend(RawREPL(FakeSerial())), str(tmp_path), "/dev/ttyACM0", "303a:4001:AAA")
    pump()
    app.disconnect()                                                               # the badge restarts: no goodbye page
    pump()
    assert rec.frames and "DISCONNECTED" not in rec.all_text()


def test_transfer_progress_sits_above_the_transfer_information(make_app):
    import threading
    app = make_app()
    gate = threading.Event()

    def job(prog):
        prog.label, prog.total, prog.done = "main.py", 4096, 1024
        gate.wait(5)
    app.runner.submit(job, label="Uploading ...")
    pump(500)
    try:
        assert app.bar.get_fraction() == 0.25
        text = app.status.get_text()
        assert text.startswith("main.py") and "1.0 KB of 4.0 KB (25%)" in text and "/s" in text
        assert app.bar.get_allocation().y < app.status.get_allocation().y            # the bar is the upper row
        cancel = app.bar.get_parent().get_parent().get_children()[1]
        block = app.bar.get_parent().get_allocation()
        assert abs(cancel.get_allocation().height - block.height) <= 1                # Cancel as tall as bar + text
        assert cancel.get_allocation().y <= block.y + 1
    finally:
        gate.set()
    pump(500)
    assert app.bar.get_fraction() == 0
