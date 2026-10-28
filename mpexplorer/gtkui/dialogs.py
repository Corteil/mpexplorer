"""Conflict prompt, sync preview and script output windows."""
from ..backends import human
from ..transport import DeviceError
from .common import Gtk, monospace_view, scrolled

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
    """Shows what a folder sync would do and lets the user apply it."""

    def __init__(self, app, plan):
        super().__init__(title="Sync folders", transient_for=app)
        self.app, self.plan = app, plan
        self.set_default_size(680, 460)
        box = self.get_content_area()
        box.set_spacing(4)
        box.set_border_width(8)
        box.pack_start(Gtk.Label(label="%s  >>  %s" % (plan.src, plan.dst), xalign=0), False, False, 0)
        self.summary = Gtk.Label(xalign=0)
        box.pack_start(self.summary, False, False, 0)
        for note in plan.notes:
            lab = Gtk.Label(label=note, xalign=0)
            box.pack_start(lab, False, False, 0)
        n_del = len(plan.of("delete"))
        self.mirror = Gtk.CheckButton(
            label="Also delete %d item(s) that exist only at the destination (mirror)" % n_del)
        self.mirror.set_sensitive(bool(n_del))
        self.mirror.connect("toggled", lambda _c: self._fill())
        box.pack_start(self.mirror, False, False, 4)
        self.store = Gtk.ListStore(str, str, str)
        tree = Gtk.TreeView(model=self.store)
        for i, (title, width) in enumerate((("Action / path", 380), ("Size", 80), ("Why", 90))):
            col = Gtk.TreeViewColumn(title, Gtk.CellRendererText(), text=i)
            col.set_min_width(width)
            col.set_resizable(True)
            tree.append_column(col)
        box.pack_start(scrolled(tree), True, True, 0)
        self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        self.apply_btn = self.add_button("Apply", Gtk.ResponseType.OK)
        self._fill()
        self.connect("response", self._response)
        self.show_all()

    def _shown(self):
        return [a for a in self.plan.actions if a.kind != "delete" or self.mirror.get_active()]

    def _fill(self):
        self.store.clear()
        shown = self._shown()
        for a in shown:
            self.store.append(["%s  %s" % (LABELS[a.kind], a.rel or "(folder)"),
                               "" if a.is_dir else human(a.size), a.reason])
        copies = [a for a in shown if a.kind == "copy"]
        self.summary.set_text("%d to copy (%s), %d folder(s) to create, %d to delete, %d already identical" % (
            len(copies), human(sum(a.size for a in copies)), sum(a.kind == "mkdir" for a in shown),
            sum(a.kind == "delete" for a in shown), self.plan.unchanged))
        self.apply_btn.set_sensitive(bool(shown))

    def _response(self, _d, res):
        mirror = self.mirror.get_active()
        self.destroy()
        if res == Gtk.ResponseType.OK:
            self.app.run_sync(self.plan, mirror)


class OutputWindow(Gtk.Window):
    """Output of a script run on the device."""

    def __init__(self, app, path, out, exc):
        super().__init__(title="Output: " + path, transient_for=app)
        self.set_default_size(760, 420)
        tv = monospace_view()
        tv.get_buffer().set_text(exc.traceback if isinstance(exc, DeviceError) else (str(exc) if exc else out))
        self.add(scrolled(tv))
        self.show_all()

