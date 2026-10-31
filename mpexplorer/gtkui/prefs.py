"""Preferences window: changes apply and are saved as soon as they are made."""
from ..openwith import describe_handler, forget_handler, normalize_extension, remember_handler
from .common import Gio, Gtk, ask_yes_no, error, info, prompt, scrolled
from .dialogs import ask_open_with

GENERAL = [
    ("general.show_hidden", "Show hidden files (names starting with a dot)"),
    ("general.remember_window", "Remember the window size and position"),
    ("general.remember_folders", "Remember the last folders (host, and one per board)"),
    ("general.remember_port", "Remember the last serial port"),
    ("general.badge_screen", "Show a status screen on the badge while connected (from the next connect)"),
    ("general.reset_on_disconnect", "Restart the board when disconnecting or quitting (brings a badge's screen back)"),
    ("general.set_board_clock", "Set the board's clock (UTC) to this computer's time when connecting"),
    ("open_with.auto_upload", "Upload saves of device files edited in other programs automatically"),
    ("general.device_mtimes", "Show modified times for device files (slow: about 40 ms per file on a Tildagon)"),
]


class PreferencesDialog(Gtk.Dialog):
    def __init__(self, app):
        super().__init__(title="Preferences", transient_for=app, modal=True)
        self.app, self.settings = app, app.settings
        self.add_button("Close", Gtk.ResponseType.CLOSE)
        self.set_default_size(520, 360)
        self.checks = {}
        nb = Gtk.Notebook(margin=6)
        self.get_content_area().pack_start(nb, True, True, 0)

        general = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=12)
        for key, label in GENERAL:
            cb = Gtk.CheckButton(label=label)
            cb.set_active(self.settings.get(key))
            cb.connect("toggled", self._toggled, key)
            self.checks[key] = cb
            general.pack_start(cb, False, False, 0)
        nb.append_page(general, Gtk.Label(label="General"))

        nb.append_page(self._file_types_page(), Gtk.Label(label="File types"))

        files = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=12)
        files.pack_start(Gtk.Label(label="Settings are stored in:", xalign=0), False, False, 0)
        path = Gtk.Label(label=self.settings.path or "(memory only)", xalign=0, selectable=True, wrap=True)
        files.pack_start(path, False, False, 0)
        self.problems = Gtk.Label(xalign=0, wrap=True, selectable=True)
        files.pack_start(self.problems, False, False, 0)
        reset = Gtk.Button(label="Reset all settings...", halign=Gtk.Align.START)
        reset.connect("clicked", lambda _b: self._reset())
        files.pack_start(reset, False, False, 6)
        nb.append_page(files, Gtk.Label(label="Settings file"))
        self._show_problems()
        self.settings.subscribe(self._setting_changed)
        self.connect("destroy", lambda _d: self.settings.unsubscribe(self._setting_changed))
        self.show_all()

    # -- File types: the default program for each extension (Open with...) ------
    def _file_types_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=12)
        note = Gtk.Label(label="Double-click or Enter opens a file with the program saved for its extension; "
                         "other extensions open in the built-in editor.", xalign=0, wrap=True)
        page.pack_start(note, False, False, 0)
        self.types = Gtk.ListStore(str, str)                            # extension, what it opens with
        self.types_view = Gtk.TreeView(model=self.types)
        for i, title in enumerate(("Extension", "Opens with")):
            self.types_view.append_column(Gtk.TreeViewColumn(title, Gtk.CellRendererText(), text=i))
        self.types_view.get_selection().connect("changed", self._types_selection)
        self.types_view.connect("row-activated", lambda *_: self._change_type())
        page.pack_start(scrolled(self.types_view), True, True, 0)
        row = Gtk.Box(spacing=6)
        self.type_buttons = {}
        for label, callback in (("Add...", self._add_type), ("Change...", self._change_type),
                                ("Remove", self._remove_type)):
            b = Gtk.Button(label=label)
            b.connect("clicked", lambda _b, cb=callback: cb())
            self.type_buttons[label] = b
            row.pack_start(b, False, False, 0)
        page.pack_start(row, False, False, 0)
        self._fill_types()
        return page

    @staticmethod
    def _app_name(desktop_id):
        app = Gio.DesktopAppInfo.new(desktop_id)
        return app.get_display_name() if app else None

    def _fill_types(self, select=None):
        self.types.clear()
        table = self.settings.get("open_with.by_extension")
        for key in sorted(table):
            it = self.types.append([key, describe_handler(self.settings, table[key], self._app_name)])
            if key == select:
                self.types_view.get_selection().select_iter(it)
        self._types_selection(self.types_view.get_selection())

    def _types_selection(self, sel):
        have = sel.get_selected()[1] is not None
        self.type_buttons["Change..."].set_sensitive(have)
        self.type_buttons["Remove"].set_sensitive(have)

    def _selected_type(self):
        model, it = self.types_view.get_selection().get_selected()
        return model[it][0] if it else None

    def _pick_program(self, ext):
        picked = ask_open_with(self, "x" + ext, ext == ".py")
        if picked is None:
            return
        handler, custom, _remember = picked                             # on this page the choice is always saved
        remember_handler(self.settings, "x" + ext, handler, custom)
        self.settings.save()
        self._fill_types(select=ext)

    def _add_type(self):
        text = prompt(self, "Add a file type", "File extension (for example .txt):")
        if text is None:
            return
        ext = normalize_extension(text)
        if ext is None:
            error(self, "File type", "%r is not a file extension." % text)
            return
        self._pick_program(ext)

    def _change_type(self):
        ext = self._selected_type()
        if ext:
            self._pick_program(ext)

    def _remove_type(self):
        ext = self._selected_type()
        if ext and forget_handler(self.settings, ext):
            self.settings.save()
            self._fill_types()

    def _show_problems(self):
        probs = self.settings.problems
        self.problems.set_text("Problems found:\n" + "\n".join("- " + p for p in probs) if probs
                               else "No problems found.")

    def _toggled(self, cb, key):
        self.settings.set(key, cb.get_active())
        self.settings.save()
        self._show_problems()

    def _setting_changed(self, key, value):
        cb = self.checks.get(key)
        if cb is not None and cb.get_active() != bool(value):
            cb.set_active(bool(value))

    def _reset(self):
        if not ask_yes_no(self, "Reset all settings", "Put every setting back to its default?"):
            return
        self.settings.reset_all()
        self.settings.save()
        self._show_problems()
        info(self, "Settings reset", "Done. The window size and position change the next time you start.")
