"""Conflict prompt, sync preview and script output windows."""
from ..backends import human
from ..transport import DeviceError
from .common import GLib, Gtk, monospace_view, scrolled

LABELS = {"mkdir": "Create folder", "copy": "Copy", "delete": "Delete"}


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


class SyncDialog(Gtk.Dialog):
    """What a folder sync would do: source, target, free space, and every item with a tick box to leave it out."""

    def __init__(self, app, plan):
        super().__init__(title="Sync folders", transient_for=app)
        self.app, self.plan = app, plan
        self.sel = {(a.kind, a.rel): True for a in plan.actions}       # ticked = will run
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
        hint = Gtk.Label(label="The target is made to match the source. Files are written under a temporary name "
                               "first, so cancelling never loses an old copy.", xalign=0, wrap=True, sensitive=False)
        box.pack_start(hint, False, False, 0)
        for note in plan.notes:
            box.pack_start(Gtk.Label(label=note, xalign=0, wrap=True), False, False, 0)

        n_del = len(plan.of("delete"))
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
    def _shown(self):
        return [a for a in self.plan.actions if a.kind != "delete" or self.mirror.get_active()]

    def _selected(self):
        return {(a.kind, a.rel) for a in self._shown() if self.sel[(a.kind, a.rel)]}

    def _fill(self):
        self.store.clear()
        for a in self._shown():
            self.store.append([self.sel[(a.kind, a.rel)], LABELS[a.kind], a.rel or "(folder)",
                               "" if a.is_dir else human(a.size), a.reason, a.kind, a.rel])
        self._summarise()

    def _refresh_ticks(self):
        for row in self.store:
            row[0] = self.sel[(row[5], row[6])]
        self._summarise()

    def _summarise(self):
        shown = self._shown()
        ticked = [a for a in shown if self.sel[(a.kind, a.rel)]]
        copies_all = [a for a in shown if a.kind == "copy"]
        copies = [a for a in ticked if a.kind == "copy"]
        need = sum(a.size for a in copies)
        parts = ["%d of %d file(s) to copy (%s)" % (len(copies), len(copies_all), human(need))]
        new = sum(a.reason == "new" for a in copies)
        if copies:
            parts.append("%d new, %d changed" % (new, len(copies) - new))
        folders = sum(a.kind == "mkdir" for a in ticked)
        if folders:
            parts.append("%d folder(s) to create" % folders)
        dels = sum(a.kind == "delete" for a in ticked)
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
        self.sync_btn.set_sensitive(bool(ticked))

    def _set_all(self, on):
        for a in self._shown():
            self.sel[(a.kind, a.rel)] = on
        self._refresh_ticks()

    def _toggled(self, _renderer, path):
        kind, rel = self.store[path][5], self.store[path][6]
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

