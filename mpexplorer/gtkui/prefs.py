"""Preferences window: changes apply and are saved as soon as they are made."""
from .common import Gtk, ask_yes_no, info

GENERAL = [
    ("general.show_hidden", "Show hidden files (names starting with a dot)"),
    ("general.remember_window", "Remember the window size and position"),
    ("general.remember_folders", "Remember the last folders (host, and one per board)"),
    ("general.remember_port", "Remember the last serial port"),
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
