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
    app = make_app()
    gate = threading.Event()
    app.runner.submit(_slow_job(gate), label="Copying ...", status="Uploading main.py")
    pump(150)
    assert app._popup is None                                              # not yet: the job is still young
    pump(700)
    popup = app._popup
    try:
        assert popup is not None and popup.get_visible() and not popup.get_modal()
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
    monkeypatch.setattr(app_module, "POPUP_DELAY", 0.2)
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
