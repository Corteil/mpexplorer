"""One directory listing bound to a backend (host or device)."""
from .. import boardinfo
from ..backends import human
from .common import Gdk, GLib, Gtk, error, fmt_time, icon_button, scrolled

HOST = "Host"
DRAG_ICON_X, DRAG_ICON_Y = 34, 22   # this point of the drag icon sits under the pointer: larger moves it left, or up
DND_TARGET = [Gtk.TargetEntry.new("application/x-mpexplorer-items", Gtk.TargetFlags.SAME_APP, 0)]


_badge_css_installed = []


def install_badge_css():
    """The firmware badges (CP / MP): white bold text on a filled rounded block, a fill per firmware (once)."""
    if _badge_css_installed or Gdk.Screen.get_default() is None:
        return
    css = ".fw-badge { color: #ffffff; font-weight: bold; border-radius: 3px; padding: 0 5px; margin: 1px 0; }\n"
    css += "".join(".fw-%s { background-color: %s; }\n" % (name, badge.fill) for name, badge in (
        ("mp", boardinfo.BADGES[boardinfo.MICROPYTHON]), ("cp", boardinfo.BADGES[boardinfo.CIRCUITPYTHON]),
        ("unknown", boardinfo.BADGES[None])))
    provider = Gtk.CssProvider()
    provider.load_from_data(css.encode())
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider,
                                             Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _badge_css_installed.append(True)


KIND_CLASS = {boardinfo.MICROPYTHON: "fw-mp", boardinfo.CIRCUITPYTHON: "fw-cp", None: "fw-unknown"}


class Pane(Gtk.Frame):
    def __init__(self, app, title):
        super().__init__(label=title)
        self.app = app
        self.title = title
        self.detail = ""           # what the pane is attached to, e.g. '/dev/ttyACM2  Pico 2 (RP2350)'
        self.backend = None
        self.cwd = ""
        self._good = ""
        self._entries = []
        self._token = 0
        self._sort = ("name", False)
        self.paused = False             # True while a terminal owns the device
        self.kinds = {}                 # source text -> the firmware its board runs (from the port scan)
        self.live_kind = None           # what the connected board really runs, once connected
        install_badge_css()

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin=2)
        self.add(box)
        source = Gtk.Box(spacing=4)
        self.filling = False
        self.badge = Gtk.Label(no_show_all=True, valign=Gtk.Align.CENTER)
        self.badge.get_style_context().add_class("fw-badge")
        source.pack_start(self.badge, False, False, 0)
        self.source_box = Gtk.ComboBoxText.new_with_entry()
        self.badge_cell = Gtk.CellRendererText(weight=700, xpad=5, ypad=1, foreground="#ffffff")
        self.source_box.pack_start(self.badge_cell, False)
        self.source_box.reorder(self.badge_cell, 0)                    # in the drop-down rows: the badge, then the text
        self.source_box.set_cell_data_func(self.badge_cell, self._badge_data)
        self.source_box.set_size_request(120, -1)
        self.source_box.get_child().set_width_chars(10)
        self.source_box.connect("changed", lambda _b: self._source_changed())
        self.source_box.connect("changed", lambda _b: self._update_badge())
        source.pack_start(self.source_box, True, True, 0)
        self.connect_btn = icon_button("network-offline", "Connect to the board",
                                       lambda: app.toggle_connection(self), "Connect")
        source.pack_start(self.connect_btn, False, False, 0)
        box.pack_start(source, False, False, 0)
        bar = Gtk.Box(spacing=4)
        bar.pack_start(icon_button("go-up", "Parent folder (Backspace)", self.up, "Up"), False, False, 0)
        bar.pack_start(icon_button("view-refresh", "Refresh (F5)", self.refresh, "Refresh"), False, False, 0)
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
        t.connect_after("drag-begin", lambda _w, ctx: self._drag_icon(ctx))     # after: GTK sets the whole row first
        t.connect("drag-data-get", lambda _w, _c, sel, _i, _t: sel.set(sel.get_target(), 8, b"items"))
        t.connect("drag-data-received", self._dropped)
        self._set_label(self._caption(" (not connected)"))

    # -- state -------------------------------------------------------------
    def _caption(self, state=""):
        """'Device: /dev/ttyACM2  Pico 2 (RP2350)' (or just 'Host'), then the state in brackets if there is one."""
        return self.title + (": " + self.detail if self.detail else "") + state

    def _set_label(self, text):
        self.set_label(text)

    def source(self):
        return self.source_box.get_child().get_text().strip()

    # -- the firmware badge ---------------------------------------------------
    def _badge_data(self, _layout, cell, model, it, _data=None):
        """Drop-down rows: a CP / MP badge before each port's text, nothing before Host."""
        text = model[it][0]
        if text == HOST:
            cell.set_property("text", "")
            cell.set_property("background-set", False)
            return
        badge = boardinfo.BADGES[self.kinds.get(text)]
        cell.set_property("text", badge.text)
        cell.set_property("background", badge.fill)

    def badge_kind(self):
        """(shown, kind): the badge beside the box. What the connected board runs beats the scan's guess."""
        text = self.source()
        if not text or text == HOST:
            return False, None
        if self.live_kind and self.backend is not None and self.backend.key == "device":
            return True, self.live_kind
        return True, self.kinds.get(text)

    def _update_badge(self):
        shown, kind = self.badge_kind()
        ctx = self.badge.get_style_context()
        for name in KIND_CLASS.values():
            ctx.remove_class(name)
        ctx.add_class(KIND_CLASS[kind])
        self.badge.set_text(boardinfo.BADGES[kind].text)
        self.badge.set_visible(shown)

    def set_kind(self, kind):
        """Connect and disconnect tell the pane what the board really runs (None: no longer connected)."""
        self.live_kind = kind
        self._update_badge()

    def fill_sources(self, ports, choice=None, kinds=None):
        """Host, then the ports. What is chosen stays, unless choice says otherwise. kinds: port text -> firmware."""
        text = self.source() if choice is None else choice
        if kinds is not None:
            self.kinds = dict(kinds)
        self.filling = True
        self.source_box.remove_all()
        self.source_box.append_text(HOST)
        for port in ports:
            self.source_box.append_text(port)
        self.source_box.get_child().set_text(text)
        self.filling = False
        self._update_badge()

    def _source_changed(self):
        if not self.filling:
            self.app.source_changed(self)

    def set_location(self, backend, path):
        self.title = "Device" if backend and backend.key == "device" else "Host" if backend else self.title
        self.backend, self.cwd, self._good = backend, path, ""
        self._set_label(self._caption() if backend else self._caption(" (not connected)"))
        self.refresh()

    def set_paused(self, paused):
        self.paused = paused
        if self.backend:
            self._set_label(self._caption(" (terminal active - paused)" if paused else ""))
            if not paused:
                self.refresh()

    def clear(self):
        self.live_kind = None
        self._update_badge()
        self.paused = False
        self.backend = None
        self._entries = []
        self.detail = ""
        self._set_label(self._caption(" (not connected)"))
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
            self.app.runner.submit(lambda prog: be.listdir(path, mtimes=self.app.settings.get("general.device_mtimes")),
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
        self.app.folder_changed(self)

    def _open(self):
        sel = self.selected()
        if len(sel) != 1:
            return
        e = self.entry(sel[0][0])
        if e and e.is_dir:
            self.go(sel[0][1])
        elif e:
            self.app.open_default(self, sel[0][1], e.size)

    # -- display -----------------------------------------------------------
    def sort_by(self, key):
        self._sort = (key, not self._sort[1] if self._sort[0] == key else False)
        self._render()
        self.app.sort_changed(self)

    def set_sort(self, key, reverse):
        """Used at start-up to restore the remembered order (no notification, no re-save)."""
        self._sort = (key, reverse)
        self._render()

    def _render(self):
        keep = {n for n, _p in self.selected()} if self.backend else set()
        flt = self.filter_entry.get_text().lower()
        show_hidden = self.app.show_hidden
        rows = [e for e in self._entries
                if (show_hidden or not e.name.startswith(".")) and flt in e.name.lower()]
        show_mtime = self.title == "Host" or self.app.settings.get("general.device_mtimes")
        order = self._sort[0] if show_mtime or self._sort[0] != "modified" else "name"     # no times: sort by name
        key = {"name": lambda e: e.name.lower(), "size": lambda e: e.size,
               "modified": lambda e: e.mtime}[order]
        rows.sort(key=key, reverse=self._sort[1])
        rows.sort(key=lambda e: not e.is_dir)       # folders always first
        self._cols["modified"].set_visible(show_mtime)
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
        if k in ("Return", "KP_Enter") and ev.state & Gdk.ModifierType.SHIFT_MASK and not ctrl:
            app.open_with_selected()
            return True
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

    def _drag_icon(self, ctx):
        """Drag a small icon, not a picture of the whole row: the item's own icon, or a stack for several."""
        model, paths = self.tree.get_selection().get_selected_rows()
        name = model[paths[0]][0] if len(paths) == 1 else "edit-copy"
        Gtk.drag_set_icon_name(ctx, name or "text-x-generic", DRAG_ICON_X, DRAG_ICON_Y)

    def folder_at(self, x, y):
        """The full path of the folder under a drop, if there is one."""
        hit = self.tree.get_dest_row_at_pos(x, y)
        name = self.store[hit[0]][1] if hit else None
        entry = self.entry(name) if name else None
        return self.backend.join(self.cwd, name) if entry and entry.is_dir else None

    def _dropped(self, _w, ctx, x, y, _sel, _info, time):
        src = self.app.drag_source
        folder = self.folder_at(x, y) if self.backend else None
        Gtk.drag_finish(ctx, bool(src), False, time)
        if src:
            GLib.idle_add(self.app.dropped, src, self, folder)
