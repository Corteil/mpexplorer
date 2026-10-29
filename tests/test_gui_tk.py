"""Tk window with the settings wired in. Needs tkinter and a display (xvfb-run); skipped otherwise.
Uses a temporary settings file and the fake board: never touches ~/.config or a real port."""
import json
import os
import sys
import time
import types

import pytest

from fakedevice import FakeSerial
from guiskip import unavailable
from mpexplorer.backends import DeviceBackend
from mpexplorer.settings import Settings
from mpexplorer.transport import RawREPL

if not os.environ.get("DISPLAY"):
    unavailable("no display (run under xvfb-run)")
try:
    import tkinter as tk

    from mpexplorer.gui.app import App
    from mpexplorer.gui.prefs import PreferencesDialog
except (ImportError, ModuleNotFoundError) as e:
    unavailable("Tk not usable: %s" % e)


def pump(app, ms=400):
    """Let Tk run: the job poller fires every 100 ms, so board listings arrive a little later."""
    end = time.time() + ms / 1000
    while time.time() < end:
        app.update()
        time.sleep(0.01)


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
        app.update()
        return app
    yield make
    for a in apps:
        try:
            a.destroy()
        except tk.TclError:
            pass


def saved(cfg):
    return json.load(open(cfg))


def test_defaults_on_first_start(make_app):
    app = make_app()
    assert app.show_hidden.get() is False and app.left._sort == ("name", False)
    assert app.left.cwd == app.local.root


def test_remembered_state_is_restored(make_app, tmp_path):
    (tmp_path / "work").mkdir()
    app = make_app(general__show_hidden=True, folders__host=str(tmp_path / "work"), window__width=1300,
                   window__height=520, general__sort__host=["size", True])
    assert app.show_hidden.get() is True and app.left.cwd == str(tmp_path / "work")
    assert app.left._sort == ("size", True) and app.right._sort == ("name", False)
    assert (app.winfo_width(), app.winfo_height()) == (1300, 520)


def test_remember_switches_turn_restoring_off(make_app, tmp_path):
    (tmp_path / "w").mkdir()
    app = make_app(folders__host=str(tmp_path / "w"), general__remember_folders=False, window__width=1300,
                   general__remember_window=False)
    assert app.left.cwd == app.local.root and app.winfo_width() != 1300


def test_changes_are_saved_when_the_window_closes(make_app, cfg, tmp_path):
    (tmp_path / "proj").mkdir()
    app = make_app()
    app.left.go(str(tmp_path / "proj"))
    app.update()
    app.left.sort_by("size")
    app.show_hidden.set(True)
    app.geometry("1333x555")
    app.update()
    app._quit()
    data = saved(cfg)
    assert data["folders"]["host"] == str(tmp_path / "proj")
    assert data["general"]["sort"]["host"] == ["size", False] and data["general"]["show_hidden"] is True
    assert (data["window"]["width"], data["window"]["height"]) == (1333, 555)


def test_device_folder_is_remembered_per_board(make_app, tmp_path):
    (tmp_path / "lib").mkdir()
    dev = DeviceBackend(RawREPL(FakeSerial()))
    app = make_app()
    app.connected(dev, dev.root, "/dev/ttyACM0", "303a:4001:AAA")
    app.right.go(str(tmp_path / "lib"))
    pump(app)
    assert app.settings.get("general.last_port") == "/dev/ttyACM0"
    assert app.settings.get("folders.boards") == {"303a:4001:AAA": str(tmp_path / "lib")}


def test_damaged_settings_file_does_not_stop_the_app(cfg):
    os.makedirs(os.path.dirname(cfg))
    open(cfg, "w").write("{ nope")
    app = App(settings=Settings.load(cfg))
    app.update()
    assert "Settings:" in app.status.cget("text") and os.path.exists(cfg + ".bad")
    app.destroy()


def test_preferences_dialog_changes_settings_live(make_app, cfg, monkeypatch):
    app = make_app()
    dlg = PreferencesDialog(app)
    dlg.update()
    dlg.vars["general.show_hidden"].set(True)
    dlg._toggled("general.show_hidden")
    assert app.show_hidden.get() is True and saved(cfg)["general"]["show_hidden"] is True
    app.show_hidden.set(False)                                  # the View menu changes the dialog's tick too
    assert dlg.vars["general.show_hidden"].get() is False
    monkeypatch.setattr("mpexplorer.gui.prefs.messagebox.askyesno", lambda *a, **k: True)
    monkeypatch.setattr("mpexplorer.gui.prefs.messagebox.showinfo", lambda *a, **k: None)
    dlg.vars["general.remember_port"].set(False)
    dlg._toggled("general.remember_port")
    dlg._reset()
    assert app.settings.get("general.remember_port") is True and dlg.vars["general.remember_port"].get() is True
    dlg.destroy()
    app.update()
    assert app.settings._listeners == [app._setting_changed]


def test_device_pane_hides_modified_column_until_asked(make_app, tmp_path):
    (tmp_path / "a.txt").write_bytes(b"hello")
    app = make_app()
    app.connected(DeviceBackend(RawREPL(FakeSerial())), str(tmp_path), "/dev/ttyACM0", "303a:4001:AAA")
    pump(app)
    assert tuple(app.right.tree["displaycolumns"]) == ("size",)
    assert tuple(app.left.tree["displaycolumns"]) == ("size", "modified")
    assert app.right.entry("a.txt").size == 5 and app.right.entry("a.txt").mtime == 0
    app.settings.set("general.device_mtimes", True)
    pump(app)
    assert tuple(app.right.tree["displaycolumns"]) == ("size", "modified")
    assert app.right.entry("a.txt").mtime > 1_000_000_000


def test_clock_is_set_on_connect_only_when_asked(make_app, tmp_path, monkeypatch):
    calls = []
    machine = types.ModuleType("machine")
    machine.RTC = lambda: types.SimpleNamespace(datetime=lambda *a: calls.append(a[0]))
    monkeypatch.setitem(sys.modules, "machine", machine)
    app = make_app()
    app.connected(DeviceBackend(RawREPL(FakeSerial())), str(tmp_path), "/dev/ttyACM0", "303a:4001:AAA")
    pump(app)
    assert calls == []                                          # off by default
    app.disconnect()
    app.settings.set("general.set_board_clock", True)
    app.connected(DeviceBackend(RawREPL(FakeSerial())), str(tmp_path), "/dev/ttyACM0", "303a:4001:AAA")
    pump(app)
    assert len(calls) == 1 and calls[0][0] == time.localtime().tm_year
