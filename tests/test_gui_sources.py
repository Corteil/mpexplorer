"""The sources manager: Preferences page, the dialog, adding (presets and custom: the Check and the trust question)."""
import os
import time

from guiskip import unavailable

try:
    import gi  # noqa: F401
except ImportError:
    unavailable("PyGObject (python3-gi) is not installed")
if os.name != "nt" and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
    unavailable("no display (run under xvfb-run)")
try:
    from mpexplorer.gtkui import common, sources as ui
    from mpexplorer.gtkui.app import App
    from mpexplorer.gtkui.prefs import PreferencesDialog
except (ImportError, ValueError) as e:
    unavailable("GTK 3 not available: %s" % e)

from mpexplorer import sources as S  # noqa: E402
from mpexplorer.settings import Settings  # noqa: E402

Gtk = common.Gtk


def pump(ms=100):
    end = time.time() + ms / 1000
    while time.time() < end:
        while Gtk.events_pending():
            Gtk.main_iteration()
        time.sleep(0.005)


def wait_until(test, ms=3000):
    end = time.time() + ms / 1000
    while not test() and time.time() < end:
        pump(20)
    assert test()


def make(tmp_path, monkeypatch, check_ok=True, trust=None, answer=True):
    app = App(settings=Settings.load(str(tmp_path / "cfg.json")))
    asked = []
    monkeypatch.setattr(ui, "ask_yes_no", lambda parent, title, text: asked.append((title, text)) or answer)
    monkeypatch.setattr(ui, "info", lambda parent, title, text: asked.append((title, text)))
    checks = []

    def check(src, settings):
        checks.append(src["name"])
        return S.Check(check_ok, "%s: 3 boards, newest v1.0" % src["name"] if check_ok else "no release files", trust)
    return app, ui.SourcesManager(app, app, check), asked, checks


def test_the_menu_offers_the_presets_and_custom_and_greys_the_ones_already_added(tmp_path, monkeypatch):
    app, mgr, _, _ = make(tmp_path, monkeypatch)
    try:
        items = [i.get_label() for i in mgr.add_menu.get_children() if isinstance(i, Gtk.MenuItem) and i.get_label()]
        assert items == ["Pimoroni MicroPython (pimoroni/pimoroni-pico)",
                         "Pimoroni MicroPython (RP2350) (pimoroni/pimoroni-pico-rp2350)",
                         "Pimoroni Pico LiPo (pimoroni/pico-lipo)", "Custom..."]
        S.add_source(app.settings, "firmware", S.validate_firmware_source(S.FIRMWARE_PRESETS[0]))
        enabled = [i.get_sensitive() for i in mgr.add_menu.get_children() if i.get_label()]
        assert enabled == [False, True, True, True]
        assert [r[0] for r in mgr.store] == ["Pimoroni MicroPython"]
    finally:
        app.destroy()


def test_adding_a_preset_checks_it_first_and_a_failed_check_saves_nothing(tmp_path, monkeypatch):
    app, mgr, _, checks = make(tmp_path, monkeypatch)
    try:
        mgr.add_preset(S.FIRMWARE_PRESETS[0])
        wait_until(lambda: app.settings.get("sources.firmware"))
        assert checks == ["Pimoroni MicroPython"] and "3 boards" in mgr.status.get_text()
        assert [r[0] for r in mgr.store] == ["Pimoroni MicroPython"]
    finally:
        app.destroy()
    (tmp_path / "second").mkdir()
    app, mgr, _, _ = make(tmp_path / "second", monkeypatch, check_ok=False)
    try:
        mgr.add_preset(S.FIRMWARE_PRESETS[0])
        wait_until(lambda: mgr.status.get_text().startswith("Not added"))
        assert app.settings.get("sources.firmware") == []
    finally:
        app.destroy()


def test_a_custom_source_from_another_site_asks_to_trust_it_and_a_no_saves_nothing(tmp_path, monkeypatch):
    app, mgr, asked, _ = make(tmp_path, monkeypatch, trust="dl.maker.example", answer=False)
    try:
        dlg = mgr.add_custom()
        dlg.name.set_text("Maker")
        dlg.is_url.set_active(True)
        dlg.address.set_text("https://dl.maker.example/fw.uf2")
        dlg._response(dlg, Gtk.ResponseType.OK)
        wait_until(lambda: asked)
        assert asked[0][0] == "Trust this site?" and "dl.maker.example" in asked[0][1]
        wait_until(lambda: dlg.result.get_text().startswith("Not added"))
        assert app.settings.get("sources.firmware") == [] and app.settings.get("sources.trusted_hosts") == []
        dlg.destroy()
    finally:
        app.destroy()


def test_a_custom_source_that_passes_and_is_trusted_is_saved_with_its_site(tmp_path, monkeypatch):
    app, mgr, asked, _ = make(tmp_path, monkeypatch, trust="dl.maker.example", answer=True)
    try:
        dlg = mgr.add_custom()
        dlg.name.set_text("Maker")
        dlg.is_url.set_active(True)
        dlg.address.set_text("https://dl.maker.example/fw.uf2")
        dlg.firmware.set_active_id("circuitpython")
        dlg._response(dlg, Gtk.ResponseType.OK)
        wait_until(lambda: app.settings.get("sources.firmware"))
        saved = app.settings.get("sources.firmware")[0]
        assert saved["name"] == "Maker" and saved["firmware"] == "circuitpython"
        assert saved["url"].startswith("https://dl.maker")
        assert app.settings.get("sources.trusted_hosts") == ["dl.maker.example"]
        assert [h[0] for h in mgr.hosts] == ["dl.maker.example"]
    finally:
        app.destroy()


def test_a_bad_form_is_explained_not_saved(tmp_path, monkeypatch):
    from mpexplorer import custom_firmware
    app = App(settings=Settings.load(str(tmp_path / "cfg.json")))
    try:
        dlg = ui.AddSourceDialog(app, app.settings, custom_firmware.check_source)       # the real Check, no fetch
        dlg.name.set_text("Mine")
        dlg.address.set_text("nonsense")
        dlg._response(dlg, Gtk.ResponseType.OK)
        wait_until(lambda: dlg.result.get_text().startswith("Not added"))
        assert "owner/name" in dlg.result.get_text() and app.settings.get("sources.firmware") == []
        dlg.destroy()
    finally:
        app.destroy()


def test_remove_asks_and_a_trusted_site_in_use_cannot_be_dropped(tmp_path, monkeypatch):
    app, mgr, asked, _ = make(tmp_path, monkeypatch)
    try:
        S.add_source(app.settings, "firmware", {"name": "Maker", "kind": "url", "url": "https://dl.maker.example/a.uf2"})
        S.trust_host(app.settings, "dl.maker.example")
        mgr.hosts_view.get_selection().select_path(Gtk.TreePath("0"))
        mgr.untrust_selected()
        assert "Still in use" in asked[-1][0] and app.settings.get("sources.trusted_hosts") == ["dl.maker.example"]
        mgr.view.get_selection().select_path(Gtk.TreePath("0"))
        mgr.remove_selected()
        assert "Remove source" in asked[-1][0] and app.settings.get("sources.firmware") == []
        assert app.settings.get("sources.trusted_hosts") == []                    # nothing uses the site any more
        assert [r[0] for r in mgr.store] == [] and [h[0] for h in mgr.hosts] == []
    finally:
        app.destroy()


def test_check_and_check_all_report_without_a_board(tmp_path, monkeypatch):
    app, mgr, _, checks = make(tmp_path, monkeypatch)
    try:
        S.add_source(app.settings, "firmware", S.validate_firmware_source(S.FIRMWARE_PRESETS[0]))
        mgr.view.get_selection().select_path(Gtk.TreePath("0"))
        mgr.check_selected()
        wait_until(lambda: mgr.status.get_text().startswith("OK: Pimoroni MicroPython"))
        assert checks == ["Pimoroni MicroPython"]

        class FakeCatalog:
            def releases(self):
                from mpexplorer.firmware_catalog import Build
                return [Build("a-v1.2.uf2", "v1.2", "2026-09-01", "", False, "https://x/a")], None
        monkeypatch.setattr(ui.custom_firmware, "make_catalog", lambda src: FakeCatalog())
        mgr.check_all()
        wait_until(lambda: mgr.status.get_text() == "Newest versions read.")
        assert mgr.store[0][2] == "v1.2 (2026-09-01)"
    finally:
        app.destroy()


def test_preferences_has_a_sources_page_and_the_firmware_dialog_a_manage_button(tmp_path, monkeypatch):
    app = App(settings=Settings.load(str(tmp_path / "cfg.json")))
    try:
        prefs = PreferencesDialog(app)
        nb = [w for w in prefs.get_content_area().get_children() if isinstance(w, Gtk.Notebook)][0]
        pages = [nb.get_tab_label_text(nb.get_nth_page(i)) for i in range(nb.get_n_pages())]
        assert pages == ["General", "File types", "Sources", "Settings file"]
        page = nb.get_nth_page(2)
        assert isinstance(page, ui.SourcesPage) and list(page.managers) == ["firmware", "libraries"]
        prefs.destroy()
    finally:
        app.destroy()


def test_a_folder_of_the_owners_own_libraries_is_added_without_any_site_to_trust(tmp_path, monkeypatch):
    app = App(settings=Settings.load(str(tmp_path / "cfg.json")))
    try:
        mine = tmp_path / "mylibs"
        mine.mkdir()
        (mine / "blink.py").write_text("print(1)\n")
        mgr = ui.SourcesManager(app, app, section="libraries")                # the real Check: it is all local
        dlg = mgr.add_custom()
        dlg.name.set_text("My libs")
        dlg.is_folder.set_active(True)
        assert dlg.browse.get_visible() and not dlg.pattern.get_visible()
        dlg.address.set_text(str(mine))
        assert dlg.source() == {"name": "My libs", "kind": "folder", "path": str(mine), "for": "micropython"}
        dlg._response(dlg, Gtk.ResponseType.OK)
        wait_until(lambda: app.settings.get("sources.libraries"))
        assert app.settings.get("sources.libraries")[0]["path"] == str(mine)
        assert app.settings.get("sources.trusted_hosts") == []
        assert [r[0] for r in mgr.store] == ["My libs"] and mgr.store[0][1] == str(mine)
    finally:
        app.destroy()
