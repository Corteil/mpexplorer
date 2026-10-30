"""Conflict prompt, sync dialogs and script output windows."""
from collections import Counter

from ..backends import human
from ..transport import DeviceError
from .common import GLib, Gtk, monospace_view, scrolled

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
        later = Gtk.Label(label="Next you will see what would change; nothing is copied until you press Sync.",
                          xalign=0, wrap=True, sensitive=False)
        box.pack_start(later, False, False, 0)
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
    """What a folder sync would do: source, target, free space, and every item with a tick box to leave it out."""

    def __init__(self, app, plan):
        super().__init__(title="Sync folders", transient_for=app)
        self.app, self.plan = app, plan
        self.sel = {(a.kind, a.rel): True for a in plan.actions}       # ticked = will run
        self.groups = {}                                               # many deletions: top-level folder -> [actions]
        deletes = plan.of("delete")
        if len(deletes) > MAX_DELETE_ROWS:
            for a in deletes:
                self.groups.setdefault(a.rel.split("/")[0], []).append(a)
            self.sel.update({("delete-group", top): True for top in self.groups})
        self.set_default_size(800, 580)
        box = self.get_content_area()
        box.set_spacing(6)
        box.set_border_width(10)

        grid = Gtk.Grid(column_spacing=10, row_spacing=3)
        for i, (title, text) in enumerate((
                ("From", "%s:  %s" % (plan.src_be.label, plan.src)),
                ("To", "%s:  %s" % (plan.dst_be.label, plan.dst)))):
            grid.attach(Gtk.Label(label=title, xalign=1, sensitive=False), 0, i, 1, 1)
            path = Gtk.Label(label=text, xalign=0, selectable=True, hexpand=True)
            path.set_ellipsize(1)       # 1 = start: keep the end of a long path
            grid.attach(path, 1, i, 1, 1)
        self.space = Gtk.Label(xalign=0, use_markup=True)
        grid.attach(Gtk.Label(label="Space", xalign=1, sensitive=False), 0, 2, 1, 1)
        grid.attach(self.space, 1, 2, 1, 1)
        if plan.exclude:
            grid.attach(Gtk.Label(label="Ignoring", xalign=1, sensitive=False), 0, 3, 1, 1)
            grid.attach(Gtk.Label(label=", ".join(plan.exclude), xalign=0), 1, 3, 1, 1)
        box.pack_start(grid, False, False, 0)
        for note in plan.notes:
            box.pack_start(Gtk.Label(label=note, xalign=0, wrap=True), False, False, 0)

        n_del = len(deletes)
        self.mirror = Gtk.CheckButton(
            label="Also delete %d item(s) that exist only at the target (mirror)" % n_del)
        self.mirror.set_sensitive(bool(n_del))
        self.mirror.connect("toggled", lambda _c: self._fill())
        box.pack_start(self.mirror, False, False, 2)
        self.summary = Gtk.Label(xalign=0, wrap=True)
        box.pack_start(self.summary, False, False, 2)

        tools = Gtk.Box(spacing=6)
        tools.pack_start(Gtk.Label(label="Untick anything you do not want to transfer:", xalign=0), False, False, 0)
        for label, on in (("Select all", True), ("Select none", False)):
            b = Gtk.Button(label=label)
            b.connect("clicked", lambda _b, on=on: self._set_all(on))
            tools.pack_end(b, False, False, 0)
        box.pack_start(tools, False, False, 0)

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
        box.pack_start(scrolled(self.tree), True, True, 0)

        self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        self.sync_btn = self.add_button("Sync", Gtk.ResponseType.OK)
        self.set_default_response(Gtk.ResponseType.OK)
        self._fill()
        self.connect("response", self._response)
        self.show_all()

    # -- what is shown and ticked --------------------------------------------------------------------------
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
        self.sync_btn.set_sensitive(bool(chosen))

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

    def _response(self, _d, res):
        mirror, selected = self.mirror.get_active(), self._selected()
        if res == Gtk.ResponseType.OK:
            deleting = [k[1] for k in selected if k[0] == "delete"]
            if deleting:
                tops = Counter(rel.split("/")[0] for rel in deleting).most_common(6)
                if not confirm_mirror_delete(self, self.plan.dst_be.label, self.plan.dst, len(deleting), tops):
                    return                  # back to the dialog, nothing happens
        self.destroy()
        if res == Gtk.ResponseType.OK:
            self.app.run_sync(self.plan, mirror, selected)


class OutputWindow(Gtk.Window):
    """Output of a script run on the device."""

    def __init__(self, app, path, out, exc):
        super().__init__(title="Output: " + path, transient_for=app)
        self.set_default_size(760, 420)
        tv = monospace_view()
        tv.get_buffer().set_text(exc.traceback if isinstance(exc, DeviceError) else (str(exc) if exc else out))
        self.add(scrolled(tv))
        self.show_all()

