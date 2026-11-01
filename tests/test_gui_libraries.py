"""Install libraries dialog against a host folder standing in for the board's /lib. Nothing real is written."""
import os
import sys
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
    from mpexplorer.gtkui import common, libraries as dialog
    from mpexplorer.gtkui.app import App
except (ImportError, ValueError) as e:
    unavailable("GTK 3 not available: %s" % e)

sys.path.insert(0, os.path.dirname(__file__))
from test_libraries import FILES, make_site  # noqa: E402

from mpexplorer import libraries as lib  # noqa: E402
from mpexplorer.backends import LocalBackend  # noqa: E402
from mpexplorer.settings import Settings  # noqa: E402

Gtk = common.Gtk


def pump(ms=100):
    end = time.time() + ms / 1000
    while time.time() < end:
        while Gtk.events_pending():
            Gtk.main_iteration()
        time.sleep(0.005)


def wait_until(test, ms=4000):
    end = time.time() + ms / 1000
    while not test() and time.time() < end:
        pump(20)
        app_poll()
    assert test()


APP = []


def app_poll():
    APP[0]._poll()


def open_dialog(tmp_path, monkeypatch, answer=True):
    monkeypatch.setattr(lib, "DEST", str(tmp_path / "board" / "lib"))
    asked = []
    monkeypatch.setattr(dialog, "ask_yes_no", lambda parent, title, text: asked.append(text) or answer)
    app = App(settings=Settings.load(str(tmp_path / "cfg.json")))
    APP[:] = [app]
    opener, _ = make_site()
    dlg = dialog.LibrariesDialog(app, LocalBackend(), "Test board", lib.Catalog(str(tmp_path / "cache"), opener))
    wait_until(lambda: len(dlg.store) == 2 and dlg.job is None)
    return app, dlg, asked


def test_lists_the_libraries_and_filters_by_search(tmp_path, monkeypatch):
    app, dlg, _ = open_dialog(tmp_path, monkeypatch)
    try:
        assert [r[1] for r in dlg.store] == ["alpha", "zed"] and not dlg.install_btn.get_sensitive()
        dlg.search.set_text("first")
        wait_until(lambda: [r[1] for r in dlg.filter] == ["alpha"])      # the entry waits a moment after typing
        assert [r[1] for r in dlg.filter] == ["alpha"]
    finally:
        dlg.destroy()
        app.destroy()


def test_installs_with_dependencies_after_asking(tmp_path, monkeypatch):
    app, dlg, asked = open_dialog(tmp_path, monkeypatch)
    try:
        dlg.store[1][0] = True                          # zed, which needs alpha
        dlg._sensitive()
        assert dlg.install_btn.get_sensitive()
        dlg.install(dlg.ticked())
        wait_until(lambda: not dlg.busy and "Done" in dlg.note.get_text())
        assert "alpha" in asked[0] and "zed" in asked[0] and "3 file(s)" in asked[0]
        assert (tmp_path / "board" / "lib" / "pkg" / "mod.py").read_bytes() == FILES["pkg/mod.py"]
        assert "3 written, 0 already there" in dlg.note.get_text()
        # /lib was read again (the fake packages are not named like their files)
        wait_until(lambda: dlg.have == {"a", "pkg"})
    finally:
        dlg.destroy()
        app.destroy()


def test_saying_no_writes_nothing(tmp_path, monkeypatch):
    app, dlg, asked = open_dialog(tmp_path, monkeypatch, answer=False)
    try:
        dlg.install(["zed"])
        wait_until(lambda: asked and not dlg.busy)
        assert dlg.note.get_text() == "Nothing written." and not (tmp_path / "board").exists()
    finally:
        dlg.destroy()
        app.destroy()


def test_circuitpython_needs_install_anyway_but_micropython_does_not(tmp_path, monkeypatch):
    app, dlg, asked = open_dialog(tmp_path, monkeypatch)
    try:
        dlg.store[1][0] = True
        dlg._sensitive()
        assert dlg.install_btn.get_sensitive() and dlg.remove_btn.get_sensitive() and not dlg.force.get_visible()
        dlg.firmware = "circuitpython"
        dlg._sensitive()
        assert not dlg.install_btn.get_sensitive() and dlg.remove_btn.get_sensitive()
        dlg.install(["zed"])                                    # refused without the override
        pump(100)
        assert not asked and not dlg.busy
        dlg.force.set_active(True)
        dlg.mpy = None                                          # what the dialog sets for CircuitPython: source files
        assert dlg.install_btn.get_sensitive()
        dlg.install(["zed"])
        wait_until(lambda: "Done" in dlg.note.get_text())
    finally:
        dlg.destroy()
        app.destroy()


def test_remove_asks_then_deletes(tmp_path, monkeypatch):
    app, dlg, asked = open_dialog(tmp_path, monkeypatch)
    try:
        dlg.install(["zed"])
        wait_until(lambda: "Done" in dlg.note.get_text())
        dlg.remove(["alpha"])
        wait_until(lambda: "Removed 2" in dlg.note.get_text())
        assert "alpha" in asked[-1] and not (tmp_path / "board" / "lib" / "pkg").exists()
        assert (tmp_path / "board" / "lib" / "a.py").exists()
    finally:
        dlg.destroy()
        app.destroy()


def test_a_circuitpython_board_shows_its_drive_and_is_labelled(tmp_path, monkeypatch):
    from mpexplorer.circuitpython import DriveBackend
    app = App(settings=Settings.load(str(tmp_path / "cfg.json")))
    mount = tmp_path / "CIRCUITPY"
    (mount / "lib").mkdir(parents=True)
    (mount / "code.py").write_text("print(1)\n")
    try:
        app.connected(DriveBackend(str(mount), types.SimpleNamespace(exec=lambda *a, **k: b"", close=lambda *a: None)),
                      str(mount), "/dev/ttyACM9", "usb:xyz", "Raspberry Pi Pico 2 with rp2350a", pane=app.right,
                      firmware=("circuitpython", "10.3.1"))
        wait_until(lambda: {e.name for e in app.right._entries} >= {"code.py", "lib"})
        assert "CircuitPython 10.3.1" in app.right.detail and "Pico 2" in app.right.detail
        assert app.conn.badge is None
    finally:
        app.conns.clear()
        app.destroy()


def open_bundle_dialog(tmp_path, monkeypatch):
    from test_cpbundle import site
    from mpexplorer import cpbundle
    monkeypatch.setattr(lib, "DEST", str(tmp_path / "board" / "lib"))
    asked = []
    monkeypatch.setattr(dialog, "ask_yes_no", lambda parent, title, text: asked.append(text) or True)
    app = App(settings=Settings.load(str(tmp_path / "cfg.json")))
    APP[:] = [app]
    opener, _ = make_site()
    bopener, _ = site()
    dlg = dialog.LibrariesDialog(app, LocalBackend(), "CP board", lib.Catalog(str(tmp_path / "cache"), opener),
                                 lambda key: cpbundle.Catalog(key, str(tmp_path / "bundles"), bopener))
    wait_until(lambda: len(dlg.store) == 2 and dlg.job is None)
    return app, dlg, asked


def test_a_circuitpython_board_starts_on_the_adafruit_bundle_and_needs_no_override(tmp_path, monkeypatch):
    app, dlg, asked = open_bundle_dialog(tmp_path, monkeypatch)
    try:
        dlg._board_read((6, set(), ("circuitpython", "10.3.1")), None)      # what reading a CircuitPython board gives
        assert dlg.source == "adafruit"
        wait_until(lambda: len(dlg.store) == 3)
        assert [r[1] for r in dlg.store][0] == "adafruit_bus_device" and not dlg.mismatch()
        assert not dlg.force.get_visible() and dlg.resolve_arg() == 10
        dlg.store[1][0] = True                                  # adafruit_thing, needs adafruit_bus_device
        dlg._sensitive()
        assert dlg.install_btn.get_sensitive()
        dlg.install(dlg.ticked())
        wait_until(lambda: "Done" in dlg.note.get_text())
        assert "adafruit_bus_device" in asked[0] and ".mpy 10" in asked[0]
        assert (tmp_path / "board" / "lib" / "adafruit_thing.mpy").read_bytes() == b"thing 10.x-mpy"
    finally:
        dlg.destroy()
        app.destroy()


def test_the_other_firmwares_list_needs_the_override(tmp_path, monkeypatch):
    app, dlg, asked = open_bundle_dialog(tmp_path, monkeypatch)
    try:
        # a CircuitPython board with micropython-lib picked
        dlg.firmware, dlg.version, dlg.chose = "circuitpython", "10.3.1", True
        dlg.store[1][0] = True
        dlg._sensitive()
        assert dlg.mismatch() and dlg.resolve_arg() is None and not dlg.install_btn.get_sensitive()
        dlg._mismatch_note()
        text = dlg.note.get_text()
        assert dlg.force.get_visible() and "CircuitPython" in text and "MicroPython" in text
        dlg.firmware, dlg.version = "micropython", "1.28.0"      # a MicroPython board, CircuitPython bundle
        dlg.source_box.set_active_id("adafruit")
        wait_until(lambda: len(dlg.store) == 3)
        assert dlg.mismatch() and dlg.resolve_arg() is None
    finally:
        dlg.destroy()
        app.destroy()


def test_open_with_on_a_circuitpython_drive_edits_the_file_where_it_is(tmp_path):
    from mpexplorer.circuitpython import DriveBackend
    app = App(settings=Settings.load(str(tmp_path / "cfg.json")))
    mount = tmp_path / "CIRCUITPY"
    mount.mkdir()
    (mount / "code.py").write_text("print(1)\n")
    launched, copied = [], []
    app.edits.launch = lambda argv: launched.append(argv)
    app.edits.edit_device_file = lambda *a: copied.append(a)             # the copy-to-cache path must not be used
    try:
        be = DriveBackend(str(mount), types.SimpleNamespace(exec=lambda *a, **k: b"", close=lambda *a: None))
        app.connected(be, str(mount), "/dev/ttyACM9", "usb:xyz", pane=app.right, firmware=("circuitpython", "10.3.1"))
        app.open_external(app.right, str(mount / "code.py"), "geany {file}")
        assert launched == [["geany", str(mount / "code.py")]] and copied == []
    finally:
        app.conns.clear()
        app.destroy()


def test_check_for_updates_marks_the_libraries_that_differ(tmp_path, monkeypatch):
    app, dlg, _ = open_dialog(tmp_path, monkeypatch)
    try:
        assert not dlg.check_btn.get_sensitive()                       # nothing is installed yet
        dlg.install(["zed"])
        wait_until(lambda: "Done" in dlg.note.get_text())
        dlg.have = {"a", "pkg"}
        dlg._mark_installed()
        # zed's file is 'a.py'; the fake packages are named differently, so name the rows by what is installed
        for row in dlg.store:
            row[3] = "yes"
        (tmp_path / "board" / "lib" / "a.py").write_text("edited on the board")
        dlg._sensitive()
        assert dlg.check_btn.get_sensitive()
        dlg.check_updates()
        wait_until(lambda: "checked" in dlg.note.get_text())
        states = {r[1]: r[3] for r in dlg.store}
        assert states == {"alpha": "up to date", "zed": "update available"}
        assert "1 to update" in dlg.note.get_text()
    finally:
        dlg.destroy()
        app.destroy()


def test_show_filter_and_select_installed_switch(tmp_path, monkeypatch):
    app, dlg, _ = open_dialog(tmp_path, monkeypatch)
    try:
        assert not dlg.select_installed.get_sensitive()                    # nothing installed: nothing to select
        dlg.store[0][3], dlg.store[1][3] = "update available", ""          # alpha is installed and old, zed is not
        dlg._sensitive()
        names = lambda: [r[1] for r in dlg.filter]                         # noqa: E731
        assert names() == ["alpha", "zed"]
        for key, shown in (("installed", ["alpha"]), ("new", ["zed"]), ("updates", ["alpha"]),
                           ("all", ["alpha", "zed"])):
            dlg.show_box.set_active_id(key)
            assert names() == shown, key
        dlg.select_installed.set_active(True)
        assert dlg.ticked() == ["alpha"] and dlg.install_btn.get_sensitive()
        dlg.select_installed.set_active(False)
        assert dlg.ticked() == []
        dlg.show_box.set_active_id("new")                                  # only the installed ones that are shown
        dlg.select_installed.set_active(True)
        assert dlg.ticked() == []
    finally:
        dlg.destroy()
        app.destroy()


def test_the_count_says_how_many_are_shown_when_something_is_hidden(tmp_path, monkeypatch):
    app, dlg, _ = open_dialog(tmp_path, monkeypatch)
    try:
        assert dlg.count.get_text() == "2 libraries"
        dlg.store[0][3] = "yes"
        dlg.show_box.set_active_id("installed")
        assert dlg.count.get_text() == "1 of 2 shown"
        dlg.show_box.set_active_id("all")
        dlg.search.set_text("first")
        wait_until(lambda: dlg.count.get_text() == "1 of 2 shown")
        dlg.search.set_text("")
        wait_until(lambda: dlg.count.get_text() == "2 libraries")
    finally:
        dlg.destroy()
        app.destroy()
