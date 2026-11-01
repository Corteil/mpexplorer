"""Install libraries dialog against a host folder standing in for the board's /lib. Nothing real is written."""
import os
import sys
import time

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
