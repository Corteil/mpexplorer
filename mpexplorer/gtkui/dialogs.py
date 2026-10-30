"""Conflict prompt, sync dialogs and script output windows."""
import time
from collections import Counter

from ..backends import human, human_duration
from ..errors import Cancelled
from ..sync import apply_plan, build_plan
from ..transport import DeviceError
from .common import Gdk, GLib, Gtk, monospace_view, scrolled

LABELS = {"mkdir": "Create folder", "copy": "Copy", "delete": "Delete", "delete-group": "Delete"}


def ask_conflict(parent, name):
    """('overwrite' | 'skip' | 'rename' | 'cancel', apply_to_all)."""
    dlg = Gtk.Dialog(title="File exists", transient_for=parent, modal=True)
    actions = ["overwrite", "skip", "rename", "cancel"]
    for i, text in enumerate(("Overwrite", "Skip", "Keep both", "Cancel")):
        dlg.add_button(text, i)
    box = dlg.get_content_area()
    box.set_border_width(12)
    box.set_spacing(8)
    box.add(Gtk.Label(label="'%s' already exists at the destination." % name))
    everywhere = Gtk.CheckButton(label="Apply to all")
    box.add(everywhere)
    dlg.show_all()
    res = dlg.run()
    all_ = everywhere.get_active()
    dlg.destroy()
    return (actions[res] if 0 <= res < len(actions) else "cancel", all_)


MAX_DELETE_ROWS = 2000      # more deletions than this are listed as one row per top-level folder
BIG_DELETE = 1000           # ... and more than this gets a red warning in the confirmation


def valid_folder_name(name):
    return bool(name) and name not in (".", "..") and "/" not in name and "\0" not in name


class SyncTargetDialog(Gtk.Dialog):
    """Asked before comparing: what is synced to where, and whether to sync into a new folder of its own."""

    def __init__(self, app, src_be, src, from_selection, dst_be, dst_base):
        super().__init__(title="Sync folders", transient_for=app, modal=True)
        self.dst_be, self.dst_base = dst_be, dst_base
        box = self.get_content_area()
        box.set_spacing(8)
        box.set_border_width(10)
        grid = Gtk.Grid(column_spacing=10, row_spacing=3)
        kind = "selected folder" if from_selection else "current folder"
        for i, (title, text) in enumerate((("From", "%s:  %s   (%s)" % (src_be.label, src, kind)),
                                           ("To", "%s:  %s" % (dst_be.label, dst_base)))):
            grid.attach(Gtk.Label(label=title, xalign=1, sensitive=False), 0, i, 1, 1)
            grid.attach(Gtk.Label(label=text, xalign=0, selectable=True), 1, i, 1, 1)
        box.pack_start(grid, False, False, 0)

        name = src_be.basename(src)
        row = Gtk.Box(spacing=6)
        self.make = Gtk.CheckButton(label="Create a folder named")
        self.make.set_active(from_selection and bool(name))
        self.make.set_sensitive(bool(name))                  # the root has no name to give a folder
        self.name = Gtk.Entry(text=name, width_chars=28)
        row.pack_start(self.make, False, False, 0)
        row.pack_start(self.name, False, False, 0)
        row.pack_start(Gtk.Label(label="in the target and sync into it"), False, False, 0)
        box.pack_start(row, False, False, 0)
        self.result = Gtk.Label(xalign=0, wrap=True, use_markup=True)
        box.pack_start(self.result, False, False, 0)
        self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        self.go = self.add_button("Continue", Gtk.ResponseType.OK)
        self.set_default_response(Gtk.ResponseType.OK)
        self.make.connect("toggled", lambda _c: self._update())
        self.name.connect("changed", lambda _e: self._update())
        self._update()
        self.show_all()

    def target(self):
        if self.make.get_active():
            return self.dst_be.join(self.dst_base, self.name.get_text().strip())
        return self.dst_base

    def _update(self):
        making = self.make.get_active()
        target = self.target()
        self.name.set_sensitive(making)
        ok = not making or valid_folder_name(self.name.get_text().strip())
        if not ok:
            text = '<span foreground="#c01c28">That is not a usable folder name.</span>'
        elif making:
            text = "Syncs into  <b>%s</b>  (created first if it does not exist)" % GLib.markup_escape_text(target)
        else:
            text = "Syncs into  <b>%s</b>,  making it match" % GLib.markup_escape_text(target)
        self.result.set_markup(text)
        self.go.set_sensitive(ok)


def ask_sync_target(app, src_be, src, from_selection, dst_be, dst_base):
    """The folder to sync into, or None if the user cancelled."""
    dlg = SyncTargetDialog(app, src_be, src, from_selection, dst_be, dst_base)
    res = dlg.run()
    target = dlg.target()
    dlg.destroy()
    return target if res == Gtk.ResponseType.OK else None


def confirm_mirror_delete(parent, label, path, count, tops):
    """True if the user confirms permanently deleting `count` items at the target. tops: [(top-level name, count)]."""
    dlg = Gtk.MessageDialog(transient_for=parent, modal=True, message_type=Gtk.MessageType.WARNING,
                            text="Delete %d item(s) from %s?" % (count, path))
    lines = ["These exist only at the target (%s: %s). They will be deleted permanently: they do not go to the Trash."
             % (label, path)]
    if tops:
        lines.append("Mostly in:  " + ",  ".join("%s (%d)" % (name, n) for name, n in tops))
    if count > BIG_DELETE:
        lines.append("<b>That is a lot of items. Check that this is the right target folder.</b>")
    dlg.format_secondary_markup("\n\n".join(
        line if line.startswith("<b>") else GLib.markup_escape_text(line) for line in lines))
    cancel = dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
    go = dlg.add_button("Delete and sync", Gtk.ResponseType.ACCEPT)
    go.get_style_context().add_class("destructive-action")
    dlg.set_default(cancel)
    res = dlg.run()
    dlg.destroy()
    return res == Gtk.ResponseType.ACCEPT


class SyncDialog(Gtk.Dialog):
    """One window for the whole sync: comparing (with its progress), the review with a tick box per item, then the
    transfer (with its progress) and the result. Pass `plan` to start at the review (tests)."""

    def __init__(self, app, src_be, src, dst_be, dst, plan=None):
        super().__init__(title="Sync folders", transient_for=app, modal=True)
        self.app, self.src_be, self.src, self.dst_be, self.dst = app, src_be, src, dst_be, dst
        self.plan, self.sel, self.groups = None, {}, {}
        self.job, self.phase, self.started = None, "scanning", 0.0
        self.set_default_size(720, 230)
        box = self.get_content_area()
        box.set_spacing(6)
        box.set_border_width(10)

        grid = Gtk.Grid(column_spacing=10, row_spacing=3)
        for i, (title, text) in enumerate((("From", "%s:  %s" % (src_be.label, src)),
                                           ("To", "%s:  %s" % (dst_be.label, dst)))):
            grid.attach(Gtk.Label(label=title, xalign=1, sensitive=False), 0, i, 1, 1)
            path = Gtk.Label(label=text, xalign=0, selectable=True, hexpand=True)
            path.set_ellipsize(1)       # 1 = start: keep the end of a long path
            grid.attach(path, 1, i, 1, 1)
        box.pack_start(grid, False, False, 0)
        self.extra = Gtk.Grid(column_spacing=10, row_spacing=3)         # space and ignored names, known after comparing
        self.space = Gtk.Label(xalign=0, use_markup=True)
        self.extra.attach(Gtk.Label(label="Space", xalign=1, sensitive=False), 0, 0, 1, 1)
        self.extra.attach(self.space, 1, 0, 1, 1)
        self.ignoring = Gtk.Label(xalign=0)
        self.ignoring_caption = Gtk.Label(label="Ignoring", xalign=1, sensitive=False)
        self.extra.attach(self.ignoring_caption, 0, 1, 1, 1)
        self.extra.attach(self.ignoring, 1, 1, 1, 1)
        box.pack_start(self.extra, False, False, 0)

        # comparing and transferring: a centred bar (60% of the width) with its text under it, like the main window's
        self.progress_box = Gtk.Grid(column_homogeneous=True, row_spacing=4)
        self.progress_box.attach(Gtk.Label(label=""), 0, 0, 1, 2)          # equal side columns: 20% | 60% | 20%
        self.progress_box.attach(Gtk.Label(label=""), 4, 0, 1, 2)
        self.bar = Gtk.ProgressBar(hexpand=True)
        self.progress_text = Gtk.Label(xalign=0, ellipsize=3, hexpand=True)           # 3 = end
        self.progress_box.attach(self.bar, 1, 0, 3, 1)
        self.progress_box.attach(self.progress_text, 1, 1, 3, 1)
        box.pack_start(self.progress_box, False, False, 6)

        self.result = Gtk.Label(xalign=0, wrap=True, use_markup=True)   # the outcome
        box.pack_start(self.result, False, False, 6)

        self.review = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.notes = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.review.pack_start(self.notes, False, False, 0)
        self.mirror = Gtk.CheckButton()
        self.mirror.connect("toggled", lambda _c: self._fill())
        self.review.pack_start(self.mirror, False, False, 2)
        self.summary = Gtk.Label(xalign=0, wrap=True)
        self.review.pack_start(self.summary, False, False, 2)
        tools = Gtk.Box(spacing=6)
        tools.pack_start(Gtk.Label(label="Untick anything you do not want to transfer:", xalign=0), False, False, 0)
        for label, on in (("Select all", True), ("Select none", False)):
            b = Gtk.Button(label=label)
            b.connect("clicked", lambda _b, on=on: self._set_all(on))
            tools.pack_end(b, False, False, 0)
        self.review.pack_start(tools, False, False, 0)
        self.store = Gtk.ListStore(bool, str, str, str, str, str, str)      # ticked, action, path, size, why, kind, rel
        self.tree = Gtk.TreeView(model=self.store)
        toggle = Gtk.CellRendererToggle()
        toggle.connect("toggled", self._toggled)
        self.tree.append_column(Gtk.TreeViewColumn("", toggle, active=0))
        for i, (title, width) in enumerate((("Action", 110), ("Path", 380), ("Size", 80), ("Why", 80)), start=1):
            col = Gtk.TreeViewColumn(title, Gtk.CellRendererText(), text=i)
            col.set_min_width(width)
            col.set_resizable(True)
            self.tree.append_column(col)
        self.review.pack_start(scrolled(self.tree), True, True, 0)
        box.pack_start(self.review, True, True, 0)

        self.cancel_btn = self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        self.sync_btn = self.add_button("Sync", Gtk.ResponseType.OK)
        self.sync_btn.set_sensitive(False)
        self.set_default_response(Gtk.ResponseType.OK)
        self.connect("response", self._response)
        self.connect("delete-event", self._delete)
        self.connect("destroy", self._destroyed)
        self.show_all()
        for part in (self.extra, self.result, self.review):
            part.hide()
        self.resize(720, 230)
        GLib.timeout_add(100, self._tick)
        if plan is not None:
            self._scanned(plan, None)
        else:
            self.job = app.runner.submit(lambda prog: build_plan(src_be, src, dst_be, dst, prog), self._scanned,
                                         "Comparing folders ...", inline=True)

    # -- phases ---------------------------------------------------------------------------------------------
    def _tick(self):
        """Every 100 ms: show what the running job is doing (this dialog owns its progress, not the status bar)."""
        if self.phase == "closed":
            return False
        if self.job is not None:
            p = self.job.prog
            if self.phase == "scanning":
                self.bar.pulse()
                self.progress_text.set_text(p.label or "Starting ...")
            elif self.phase == "transferring":
                self.bar.set_fraction(p.fraction())
                self.progress_text.set_text(p.describe())
        return True

    def _scanned(self, plan, exc):
        self.job = None
        if self.phase == "closed":
            return
        if isinstance(exc, Cancelled):
            self.app.set_status("Sync cancelled")
            self.destroy()
        elif exc:
            self.app.set_status("Sync failed")
            self._finish("Could not compare the folders: %s" % exc, error=True)
        elif not plan.actions:
            self.app.set_status("Folders are already in sync")
            self._finish("Nothing to do: the folders already match.")
        else:
            self._show_plan(plan)

    def _show_plan(self, plan):
        self.plan, self.phase = plan, "review"
        self.sel = {(a.kind, a.rel): True for a in plan.actions}       # ticked = will run
        deletes = plan.of("delete")
        self.groups = {}
        if len(deletes) > MAX_DELETE_ROWS:                             # too many to list one by one
            for a in deletes:
                self.groups.setdefault(a.rel.split("/")[0], []).append(a)
            self.sel.update({("delete-group", top): True for top in self.groups})
        self.ignoring.set_text(", ".join(plan.exclude))
        for note in plan.notes:
            self.notes.pack_start(Gtk.Label(label=note, xalign=0, wrap=True), False, False, 0)
        self.mirror.set_label("Also delete %d item(s) that exist only at the target (mirror)" % len(deletes))
        self.mirror.set_sensitive(bool(deletes))
        self._fill()
        self.progress_box.hide()
        self.extra.show_all()
        self.notes.show_all()
        self.review.show()
        for w in (self.ignoring, self.ignoring_caption):
            w.set_visible(bool(plan.exclude))
        self.resize(800, 580)

    def start_transfer(self, mirror, selected):
        plan = self.plan
        self.phase, self.started = "transferring", time.monotonic()
        self.review.hide()
        self.sync_btn.hide()
        self.bar.set_fraction(0)
        self.progress_box.show_all()
        self.resize(720, 230)
        copies = [a for a in plan.of("copy") if ("copy", a.rel) in selected]
        self._summary = (len(copies), sum(a.size for a in copies),
                         sum(k[0] == "delete" for k in selected) if mirror else 0)
        self.job = self.app.runner.submit(lambda prog: apply_plan(plan, prog, mirror, selected), self._applied,
                                          "Syncing ...", inline=True)

    def _applied(self, _res, exc):
        self.job = None
        self.app._refresh_all()
        if self.phase == "closed":
            return
        if isinstance(exc, Cancelled):
            self.app.set_status("Sync cancelled")
            self._finish("Cancelled. Some files may already have been copied; run the sync again to finish.")
        elif exc:
            self.app.set_status("Sync failed")
            self._finish("Sync failed: %s" % exc, error=True)
        else:
            files, size, deleted = self._summary
            text = "Done: %d file(s) (%s) copied" % (files, human(size))
            if deleted:
                text += ", %d item(s) deleted" % deleted
            self.app.set_status("Sync complete")
            self._finish("%s, in %s." % (text, human_duration(time.monotonic() - self.started)))

    def _finish(self, text, error=False):
        self.phase = "done"
        self.progress_box.hide()
        self.review.hide()
        self.sync_btn.hide()
        self.cancel_btn.set_label("Close")
        self.cancel_btn.set_sensitive(True)
        markup = GLib.markup_escape_text(text)
        self.result.set_markup('<span foreground="#c01c28">%s</span>' % markup if error else markup)
        self.result.show()
        self.resize(720, 230)

    def _cancel_request(self):
        self.cancel_btn.set_sensitive(False)
        self.cancel_btn.set_label("Cancelling ...")
        self.app.runner.cancel_all()

    def _response(self, _d, res):
        if res != Gtk.ResponseType.OK:
            if self.phase in ("scanning", "transferring"):
                self._cancel_request()              # the dialog closes or reports once the job has stopped
            else:
                self.destroy()
            return
        if self.phase != "review":
            return
        mirror, selected = self.mirror.get_active(), self._selected()
        deleting = [k[1] for k in selected if k[0] == "delete"]
        if deleting:
            tops = Counter(rel.split("/")[0] for rel in deleting).most_common(6)
            if not confirm_mirror_delete(self, self.plan.dst_be.label, self.plan.dst, len(deleting), tops):
                return                              # back to the review, nothing happens
        self.start_transfer(mirror, selected)

    def _delete(self, *_):
        if self.phase in ("scanning", "transferring"):
            self._cancel_request()
            return True                             # stay open until the job stops
        return False

    def _destroyed(self, _w):
        self.phase = "closed"

    # -- the review: what is listed and ticked --------------------------------------------------------------
    def _rows(self):
        """(kind, rel, action text, path, size text, why) for every listed row."""
        for a in self.plan.actions:
            if a.kind != "delete":
                size = "" if a.is_dir else human(a.size)
                yield a.kind, a.rel, LABELS[a.kind], a.rel or "(target folder)", size, a.reason
        if not self.mirror.get_active():
            return
        if self.groups:
            for top, acts in self.groups.items():
                yield "delete-group", top, "Delete %d" % len(acts), top + "/", human(sum(x.size for x in acts)), "extra"
        else:
            for a in self.plan.of("delete"):
                yield a.kind, a.rel, LABELS[a.kind], a.rel, "" if a.is_dir else human(a.size), a.reason

    def _selected(self):
        """The (kind, rel) of every action that will run."""
        chosen = {(a.kind, a.rel) for a in self.plan.actions if a.kind != "delete" and self.sel[(a.kind, a.rel)]}
        if self.mirror.get_active():
            if self.groups:
                for top, acts in self.groups.items():
                    if self.sel[("delete-group", top)]:
                        chosen.update(("delete", a.rel) for a in acts)
            else:
                chosen.update((a.kind, a.rel) for a in self.plan.of("delete") if self.sel[(a.kind, a.rel)])
        return chosen

    def _fill(self):
        self.store.clear()
        for kind, rel, action, path, size, why in self._rows():
            self.store.append([self.sel[(kind, rel)], action, path, size, why, kind, rel])
        self._summarise()

    def _refresh_ticks(self):
        for row in self.store:
            row[0] = self.sel[(row[5], row[6])]
        self._summarise()

    def _summarise(self):
        chosen = self._selected()
        copies_all = [a for a in self.plan.actions if a.kind == "copy"]
        copies = [a for a in copies_all if ("copy", a.rel) in chosen]
        need = sum(a.size for a in copies)
        parts = ["%d of %d file(s) to copy (%s)" % (len(copies), len(copies_all), human(need))]
        new = sum(a.reason == "new" for a in copies)
        if copies:
            parts.append("%d new, %d changed" % (new, len(copies) - new))
        folders = sum(k[0] == "mkdir" for k in chosen)
        if folders:
            parts.append("%d folder(s) to create" % folders)
        dels = sum(k[0] == "delete" for k in chosen)
        if dels:
            parts.append("%d to delete" % dels)
        parts.append("%d already identical" % self.plan.unchanged)
        free = self.plan.dst_free
        if free is None:
            space = GLib.markup_escape_text("free space on the target is unknown")
        elif need > free:
            warn = GLib.markup_escape_text("not enough: %s needed, %s free" % (human(need), human(free)))
            space = '<span foreground="#c01c28"><b>%s</b></span>' % warn
        else:
            space = GLib.markup_escape_text("%s needed, %s free" % (human(need), human(free)))
        self.space.set_markup(space)
        self.summary.set_markup("<b>%s</b>" % GLib.markup_escape_text(",  ".join(parts)))
        self.sync_btn.set_sensitive(bool(chosen) and self.phase == "review")

    def _set_all(self, on):
        for row in self.store:
            self.sel[(row[5], row[6])] = on
        self._refresh_ticks()

    def _toggled(self, _renderer, path):
        kind, rel = self.store[path][5], self.store[path][6]
        if kind == "delete-group":
            self.sel[(kind, rel)] = not self.sel[(kind, rel)]
        else:
            action = next(a for a in self.plan.actions if (a.kind, a.rel) == (kind, rel))
            self._set(action, not self.sel[(kind, rel)])
        self._refresh_ticks()

    def _set(self, a, on):
        """Tick or untick one item, keeping folders consistent: a file needs its new folders, a folder takes what is
        inside it along, and a folder is only deleted once everything inside it is."""
        def below(x, top):        # x is inside top ("" is the target folder itself)
            return x.rel != top.rel and (top.rel == "" or x.rel.startswith(top.rel + "/"))
        make = ("mkdir", "copy")
        acts = self.plan.actions
        self.sel[(a.kind, a.rel)] = on
        if a.kind == "delete":
            if on and a.is_dir:
                for x in acts:
                    if x.kind == "delete" and below(x, a):
                        self.sel[("delete", x.rel)] = True
            if not on:
                for x in acts:
                    if x.kind == "delete" and x.is_dir and below(a, x):
                        self.sel[("delete", x.rel)] = False
            return
        if on:
            for x in acts:
                if x.kind == "mkdir" and below(a, x):
                    self.sel[("mkdir", x.rel)] = True
        if a.kind == "mkdir":
            for x in acts:
                if x.kind in make and below(x, a):
                    self.sel[(x.kind, x.rel)] = on


class TransferPopup(Gtk.Window):
    """A small non-modal progress window for a file job that is taking a while: what it is doing, a centred bar, the
    transfer line, and Cancel. The app opens it after a second and closes it when the job ends."""

    def __init__(self, app, job):
        super().__init__(title="Working ...", transient_for=app)
        self.app, self.job = app, job
        self.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        self.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
        self.set_focus_on_map(False)                        # do not pull the keyboard away from the main window
        self.set_default_size(720, 160)
        self.set_icon(app.get_icon())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=12)
        self.add(box)
        grid = Gtk.Grid(column_homogeneous=True, row_spacing=4)
        grid.attach(Gtk.Label(label=""), 0, 0, 1, 4)        # equal side columns: 20% | 60% | 20%
        grid.attach(Gtk.Label(label=""), 4, 0, 1, 4)
        self.heading = Gtk.Label(xalign=0, ellipsize=3, hexpand=True)
        self.bar = Gtk.ProgressBar(hexpand=True)
        self.file = Gtk.Label(xalign=0, ellipsize=3, hexpand=True)          # the file being copied
        self.text = Gtk.Label(xalign=0, ellipsize=3, hexpand=True)          # done of total, speed, time left
        grid.attach(self.heading, 1, 0, 3, 1)
        grid.attach(self.bar, 1, 1, 3, 1)
        grid.attach(self.file, 1, 2, 3, 1)
        grid.attach(self.text, 1, 3, 3, 1)
        box.pack_start(grid, True, True, 0)
        buttons = Gtk.Box(spacing=6)
        self.cancel_btn = Gtk.Button(label="Cancel")
        self.cancel_btn.connect("clicked", lambda _b: self._cancel())
        hide = Gtk.Button(label="Hide")
        hide.set_tooltip_text("Close this window; the job keeps running (see the status bar)")
        hide.connect("clicked", lambda _b: self.dismiss())
        buttons.pack_end(self.cancel_btn, False, False, 0)
        buttons.pack_end(hide, False, False, 0)
        box.pack_start(buttons, False, False, 0)
        self.connect("delete-event", lambda *_: self.dismiss() or True)
        self.update()
        self.show_all()

    def update(self):
        p = self.job.prog
        self.heading.set_markup("<b>%s</b>" % GLib.markup_escape_text(self.job.status))
        if p.known():
            self.bar.set_fraction(p.fraction())
        else:
            self.bar.pulse()
        self.file.set_text(p.label)
        self.text.set_text(p.describe(with_label=False))

    def _cancel(self):
        self.cancel_btn.set_sensitive(False)
        self.cancel_btn.set_label("Cancelling ...")
        self.app.runner.cancel_all()

    def dismiss(self):
        """Close the window but leave the job running; it is not shown again for this job."""
        self.app._popup_dismissed = True
        self.close_window()

    def close_window(self):
        self.app._popup = None
        self.destroy()


class OutputWindow(Gtk.Window):
    """Output of a script run on the device."""

    def __init__(self, app, path, out, exc):
        super().__init__(title="Output: " + path, transient_for=app)
        self.set_default_size(760, 420)
        tv = monospace_view()
        tv.get_buffer().set_text(exc.traceback if isinstance(exc, DeviceError) else (str(exc) if exc else out))
        self.add(scrolled(tv))
        self.show_all()

