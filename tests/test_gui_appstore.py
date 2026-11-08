"""Tools > Badge apps against a host folder standing in for the badge and a fake app store (APP_STORE_PLAN.md phase 2).
Nothing real is downloaded or written."""
import json
import os
import sys
import time

from guiskip import unavailable

try:
    import gi  # noqa: F401
except ImportError:
    unavailable("PyGObject (python3-gi) is not installed")
if os.name != "nt" and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
    unavailable("no display (run under xvfb-run)")
try:
    from mpexplorer.gtkui import appstore as dialog, common
    from mpexplorer.gtkui.app import App
except (ImportError, ValueError) as e:
    unavailable("GTK 3 not available: %s" % e)

sys.path.insert(0, os.path.dirname(__file__))
from test_appstore import APP_PY, entry, tarball, web_for  # noqa: E402

from mpexplorer import appstore  # noqa: E402
from mpexplorer.backends import LocalBackend  # noqa: E402
from mpexplorer.settings import Settings  # noqa: E402

Gtk = common.Gtk
APP = []


def pump(ms=100):
    end = time.time() + ms / 1000
    while time.time() < end:
        while Gtk.events_pending():
            Gtk.main_iteration()
        time.sleep(0.005)


def wait_until(test, ms=5000):
    end = time.time() + ms / 1000
    while not test() and time.time() < end:
        pump(20)
        APP[0]._poll()
    assert test()


class Badge(LocalBackend):
    """A host folder as the badge, answering ota.get_version() like Tildagon OS."""
    os_version = "v2.2.0"

    def _run(self, code, timeout=10.0):
        if "ota.get_version()" in code and self.os_version:
            return (self.os_version + "\r\n").encode()
        raise RuntimeError("ImportError: no module named 'ota'")

    def free_space(self, p):
        return 1 << 20


NEAT = (entry(code="11111111", title="neat-app", version="1.1", category="Games"),
        tarball({"app.py": APP_PY}))
CLOCK = (entry(code="22222222", owner="Corteil", title="tildagon-gps-clock", version="3", category="Apps"),
         tarball({"app.py": APP_PY, "astro.py": b"x=1"}))
NEEDS2 = (entry(code="33333333", title="needs-two", version="1", category="Apps",
                caps=[{"required": True, "feature": {"type": "TildagonOSMinimumVersion", "version": "3.0.0"}}]),
          tarball({"app.py": APP_PY}))


def open_dialog(tmp_path, monkeypatch, backend="badge", pairs=(NEAT, CLOCK, NEEDS2), answer=True, on_badge=()):
    """The dialog on a fake store (and a fake badge unless backend is None). on_badge: (folder, version) apps already
    there. Returns (app, dlg, asked, web)."""
    asked = []
    monkeypatch.setattr(dialog, "ask_yes_no", lambda parent, title, text: asked.append(text) or answer)
    monkeypatch.setattr(dialog, "error", lambda parent, title, text: asked.append("ERROR " + text))
    monkeypatch.setattr(dialog, "info", lambda parent, title, text: asked.append("INFO " + text))
    app = App(settings=Settings.load(str(tmp_path / "cfg.json")))
    APP[:] = [app]
    web = web_for(*pairs)
    store = appstore.Store(str(tmp_path / "store"), web)
    root = tmp_path / "badge"
    for folder, version in on_badge:
        (root / "apps" / folder).mkdir(parents=True)
        (root / "apps" / folder / "metadata.json").write_text(json.dumps({"name": folder, "version": version}))
    root.mkdir(exist_ok=True)
    be = Badge() if backend == "badge" else backend
    dlg = dialog.BadgeAppsDialog(app, be, "Test badge", pane=app.right, store=store, root=str(root))
    wait_until(lambda: len(dlg.model) == len(pairs) and not dlg.busy and dlg.job is None and dlg.board_read)
    return app, dlg, asked, web


def select(dlg, title):
    dlg.show_box.set_active_id("all")
    for row in dlg.sorted:
        if dlg.index[row[dialog.KEY]].title == title:
            dlg.view.get_selection().select_iter(row.iter)
            return dlg.index[row[dialog.KEY]]
    raise AssertionError(title)


def rows(model):
    return {r[dialog.NAME]: r for r in model}


def test_without_a_badge_it_browses_and_keeps_favourites(tmp_path, monkeypatch):
    app, dlg, _asked, _web = open_dialog(tmp_path, monkeypatch, backend=None)
    try:
        assert set(rows(dlg.model)) == {"neat-app", "tildagon-gps-clock", "needs-two"}
        assert "No badge connected" in dlg.board_line.get_text()
        assert "3 apps, list from" in dlg.store_line.get_text() and "0 of them kept" in dlg.store_line.get_text()
        assert all(r[dialog.OFFLINE] == "not downloaded" for r in dlg.model)
        assert not dlg.install_btn.get_sensitive() and not dlg.favs_btn.get_sensitive()
        assert dlg.offline_btn.get_sensitive() and dlg.refresh_btn.get_sensitive()
        cats = [(r[0], r[1]) for r in dlg.category_box.get_model()]       # (text, id)
        assert cats == [("All", ""), ("Apps", "Apps"), ("Games", "Games")]
        dlg.category_box.set_active_id("Games")
        assert [r[dialog.NAME] for r in dlg.filter] == ["neat-app"]
        dlg.category_box.set_active_id("")
        path = next(r.path for r in dlg.sorted if r[dialog.NAME] == "neat-app")
        dlg._star_toggled(None, str(path))
        assert dlg.store.favourites() == {"github/alice/neat-app"}
        dlg.show_box.set_active_id("favourites")
        assert [r[dialog.NAME] for r in dlg.filter] == ["neat-app"]
        dlg.search.set_text("gps")
        wait_until(lambda: dlg.count.get_text() == "0 of 3")         # favourites AND the search
    finally:
        dlg.destroy()
        app.destroy()


def test_it_marks_what_is_on_the_badge_and_updates_after_asking(tmp_path, monkeypatch):
    app, dlg, asked, _web = open_dialog(tmp_path, monkeypatch, on_badge=[("Corteil_tildagon_gps_clock", "2")])
    try:
        assert "Tildagon OS v2.2.0, 1 apps on it, 1.0 MB free" in dlg.board_line.get_text()
        r = rows(dlg.model)
        assert r["tildagon-gps-clock"][dialog.ON_BADGE] == "2 (update)" and r["neat-app"][dialog.ON_BADGE] == ""
        dlg.show_box.set_active_id("updates")
        assert [x[dialog.NAME] for x in dlg.filter] == ["tildagon-gps-clock"]
        select(dlg, "tildagon-gps-clock")
        assert dlg.install_btn.get_label() == "Update" and dlg.remove_btn.get_sensitive()
        assert "On the badge: apps/Corteil_tildagon_gps_clock, version 2" in dlg.details.get_text()
        dlg.response(Gtk.ResponseType.OK)
        wait_until(lambda: not dlg.busy and dlg.job is None)
        assert "Update tildagon-gps-clock 3" in asked[0] and "over version 2" in asked[0]
        folder = tmp_path / "badge" / "apps" / "Corteil_tildagon_gps_clock"
        assert (folder / "astro.py").read_bytes() == b"x=1"
        assert json.loads((folder / "metadata.json").read_text())["version"] == "3"
        assert rows(dlg.model)["tildagon-gps-clock"][dialog.ON_BADGE] == "3"
        assert "Installed tildagon-gps-clock 3" in dlg.note.get_text() and "Restart the badge" in dlg.note.get_text()
        assert dlg.install_btn.get_label() == "Reinstall"
    finally:
        dlg.destroy()
        app.destroy()


def test_install_all_favourites_puts_on_only_what_is_missing_or_older(tmp_path, monkeypatch):
    app, dlg, asked, _web = open_dialog(tmp_path, monkeypatch, on_badge=[("alice_neat_app", "1.1"),
                                                                       ("Corteil_tildagon_gps_clock", "2")])
    try:
        for title in ("neat-app", "tildagon-gps-clock"):
            dlg.store.set_favourite(dlg.index["github/%s/%s" % ("Corteil" if "gps" in title else "alice", title)])
        dlg._sensitive()
        assert dlg.favs_btn.get_sensitive()
        dlg.response(3)
        wait_until(lambda: not dlg.busy and dlg.job is None)
        assert asked[0].startswith("Put these on Test badge?") and "tildagon-gps-clock 3 (update)" in asked[0]
        assert "neat-app" not in asked[0]                                 # already at the store's version
        assert rows(dlg.model)["tildagon-gps-clock"][dialog.ON_BADGE] == "3"
        asked.clear()
        dlg.response(3)
        pump(100)
        assert asked and asked[0].startswith("INFO Every favourite is already on Test badge")
    finally:
        dlg.destroy()
        app.destroy()


def test_install_by_code(tmp_path, monkeypatch):
    app, dlg, asked, _web = open_dialog(tmp_path, monkeypatch)
    try:
        monkeypatch.setattr(dialog, "prompt", lambda *a, **k: " 1111 1111 ")
        dlg.response(2)
        wait_until(lambda: not dlg.busy and dlg.job is None)
        assert (tmp_path / "badge" / "apps" / "alice_neat_app" / "app.py").exists()
        assert dlg.selected_app().code == "11111111"
        monkeypatch.setattr(dialog, "prompt", lambda *a, **k: "99999999")
        asked.clear()
        dlg.response(2)
        pump(50)
        assert asked == ["ERROR No app in the store has the code 99999999."]
    finally:
        dlg.destroy()
        app.destroy()


def test_an_unmet_os_minimum_is_asked_about_not_blocked(tmp_path, monkeypatch):
    app, dlg, asked, _web = open_dialog(tmp_path, monkeypatch, answer=False)
    try:
        select(dlg, "needs-two")
        assert "Tildagon OS 3.0.0 or later (required, NOT met on this badge)" in dlg.details.get_text()
        assert dlg.install_btn.get_sensitive()                            # decision 5: never blocked
        dlg.response(Gtk.ResponseType.OK)
        pump(100)
        assert asked == ["needs-two needs Tildagon OS 3.0.0 or later; this badge has v2.2.0.\n\nInstall anyway?"]
        assert not (tmp_path / "badge" / "apps").exists()                 # answered no: nothing written
    finally:
        dlg.destroy()
        app.destroy()


def test_remove_deletes_the_apps_folder_after_asking(tmp_path, monkeypatch):
    app, dlg, asked, _web = open_dialog(tmp_path, monkeypatch, on_badge=[("alice_neat_app", "1.1")])
    try:
        select(dlg, "neat-app")
        dlg.response(4)
        wait_until(lambda: not dlg.busy and dlg.job is None)
        assert asked[0].startswith("Delete alice_neat_app")
        assert not (tmp_path / "badge" / "apps" / "alice_neat_app").exists()
        assert rows(dlg.model)["neat-app"][dialog.ON_BADGE] == "" and not dlg.remove_btn.get_sensitive()
    finally:
        dlg.destroy()
        app.destroy()


def test_offline_it_uses_the_saved_list_and_the_kept_apps(tmp_path, monkeypatch):
    app, dlg, asked, web = open_dialog(tmp_path, monkeypatch)
    try:
        dlg.download_offline()
        wait_until(lambda: not dlg.busy)
        assert "3 downloaded, 0 already kept." in dlg.note.get_text()
        assert all(r[dialog.OFFLINE] == "" for r in dlg.model) and "3 of them kept" in dlg.store_line.get_text()
    finally:
        dlg.destroy()
        app.destroy()
    web.down = True                                                       # no internet now: a fresh dialog
    app = App(settings=Settings.load(str(tmp_path / "cfg2.json")))
    APP[:] = [app]
    dlg = dialog.BadgeAppsDialog(app, Badge(), "Test badge", store=appstore.Store(str(tmp_path / "store"), web),
                                 root=str(tmp_path / "badge"))
    try:
        wait_until(lambda: len(dlg.model) == 3 and not dlg.busy and dlg.job is None and dlg.board_read)
        assert "(offline: the saved list)" in dlg.store_line.get_text()
        select(dlg, "neat-app")
        dlg.install([dlg.selected_app()], confirm=False)
        wait_until(lambda: not dlg.busy and dlg.job is None)
        assert (tmp_path / "badge" / "apps" / "alice_neat_app" / "app.py").exists()   # installed with no internet
    finally:
        dlg.destroy()
        app.destroy()


def test_a_board_without_tildagon_os_can_browse_but_not_install(tmp_path, monkeypatch):
    pico = Badge()
    pico.os_version = None
    app, dlg, _asked, _web = open_dialog(tmp_path, monkeypatch, backend=pico)
    try:
        assert "is not running Tildagon OS" in dlg.board_line.get_text()
        select(dlg, "neat-app")
        assert not dlg.install_btn.get_sensitive() and not dlg.code_btn.get_sensitive()
        assert dlg.offline_btn.get_sensitive()
    finally:
        dlg.destroy()
        app.destroy()


def test_restart_disconnects_with_a_soft_reset(tmp_path, monkeypatch):
    app, dlg, _asked, _web = open_dialog(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(app, "disconnect", lambda pane=None, **kw: calls.append((pane, kw)))
    try:
        assert dlg.restart_btn.get_sensitive()
        dlg.response(1)
        pump(50)
        assert calls == [(app.right, {"reset": True})] and dlg.closed
    finally:
        app.destroy()


def test_the_tools_menu_opens_it_with_or_without_a_badge(tmp_path, monkeypatch):
    app = App(settings=Settings.load(str(tmp_path / "cfg.json")))
    opened = []
    monkeypatch.setattr("mpexplorer.gtkui.app.BadgeAppsDialog", lambda *a, **kw: opened.append((a, kw)))
    try:
        app.badge_apps()
        assert opened == [((app,), {})]                                   # browse only
    finally:
        app.destroy()
