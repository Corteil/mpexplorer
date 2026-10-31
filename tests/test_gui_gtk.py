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
    from mpexplorer.gtkui.common import GdkPixbuf
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
        grid = app.bar.get_parent()
        bar, label, total = app.bar.get_allocation(), app.status.get_allocation(), grid.get_allocation()
        cancel = [c for c in grid.get_children() if isinstance(c, Gtk.Button)][0].get_allocation()
        assert abs(cancel.height - total.height) <= 1                                 # Cancel as tall as both rows
        assert total.height >= bar.height + label.height                              # ... which hold both
        assert 0.55 < bar.width / total.width < 0.65                                  # about 60% of the width
        assert abs((bar.x + bar.width / 2) - (total.x + total.width / 2)) <= 3        # centred
        assert abs(label.x - bar.x) <= 1                                              # text lines up with the bar
    finally:
        gate.set()
    pump(500)
    assert app.bar.get_fraction() == 0


def _sync_plan(tmp_path):
    from mpexplorer.backends import LocalBackend
    from mpexplorer.sync import build_plan
    src, dst = tmp_path / "src", tmp_path / "dst"
    (src / "lib").mkdir(parents=True)
    dst.mkdir()
    for name in ("a.py", "b.txt", "lib/m.py", "lib/n.py"):
        (src / name).write_text(name)
    (dst / "extra.txt").write_text("old")
    be = LocalBackend()
    return build_plan(be, str(src), be, str(dst))


def _review(app, plan):
    """A sync dialog that starts at the review of this plan (the real one compares first)."""
    from mpexplorer.gtkui.dialogs import SyncDialog
    return SyncDialog(app, plan.src_be, plan.src, plan.dst_be, plan.dst, plan=plan)


def _tick(dialog, kind, rel):
    return next(r[0] for r in dialog.store if (r[5], r[6]) == (kind, rel))


def _click(dialog, kind, rel):
    path = next(r.path for r in dialog.store if (r[5], r[6]) == (kind, rel))
    dialog._toggled(None, str(path))


def test_sync_dialog_shows_both_folders_and_the_space_and_lets_items_be_unticked(make_app, tmp_path):
    app = make_app()
    plan = _sync_plan(tmp_path)
    dialog = _review(app, plan)
    try:
        texts = [w.get_text() for w in dialog.get_content_area().get_children()[0].get_children()
                 if isinstance(w, Gtk.Label)]
        assert any(str(tmp_path / "src") in t for t in texts) and any(str(tmp_path / "dst") in t for t in texts)
        assert "needed" in dialog.space.get_text() and "free" in dialog.space.get_text()
        assert dialog.sync_btn.get_label() == "Sync" and all(r[0] for r in dialog.store)      # everything ticked
        assert [r[6] for r in dialog.store if r[5] == "delete"] == []                           # mirror is off
        # leave one file out
        _click(dialog, "copy", "b.txt")
        assert _tick(dialog, "copy", "b.txt") is False and "3 of 4 file(s)" in dialog.summary.get_text()
        # a folder takes its files along
        _click(dialog, "mkdir", "lib")
        assert not _tick(dialog, "copy", "lib/m.py") and not _tick(dialog, "copy", "lib/n.py")
        # a file brings its folder back
        _click(dialog, "copy", "lib/n.py")
        assert _tick(dialog, "mkdir", "lib") and _tick(dialog, "copy", "lib/n.py")
        assert not _tick(dialog, "copy", "lib/m.py")
        # nothing ticked: nothing to sync
        dialog._set_all(False)
        assert not dialog.sync_btn.get_sensitive()
        dialog._set_all(True)
        sent = []
        dialog.start_transfer = lambda mirror, selected: sent.append((mirror, selected))
        dialog.mirror.set_active(True)
        # and one deletion left out
        _click(dialog, "delete", "extra.txt")
        dialog.response(Gtk.ResponseType.OK)
        (mirror, selected), = sent
        assert mirror is True and ("copy", "b.txt") in selected and ("delete", "extra.txt") not in selected
    finally:
        dialog.destroy()


def test_sync_dialog_warns_when_the_target_is_too_small(make_app, tmp_path):
    app = make_app()
    plan = _sync_plan(tmp_path)
    plan.dst_free = 3                                         # smaller than the files to copy
    dialog = _review(app, plan)
    try:
        assert "not enough" in dialog.space.get_text()
        plan.dst_free = None
        dialog._summarise()
        assert "unknown" in dialog.space.get_text()
    finally:
        dialog.destroy()


def _target_dialog(app, tmp_path, from_selection, src="/dev/lib/proj"):
    from mpexplorer.backends import LocalBackend
    from mpexplorer.gtkui.dialogs import SyncTargetDialog
    be = LocalBackend()
    return SyncTargetDialog(app, be, src, from_selection, be, str(tmp_path))


def test_sync_target_dialog_offers_a_folder_for_a_selected_folder(make_app, tmp_path):
    app = make_app()
    dialog = _target_dialog(app, tmp_path, from_selection=True)
    try:
        assert dialog.make.get_active() and dialog.name.get_text() == "proj"             # ticked for a selected folder
        assert dialog.target() == str(tmp_path / "proj") and dialog.go.get_sensitive()
        assert "created first" in dialog.result.get_text()
        dialog.name.set_text("copy-of-proj")
        assert dialog.target() == str(tmp_path / "copy-of-proj")
        for bad in ("", "a/b", "..", "."):                                               # not usable as one folder name
            dialog.name.set_text(bad)
            assert not dialog.go.get_sensitive(), bad
        dialog.make.set_active(False)                                                    # sync into the target itself
        assert dialog.target() == str(tmp_path) and dialog.go.get_sensitive()
    finally:
        dialog.destroy()


def test_sync_target_dialog_does_not_default_to_a_new_folder_for_the_current_folder(make_app, tmp_path):
    app = make_app()
    current = _target_dialog(app, tmp_path, from_selection=False)
    root = _target_dialog(app, tmp_path, from_selection=True, src="/")                   # the root has no name to use
    try:
        assert not current.make.get_active() and current.target() == str(tmp_path)
        assert not root.make.get_active() and not root.make.get_sensitive() and root.target() == str(tmp_path)
    finally:
        current.destroy()
        root.destroy()


def test_mirror_deletion_needs_a_confirmation(make_app, tmp_path, monkeypatch):
    from mpexplorer.gtkui import dialogs
    app = make_app()
    sent, asked = [], []
    plan = _sync_plan(tmp_path)
    for answer in (False, True):
        monkeypatch.setattr(dialogs, "confirm_mirror_delete",
                            lambda parent, label, path, count, tops, a=answer: asked.append((count, tops)) or a)
        dialog = _review(app, plan)
        dialog.start_transfer = lambda mirror, selected: sent.append(selected)
        dialog.mirror.set_active(True)
        dialog.response(Gtk.ResponseType.OK)
        if not answer:
            dialog.destroy()                      # a refusal leaves the dialog open
    assert asked == [(1, [("extra.txt", 1)])] * 2
    # only the second answer went through
    assert len(sent) == 1 and ("delete", "extra.txt") in sent[0]


def test_no_confirmation_when_nothing_is_deleted(make_app, tmp_path, monkeypatch):
    from mpexplorer.gtkui import dialogs
    app = make_app()
    sent = []
    monkeypatch.setattr(dialogs, "confirm_mirror_delete", lambda *a: pytest.fail("asked although nothing is deleted"))
    dialog = _review(app, _sync_plan(tmp_path))
    dialog.start_transfer = lambda mirror, selected: sent.append(selected)
    dialog.response(Gtk.ResponseType.OK)                                                 # mirror is off
    assert len(sent) == 1 and not any(k[0] == "delete" for k in sent[0])


def test_many_deletions_are_listed_by_folder_and_can_be_unticked(make_app, tmp_path):
    from mpexplorer.backends import LocalBackend
    from mpexplorer.gtkui import dialogs
    from mpexplorer.sync import build_plan
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    (src / "keep.txt").write_text("k")
    for top, n in (("big", dialogs.MAX_DELETE_ROWS), ("small", 5)):
        (dst / top).mkdir(parents=True)
        for i in range(n):
            (dst / top / ("f%d" % i)).write_text("x")
    be = LocalBackend()
    app = make_app()
    dialog = _review(app, build_plan(be, str(src), be, str(dst)))
    try:
        dialog.mirror.set_active(True)
        rows = [(r[5], r[6], r[1]) for r in dialog.store if r[5] == "delete-group"]       # one row per top-level folder
        assert sorted(rows) == [("delete-group", "big", "Delete %d" % (dialogs.MAX_DELETE_ROWS + 1)),
                                ("delete-group", "small", "Delete 6")]                    # the files and their folder
        assert sum(k[0] == "delete" for k in dialog._selected()) == dialogs.MAX_DELETE_ROWS + 1 + 6
        dialog._toggled(None, str(next(r.path for r in dialog.store if r[6] == "big")))
        assert not any(k[1].startswith("big") for k in dialog._selected() if k[0] == "delete")
        assert any(k[1].startswith("small") for k in dialog._selected() if k[0] == "delete")
    finally:
        dialog.destroy()


def _wait(cond, secs=8.0):
    end = time.time() + secs
    while not cond() and time.time() < end:
        pump(50)
    return cond()


def test_sync_dialog_compares_inside_itself_then_shows_the_review(make_app, tmp_path):
    from mpexplorer.backends import LocalBackend
    from mpexplorer.gtkui.dialogs import SyncDialog
    app = make_app()
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / "a.py").write_text("a")
    be = LocalBackend()
    dialog = SyncDialog(app, be, str(src), be, str(dst))
    try:
        assert dialog.phase == "scanning" and dialog.progress_box.get_visible() and not dialog.review.get_visible()
        assert not dialog.sync_btn.get_sensitive()                                    # nothing to sync until compared
        assert _wait(lambda: dialog.phase == "review")
        assert dialog.review.get_visible() and not dialog.progress_box.get_visible() and dialog.sync_btn.get_sensitive()
        assert [(r[5], r[6]) for r in dialog.store] == [("copy", "a.py")]
        assert app.status.get_text() == "Ready"                                       # main status bar not used
    finally:
        dialog.destroy()


def test_sync_dialog_says_when_there_is_nothing_to_do(make_app, tmp_path):
    from mpexplorer.backends import LocalBackend
    from mpexplorer.gtkui.dialogs import SyncDialog
    app = make_app()
    for name in ("src", "dst"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "same.txt").write_text("same")
    be = LocalBackend()
    dialog = SyncDialog(app, be, str(tmp_path / "src"), be, str(tmp_path / "dst"))
    try:
        assert _wait(lambda: dialog.phase == "done")
        assert "Nothing to do" in dialog.result.get_text() and dialog.cancel_btn.get_label() == "Close"
        assert not dialog.sync_btn.get_visible()
    finally:
        dialog.destroy()


def test_sync_dialog_transfers_inside_itself_and_reports_the_result(make_app, tmp_path):
    app = make_app()
    plan = _sync_plan(tmp_path)
    dialog = _review(app, plan)
    try:
        dialog._set_all(True)
        _click(dialog, "copy", "b.txt")                                               # leave one file out
        dialog.response(Gtk.ResponseType.OK)
        assert dialog.phase == "transferring" and dialog.progress_box.get_visible() and not dialog.review.get_visible()
        assert _wait(lambda: dialog.phase == "done")
        text = dialog.result.get_text()
        assert text.startswith("Done: 3 file(s)") and "copied" in text and dialog.cancel_btn.get_label() == "Close"
        assert (tmp_path / "dst" / "a.py").exists() and (tmp_path / "dst" / "lib" / "m.py").exists()
        assert not (tmp_path / "dst" / "b.txt").exists() and app.status.get_text() == "Sync complete"
        dialog.response(Gtk.ResponseType.CANCEL)                                      # "Close"
        assert dialog.phase == "closed"
    finally:
        if dialog.phase != "closed":
            dialog.destroy()


def test_sync_dialog_shows_the_scan_and_the_transfer_progress_itself(make_app, tmp_path):
    from types import SimpleNamespace
    from mpexplorer.ops import Progress
    app = make_app()
    dialog = _review(app, _sync_plan(tmp_path))
    try:
        prog = Progress()
        prog.label = "Comparing lib/m.py"
        dialog.job, dialog.phase = SimpleNamespace(prog=prog), "scanning"
        dialog._tick()
        assert dialog.progress_text.get_text() == "Comparing lib/m.py"               # what is being scanned
        prog.label, prog.total, prog.done = "m.py", 4096, 1024
        dialog.phase = "transferring"
        dialog._tick()
        assert dialog.bar.get_fraction() == 0.25
        assert dialog.progress_text.get_text().startswith("m.py   1.0 KB of 4.0 KB (25%)")
    finally:
        dialog.job = None
        dialog.destroy()


def test_inline_jobs_do_not_use_the_main_status_bar(make_app):
    import threading
    app = make_app()
    gate = threading.Event()

    def job(prog):
        prog.label, prog.total, prog.done = "x", 100, 50
        gate.wait(5)
    app.runner.submit(job, label="Comparing folders ...", inline=True)
    pump(400)
    try:
        assert app.bar.get_fraction() == 0 and app.status.get_text() == "Ready"
    finally:
        gate.set()
    pump(300)


def test_sync_dialog_progress_bar_is_centred_at_60_percent_with_its_text_under_it(make_app, tmp_path):
    app = make_app()
    dialog = _review(app, _sync_plan(tmp_path))
    try:
        dialog.review.hide()                                  # hold the dialog in its progress state
        dialog.progress_box.show_all()
        pump(400)
        grid, bar = dialog.progress_box.get_allocation(), dialog.bar.get_allocation()
        text = dialog.progress_text.get_allocation()
        assert 0.55 < bar.width / grid.width < 0.65
        assert abs((bar.x + bar.width / 2) - (grid.x + grid.width / 2)) <= 3
        assert abs(text.x - bar.x) <= 1 and text.y > bar.y
    finally:
        dialog.destroy()


def _slow_job(gate):
    def job(prog):
        prog.label, prog.total, prog.done = "main.py", 4096, 1024
        gate.wait(5)
    return job


def test_transfer_popup_opens_after_the_delay_and_closes_with_the_job(make_app, monkeypatch):
    import threading
    from mpexplorer.gtkui import app as app_module
    monkeypatch.setattr(app_module, "POPUP_DELAY", 0.4)
    monkeypatch.setattr(app_module, "POPUP_MIN_VISIBLE", 0.0)       # these tests want it gone when the job is
    app = make_app()
    gate = threading.Event()
    app.runner.submit(_slow_job(gate), label="Copying ...", status="Uploading main.py")
    pump(150)
    assert app._popup is None                                              # not yet: the job is still young
    pump(700)
    popup = app._popup
    try:
        assert popup is not None and popup.get_visible() and not popup.get_modal()
        # Hide and Cancel while running
        assert popup.running.get_visible() and not popup.close_btn.get_visible()
        assert popup.heading.get_text() == "Uploading main.py" and popup.bar.get_fraction() == 0.25
        assert popup.file.get_text() == "main.py" and popup.text.get_text().startswith("1.0 KB of 4.0 KB (25%)")
        grid, bar = popup.bar.get_parent().get_allocation(), popup.bar.get_allocation()
        text = popup.text.get_allocation()
        assert 0.55 < bar.width / grid.width < 0.65 and abs((bar.x + bar.width / 2) - (grid.x + grid.width / 2)) <= 3
        assert abs(text.x - bar.x) <= 1 and abs(popup.file.get_allocation().x - bar.x) <= 1
    finally:
        gate.set()
    pump(500)
    assert app._popup is None and app._popup_job is None                    # it went with the job


def test_transfer_popup_stays_away_from_quick_quiet_and_inline_jobs(make_app, monkeypatch):
    import threading
    from mpexplorer.gtkui import app as app_module
    app = make_app()
    app.runner.submit(lambda prog: None, label="Copying ...")                 # finishes at once, default delay of 1 s
    pump(400)
    assert app._popup is None
    assert app_module.POPUP_DELAY == 1.0 and app_module.POPUP_MIN_VISIBLE == 3.0           # the defaults
    monkeypatch.setattr(app_module, "POPUP_DELAY", 0.2)
    monkeypatch.setattr(app_module, "POPUP_MIN_VISIBLE", 0.0)       # these tests want it gone when the job is
    for kwargs in ({"quiet": True}, {"inline": True}):
        gate = threading.Event()
        app.runner.submit(_slow_job(gate), label="Working ...", **kwargs)
        pump(700)
        try:
            assert app._popup is None, kwargs
        finally:
            gate.set()
        pump(300)


def test_transfer_popup_can_be_hidden_without_stopping_the_job(make_app, monkeypatch):
    import threading
    from mpexplorer.gtkui import app as app_module
    monkeypatch.setattr(app_module, "POPUP_DELAY", 0.2)
    monkeypatch.setattr(app_module, "POPUP_MIN_VISIBLE", 0.0)       # these tests want it gone when the job is
    app = make_app()
    gate = threading.Event()
    job = app.runner.submit(_slow_job(gate), label="Copying ...")
    pump(600)
    try:
        assert app._popup is not None
        app._popup.dismiss()
        assert app._popup is None and app.runner.current is job              # closed, but the job runs on
        pump(500)
        assert app._popup is None                                            # and it is not opened again for this job
    finally:
        gate.set()
    pump(300)
    gate2 = threading.Event()
    app.runner.submit(_slow_job(gate2), label="Copying ...")                  # the next job gets its own popup
    pump(600)
    try:
        assert app._popup is not None
    finally:
        gate2.set()
    pump(300)


def test_transfer_popup_cancel_stops_the_job(make_app, monkeypatch):
    from mpexplorer.errors import Cancelled
    from mpexplorer.gtkui import app as app_module
    monkeypatch.setattr(app_module, "POPUP_DELAY", 0.2)
    monkeypatch.setattr(app_module, "POPUP_MIN_VISIBLE", 0.0)       # these tests want it gone when the job is
    app = make_app()
    outcome = []

    def job(prog):
        prog.total = 100
        for _ in range(500):
            prog.check()                                                      # raises Cancelled once cancelled
            time.sleep(0.01)
    app.runner.submit(job, lambda res, exc: outcome.append(exc), "Copying ...")
    pump(600)
    app._popup.cancel_btn.clicked()
    assert not app._popup.cancel_btn.get_sensitive()
    pump(600)
    assert len(outcome) == 1 and isinstance(outcome[0], Cancelled) and app._popup is None


def test_delete_progress_shows_item_counts_in_the_status_bar_and_the_popup(make_app, monkeypatch):
    import threading
    from mpexplorer.gtkui import app as app_module
    monkeypatch.setattr(app_module, "POPUP_DELAY", 0.3)
    monkeypatch.setattr(app_module, "POPUP_MIN_VISIBLE", 0.0)       # these tests want it gone when the job is
    app = make_app()
    gate = threading.Event()

    def job(prog):
        prog.label, prog.items_total, prog.items, prog.items_word = "Deleting old.py", 5, 2, "deleted"
        gate.wait(5)
    app.runner.submit(job, label="Deleting ...")
    pump(800)
    try:
        assert app.status.get_text().startswith("Deleting old.py   2 of 5 item(s) deleted (40%)")
        assert "0 B" not in app.status.get_text() and app.bar.get_fraction() == 0.4
        popup = app._popup
        assert popup is not None and popup.file.get_text() == "Deleting old.py"
        assert popup.text.get_text().startswith("2 of 5 item(s) deleted (40%)") and popup.bar.get_fraction() == 0.4
    finally:
        gate.set()
    pump(400)


def test_a_job_with_no_total_shows_no_fake_numbers(make_app):
    import threading
    app = make_app()
    gate = threading.Event()
    app.runner.submit(lambda prog: gate.wait(5), label="Deleting ...")
    pump(500)
    try:
        assert app.status.get_text() == "Deleting ..."                      # not "Deleting ...   0 B   0 B/s"
        assert app.bar.get_fraction() == 0                                  # (the bar pulses instead)
    finally:
        gate.set()
    pump(300)


def test_status_goes_back_to_ready_after_the_hold_time(make_app, monkeypatch):
    from mpexplorer.gtkui import app as app_module
    monkeypatch.setattr(app_module, "STATUS_HOLD", 0.4)
    app = make_app()
    app.set_status("Sync complete")
    pump(150)
    assert app.status.get_text() == "Sync complete"                           # held for a while
    pump(500)
    assert app.status.get_text() == "Ready"
    app.set_status("Settings: bad value", keep=True)                          # a warning is not wiped
    pump(700)
    assert app.status.get_text() == "Settings: bad value"
    app.set_status("Ready")
    assert app._status_timer is None                                          # nothing pending for Ready


def test_status_stays_while_a_job_runs_and_clears_after_it_finishes(make_app, monkeypatch):
    import threading
    from mpexplorer.gtkui import app as app_module
    monkeypatch.setattr(app_module, "STATUS_HOLD", 0.4)
    app = make_app()
    gate = threading.Event()

    def job(prog):
        prog.label, prog.total, prog.done = "main.py", 4096, 1024
        gate.wait(5)
    app.runner.submit(job, label="Copying ...", inline=False)
    pump(900)                                                                  # longer than the hold time
    try:
        assert app.status.get_text().startswith("main.py")                    # progress keeps the status alive
    finally:
        gate.set()
    pump(300)
    assert app.status.get_text() != "Ready"                                    # the result is shown first ...
    pump(700)
    assert app.status.get_text() == "Ready"                                    # ... then it clears


def test_a_simple_job_reports_done_and_the_cancel_button_is_a_word(make_app, monkeypatch):
    from mpexplorer.gtkui import app as app_module
    monkeypatch.setattr(app_module, "STATUS_HOLD", 0.4)
    app = make_app()
    app._simple_job(lambda prog: None, "Creating folder", "Cannot create folder")
    pump(400)
    assert app.status.get_text() == "Done"
    pump(700)
    assert app.status.get_text() == "Ready"
    cancel = [c for c in app.status.get_parent().get_children() if isinstance(c, Gtk.Button)][0]
    assert cancel.get_label() == "Cancel" and cancel.get_image() is None      # the word, not an icon


def test_transfer_popup_stays_up_for_the_minimum_time_when_the_job_ends_sooner(make_app, monkeypatch):
    import threading
    from mpexplorer.gtkui import app as app_module
    monkeypatch.setattr(app_module, "POPUP_DELAY", 0.2)
    monkeypatch.setattr(app_module, "POPUP_MIN_VISIBLE", 1.2)
    app = make_app()
    gate = threading.Event()
    app.runner.submit(_slow_job(gate), label="Copying ...", status="Uploading main.py")
    pump(500)
    popup = app._popup
    assert popup is not None
    gate.set()
    pump(300)                                                              # the job has ended; the popup has not gone
    assert app._popup is popup and popup.finished
    # Hide and Cancel give way to Close
    assert not popup.running.get_visible() and popup.close_btn.get_visible()
    assert popup.heading.get_text() == "Finished: Uploading main.py" and popup.get_visible()
    pump(1300)                                                             # ... until it has been up long enough
    assert app._popup is None


def test_a_lingering_popup_does_not_stop_the_next_job_getting_its_own(make_app, monkeypatch):
    import threading
    from mpexplorer.gtkui import app as app_module
    monkeypatch.setattr(app_module, "POPUP_DELAY", 0.2)
    monkeypatch.setattr(app_module, "POPUP_MIN_VISIBLE", 0.8)
    app = make_app()
    first, second = threading.Event(), threading.Event()
    app.runner.submit(_slow_job(first), label="Copying ...", status="First")
    pump(500)
    old = app._popup
    first.set()
    app.runner.submit(_slow_job(second), label="Copying ...", status="Second")
    pump(300)
    assert app._popup is old and old.finished                              # the first one is still shown, finished
    pump(1500)                                                             # then it goes and the second job's appears
    try:
        assert app._popup is not None and app._popup is not old and app._popup.job.status == "Second"
        assert not app._popup.finished
    finally:
        second.set()
    pump(1500)
    assert app._popup is None


def test_the_finished_popup_can_be_closed_at_once_with_its_close_button(make_app, monkeypatch):
    import threading
    from mpexplorer.gtkui import app as app_module
    monkeypatch.setattr(app_module, "POPUP_DELAY", 0.2)
    monkeypatch.setattr(app_module, "POPUP_MIN_VISIBLE", 30.0)                # it would otherwise stay for a long time
    app = make_app()
    gate = threading.Event()
    app.runner.submit(_slow_job(gate), label="Copying ...", status="Uploading main.py")
    pump(500)
    popup = app._popup
    assert popup is not None and popup.running.get_visible()
    gate.set()
    pump(400)
    assert app._popup is popup and popup.close_btn.get_visible()
    popup.close_btn.clicked()
    assert app._popup is None                                                  # gone straight away, not after 30 s
    app.runner.submit(_slow_job(threading.Event()), label="Copying ...", status="Next")
    pump(600)
    assert app._popup is not None                                              # closing it did not hide the next job's
    app._popup.dismiss()
    app.runner.cancel_all()
    pump(300)


def test_device_pane_label_names_the_port_and_board(make_app, tmp_path):
    app = make_app()
    assert app.right.get_label() == "Device (not connected)"
    app.connected(DeviceBackend(RawREPL(FakeSerial())), str(tmp_path), "/dev/ttyACM2", "2e8a:0005:AAA",
                  "Raspberry Pi Pico2 with RP2350")
    assert app.right.get_label() == "Device: /dev/ttyACM2  Pico 2 (RP2350)"
    assert app.left.get_label() == "Host"
    app.right.set_paused(True)
    assert app.right.get_label() == "Device: /dev/ttyACM2  Pico 2 (RP2350) (terminal active - paused)"
    app.disconnect()
    assert app.right.get_label() == "Device (not connected)"
    # the model is not known (no answer to os.uname): just the port
    app.connected(DeviceBackend(RawREPL(FakeSerial())), str(tmp_path), "/dev/ttyACM0", "303a:4001:AAA")
    assert app.right.get_label() == "Device: /dev/ttyACM0"


def test_run_script_can_be_interrupted_by_cancel(make_app, monkeypatch):
    app = make_app()
    calls, submitted = [], []

    class Repl:
        interrupt = staticmethod(lambda: calls.append("ctrl-c"))

    class Board:
        key, repl = "device", Repl()
    app.right.backend = Board()
    monkeypatch.setattr(app.right, "selected", lambda: [("main.py", "/main.py")])
    monkeypatch.setattr(app.runner, "submit", lambda *a, **k: submitted.append(k))
    app.run_script()
    assert len(submitted) == 1
    assert submitted[0]["on_cancel"] is Repl.interrupt              # Cancel and Quit stop an endless script
    submitted[0]["on_cancel"]()
    assert calls == ["ctrl-c"]
    app.right.backend = None


def test_open_with_external_editor_uploads_saves_and_cleans_up(make_app, tmp_path, monkeypatch):
    dialogs = []
    monkeypatch.setattr("mpexplorer.gtkui.app.error", lambda parent, title, text: dialogs.append((title, text)))
    board = tmp_path / "board"
    board.mkdir()
    (board / "main.py").write_bytes(b"v1\n")
    app = make_app()
    launched = []

    class Clock:
        t = 100.0

        def __call__(self):
            return self.t
    clock = Clock()
    app.edits._clock, app.edits.interval, app.edits.root = clock, 0, str(tmp_path / "cache")
    app.edits._last_tick = float("-inf")                            # the poller already ticked on the real clock
    app.edits.launch = lambda argv: launched.append(argv) or type("P", (), {"poll": lambda self: None})()
    app.edits.ask_conflict = lambda name: "skip"                    # a real dialog would block the test
    app.connected(DeviceBackend(RawREPL(FakeSerial())), str(board), "/dev/ttyACM0", "303a:4001:AAA")
    app.open_external(app.right, str(board / "main.py"), "geany {file}")
    pump(400)
    assert len(launched) == 1 and launched[0][0] == "geany"
    cached = launched[0][1]
    assert cached.startswith(str(tmp_path / "cache")) and open(cached, "rb").read() == b"v1\n"
    open(cached, "wb").write(b"v2 from geany\n")
    pump(300)                                                       # the app notices the save
    clock.t += 2                                                    # ... and it settles
    pump(600)
    assert (board / "main.py").read_bytes() == b"v2 from geany\n"
    assert app.status.get_text().startswith("Uploaded main.py")
    app.disconnect()
    pump(400)
    assert not os.path.exists(cached) and not app.edits.sessions    # nothing unsent: the cache copy is removed
    assert dialogs == []                                            # and no 'edits not uploaded' warning


def test_host_file_opens_directly_in_the_external_editor(make_app, tmp_path):
    (tmp_path / "a b.py").write_text("x\n")
    app = make_app()
    launched = []
    app.edits.launch = lambda argv: launched.append(argv)
    app.open_external(app.left, str(tmp_path / "a b.py"), "geany {file}")
    assert launched == [["geany", str(tmp_path / "a b.py")]]


def test_hex_and_image_viewers_open_host_files(make_app, tmp_path):
    from mpexplorer.gtkui.viewer import Viewer
    (tmp_path / "blob.bin").write_bytes(bytes(range(40)))
    pix = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 3, 2)
    pix.savev(str(tmp_path / "p.png"), "png", [], [])
    app = make_app()
    app.open_with(app.left, str(tmp_path / "blob.bin"), 40, "hex")
    pump(300)
    hexview = [w for w in app.windows if isinstance(w, Viewer)][-1]
    text = hexview.get_children()[0].get_children()[1].get_child().get_buffer()
    assert text.get_text(text.get_start_iter(), text.get_end_iter(), True).startswith("00000000  00 01 02")
    app.open_with(app.left, str(tmp_path / "p.png"), (tmp_path / "p.png").stat().st_size, "image")
    pump(300)
    assert "3x2" in [w for w in app.windows if isinstance(w, Viewer)][-1].info.get_text()


def test_open_with_dispatches_on_the_saved_handler(make_app, tmp_path, monkeypatch):
    (tmp_path / "a b.py").write_text("x\n")
    (tmp_path / "notes.txt").write_text("y\n")
    app = make_app()
    app.settings.set("open_with.by_extension", {".py": "custom:viewer"})
    app.settings.set("open_with.custom", {"viewer": "viewer --new {file}"})
    launched, editor = [], []
    app.edits.launch = lambda argv: launched.append(argv)
    monkeypatch.setattr(app, "open_file", lambda pane, path, size: editor.append(path))
    app.open_default(app.left, str(tmp_path / "a b.py"), 2)                 # saved for .py: the custom command
    app.open_default(app.left, str(tmp_path / "notes.txt"), 2)              # nothing saved for .txt: built-in editor
    assert launched == [["viewer", "--new", str(tmp_path / "a b.py")]]
    assert editor == [str(tmp_path / "notes.txt")]
    errors = []
    monkeypatch.setattr("mpexplorer.gtkui.app.error", lambda parent, title, text: errors.append(text))
    app.open_with(app.left, str(tmp_path / "notes.txt"), 2, "custom:gone")   # a stale setting is reported, not raised
    assert len(errors) == 1 and "gone" in errors[0] and len(launched) == 1


def test_choose_open_with_remembers_the_choice_for_the_file_type(make_app, tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("x\n")
    app = make_app()
    launched = []
    app.edits.launch = lambda argv: launched.append(argv)
    picks = iter([("custom:geany", ("geany", "geany {file}"), True),
                  ("custom:thonny", ("thonny", "thonny {file}"), False)])
    monkeypatch.setattr("mpexplorer.gtkui.app.ask_open_with", lambda parent, name, can_run: next(picks))
    app.choose_open_with(app.left, str(tmp_path / "a.py"), 2)
    app.choose_open_with(app.left, str(tmp_path / "a.py"), 2)               # not remembered: the first choice stays
    assert [a[0] for a in launched] == ["geany", "thonny"]
    assert app.settings.get("open_with.by_extension") == {".py": "custom:geany"}
    assert app.settings.get("open_with.custom") == {"geany": "geany {file}", "thonny": "thonny {file}"}


def test_open_with_dialog_lists_apps_and_returns_the_choice(make_app):
    from mpexplorer.gtkui import dialogs
    app = make_app()
    assert dialogs.installed_apps("a.py") is not None                       # whatever this machine has installed
    GLib = dialogs.GLib
    GLib.timeout_add(200, lambda: (app_dialog_ok(), False)[1])

    def app_dialog_ok():
        for w in dialogs.Gtk.Window.list_toplevels():
            if isinstance(w, dialogs.Gtk.Dialog) and w.get_title() == "Open with":
                w.response(dialogs.Gtk.ResponseType.OK)
    assert dialogs.ask_open_with(app, "a.py", True) == ("editor", None, False)      # the first row is preselected


def test_file_types_page_lists_adds_changes_and_removes(make_app, cfg, monkeypatch):
    app = make_app(open_with__by_extension={".py": "custom:geany", ".bin": "hex"},
                   open_with__custom={"geany": "geany {file}"})
    dlg = PreferencesDialog(app)
    assert [tuple(r) for r in dlg.types] == [(".bin", "Hex viewer"), (".py", "Custom: geany {file}")]
    picks = iter([("image", None, False), ("editor", None, True)])
    monkeypatch.setattr("mpexplorer.gtkui.prefs.ask_open_with", lambda *a: next(picks))
    monkeypatch.setattr("mpexplorer.gtkui.prefs.prompt", lambda *a: " PNG")
    dlg._add_type()                                                     # normalised to .png, saved at once
    assert app.settings.get("open_with.by_extension")[".png"] == "image"
    assert saved(cfg)["open_with"]["by_extension"][".png"] == "image"
    dlg.types_view.set_cursor(common.Gtk.TreePath.new_from_indices([0]))       # .bin
    dlg._change_type()
    assert app.settings.get("open_with.by_extension")[".bin"] == "editor"
    dlg.types_view.set_cursor(common.Gtk.TreePath.new_from_indices([0]))
    dlg._remove_type()
    assert ".bin" not in app.settings.get("open_with.by_extension")
    errors = []
    monkeypatch.setattr("mpexplorer.gtkui.prefs.prompt", lambda *a: "a b")
    monkeypatch.setattr("mpexplorer.gtkui.prefs.error", lambda parent, title, text: errors.append(text))
    dlg._add_type()
    assert errors and [r[0] for r in dlg.types] == [".png", ".py"]


def test_shift_enter_and_file_menu_choose_a_program(make_app, tmp_path, monkeypatch):
    (tmp_path / "notes.txt").write_text("y\n")
    (tmp_path / "sub").mkdir()
    app = make_app()
    app.left.go(str(tmp_path))
    pump(300)
    app.active = app.left
    asked = []
    monkeypatch.setattr(app, "choose_open_with", lambda pane, path, size: asked.append(path))
    notes = []
    monkeypatch.setattr("mpexplorer.gtkui.app.info", lambda parent, title, text: notes.append(text))
    shift_enter = types.SimpleNamespace(keyval=common.Gdk.KEY_Return, state=common.Gdk.ModifierType.SHIFT_MASK)
    app.left.tree.get_selection().unselect_all()
    assert app.left._key(None, shift_enter) is True                     # nothing selected: told, not raised
    assert notes == ["Select one file first."] and asked == []
    for i, row in enumerate(app.left.store):
        if row[1] == "notes.txt":
            app.left.tree.get_selection().select_path(common.Gtk.TreePath.new_from_indices([i]))
    assert app.left._key(None, shift_enter) is True
    app.open_with_selected()                                            # the File menu entry
    assert asked == [str(tmp_path / "notes.txt")] * 2
    plain = types.SimpleNamespace(keyval=common.Gdk.KEY_Return, state=0)
    assert app.left._key(None, plain) is False                          # plain Enter still opens the default
