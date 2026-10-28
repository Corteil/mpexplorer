"""One directory listing bound to a backend (host or device)."""
from gi.repository import Gdk, GLib, Gtk

from ..backends import human
from .common import button, error, fmt_time, scrolled

DND_TARGET = [Gtk.TargetEntry.new("application/x-mpexplorer-items", Gtk.TargetFlags.SAME_APP, 0)]


class Pane(Gtk.Frame):
    def __init__(self, app, title):
        super().__init__(label=title)
        self.app = app
        self.title = title
        self.backend = None
        self.cwd = ""
        self._good = ""
        self._entries = []
        self._token = 0
        self._sort = ("name", False)
        self.paused = False             # True while a terminal owns the device

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin=2)
        self.add(box)
        bar = Gtk.Box(spacing=4)
        bar.pack_start(button("Up", self.up), False, False, 0)
        bar.pack_start(button("Refresh", self.refresh), False, False, 0)
        self.path_entry = Gtk.Entry()
        self.path_entry.connect("activate", lambda e: self.go(e.get_text()))
        bar.pack_start(self.path_entry, True, True, 0)
        bar.pack_start(Gtk.Label(label="Filter"), False, False, 0)
        self.filter_entry = Gtk.Entry(width_chars=12)
        self.filter_entry.connect("changed", lambda _e: self._render())
        bar.pack_start(self.filter_entry, False, False, 0)
        box.pack_start(bar, False, False, 0)

        # icon, name, size text, modified text
        self.store = Gtk.ListStore(str, str, str, str)
        self.tree = Gtk.TreeView(model=self.store, headers_clickable=True, enable_search=False)
        self.tree.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        name_col = Gtk.TreeViewColumn("Name")
        icon_cell, text_cell = Gtk.CellRendererPixbuf(), Gtk.CellRendererText()
        name_col.pack_start(icon_cell, False)
        name_col.pack_start(text_cell, True)
        name_col.add_attribute(icon_cell, "icon-name", 0)
        name_col.add_attribute(text_cell, "text", 1)
        name_col.set_resizable(True)
        name_col.set_min_width(240)
        size_cell = Gtk.CellRendererText(xalign=1.0)
        size_col = Gtk.TreeViewColumn("Size", size_cell, text=2)
        mod_col = Gtk.TreeViewColumn("Modified", Gtk.CellRendererText(), text=3)
        self._cols = {"name": name_col, "size": size_col, "modified": mod_col}
        for key, col in self._cols.items():
            col.set_clickable(True)
            col.connect("clicked", lambda _c, k=key: self.sort_by(k))
            self.tree.append_column(col)
        box.pack_start(scrolled(self.tree), True, True, 0)

        t = self.tree
        t.connect("row-activated", lambda *_: self._open())
        t.connect("key-press-event", self._key)
        t.connect("button-press-event", self._press)
        t.connect("focus-in-event", lambda *_: app.set_active(self))
        t.enable_model_drag_source(Gdk.ModifierType.BUTTON1_MASK, DND_TARGET, Gdk.DragAction.COPY)
        t.enable_model_drag_dest(DND_TARGET, Gdk.DragAction.COPY)
        t.connect("drag-begin", lambda _w, _c: app.set_drag_source(self))
        t.connect("drag-data-get", lambda _w, _c, sel, _i, _t: sel.set(sel.get_target(), 8, b"items"))
        t.connect("drag-data-received", self._dropped)
        self._set_label(title + " (not connected)")

    # -- state -------------------------------------------------------------
    def _set_label(self, text):
        self.set_label(text)

    def set_location(self, backend, path):
        self.backend, self.cwd, self._good = backend, path, ""
        self._set_label(self.title if backend else self.title + " (not connected)")
        self.refresh()

    def set_paused(self, paused):
        self.paused = paused
        if self.backend:
            self._set_label(self.title + (" (terminal active - paused)" if paused else ""))
            if not paused:
                self.refresh()

    def clear(self):
        self.paused = False
        self.backend = None
        self._entries = []
        self._set_label(self.title + " (not connected)")
        self.path_entry.set_text("")
        self._render()

    def select_all(self):
        self.tree.get_selection().select_all()

    def selected(self):
        """[(name, full_path)] for the selected rows."""
        if not self.backend or self.paused:
            return []
        model, paths = self.tree.get_selection().get_selected_rows()
        return [(model[p][1], self.backend.join(self.cwd, model[p][1])) for p in paths]

    def entry(self, name):
        return next((e for e in self._entries if e.name == name), None)

    # -- navigation --------------------------------------------------------
    def go(self, path):
        if self.backend:
            self.cwd = path.strip() or self.backend.root
            self.refresh()

    def up(self):
        if self.backend:
            self.go(self.backend.parent(self.cwd))

    def refresh(self):
        if not self.backend or self.paused:
            return
        self.path_entry.set_text(self.cwd)
        self._token += 1
        token, be, path = self._token, self.backend, self.cwd
        if be.key == "local":
            try:
                self._loaded(token, be.listdir(path), None)
            except Exception as e:
                self._loaded(token, None, e)
        else:
            self.app.runner.submit(lambda prog: be.listdir(path),
                                   on_done=lambda res, exc: self._loaded(token, res, exc),
                                   label="Listing " + path, quiet=True)

    def _loaded(self, token, entries, exc):
        if token != self._token:
            return
        if exc:
            bad = self.cwd
            if self._good:
                self.cwd = self._good
                self.path_entry.set_text(self.cwd)
            error(self.app, "Cannot open folder", "%s\n\n%s" % (bad, exc))
            return
        self._good = self.cwd
        self._entries = entries
        self._render()

    def _open(self):
        sel = self.selected()
        if len(sel) != 1:
            return
        e = self.entry(sel[0][0])
        if e and e.is_dir:
            self.go(sel[0][1])
        elif e:
            self.app.open_file(self, sel[0][1], e.size)

    # -- display -----------------------------------------------------------
    def sort_by(self, key):
        self._sort = (key, not self._sort[1] if self._sort[0] == key else False)
        self._render()

    def _render(self):
        keep = {n for n, _p in self.selected()} if self.backend else set()
        flt = self.filter_entry.get_text().lower()
        show_hidden = self.app.show_hidden
        rows = [e for e in self._entries
                if (show_hidden or not e.name.startswith(".")) and flt in e.name.lower()]
        key = {"name": lambda e: e.name.lower(), "size": lambda e: e.size,
               "modified": lambda e: e.mtime}[self._sort[0]]
        rows.sort(key=key, reverse=self._sort[1])
        rows.sort(key=lambda e: not e.is_dir)       # folders always first
        sel = self.tree.get_selection()
        self.store.clear()
        for e in rows:
            it = self.store.append(["folder" if e.is_dir else "text-x-generic", e.name,
                                    "" if e.is_dir else human(e.size), fmt_time(e.mtime)])
            if e.name in keep:
                sel.select_iter(it)
        for k, col in self._cols.items():
            col.set_sort_indicator(k == self._sort[0])
            col.set_sort_order(Gtk.SortType.DESCENDING if self._sort[1] else Gtk.SortType.ASCENDING)

    # -- keys, context menu and drag between panes ------------------------
    def _key(self, _w, ev):
        ctrl = ev.state & Gdk.ModifierType.CONTROL_MASK
        k = Gdk.keyval_name(ev.keyval)
        app = self.app
        actions = {"Delete": app.delete, "F2": app.rename, "F5": self.refresh, "F7": app.new_folder,
                   "BackSpace": self.up}
        if ctrl:
            actions = {"c": lambda: app.copy("copy"), "x": lambda: app.copy("cut"), "v": app.paste,
                       "a": self.select_all}
            k = k.lower() if k else k
        if k in actions:
            actions[k]()
            return True
        return False

    def _press(self, _w, ev):
        if ev.type == Gdk.EventType.BUTTON_PRESS and ev.button == 3:
            hit = self.tree.get_path_at_pos(int(ev.x), int(ev.y))
            if hit and not self.tree.get_selection().path_is_selected(hit[0]):
                self.tree.get_selection().unselect_all()
                self.tree.get_selection().select_path(hit[0])
            self.tree.grab_focus()
            self.app.context_menu().popup_at_pointer(ev)
            return True
        return False

    def _dropped(self, _w, ctx, _x, _y, _sel, _info, time):
        src = self.app.drag_source
        done = bool(src and src is not self)
        Gtk.drag_finish(ctx, done, False, time)
        if done:
            GLib.idle_add(self.app.transfer, src, self)
