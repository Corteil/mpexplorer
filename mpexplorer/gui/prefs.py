"""Tk Preferences window: changes apply and are saved as soon as they are made."""
import tkinter as tk
from tkinter import messagebox, ttk

GENERAL = [
    ("general.show_hidden", "Show hidden files (names starting with a dot)"),
    ("general.remember_window", "Remember the window size"),
    ("general.remember_folders", "Remember the last folders (host, and one per board)"),
    ("general.remember_port", "Remember the last serial port"),
    ("general.reset_on_disconnect", "Restart the board when disconnecting or quitting (brings a badge's screen back)"),
    ("general.set_board_clock", "Set the board's clock (UTC) to this computer's time when connecting"),
    ("general.device_mtimes", "Show modified times for device files (slow: about 40 ms per file on a Tildagon)"),
]


class PreferencesDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app, self.settings = app, app.settings
        self.title("Preferences")
        self.transient(app)
        self.geometry("520x300")
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=8)

        general = ttk.Frame(nb, padding=12)
        nb.add(general, text="General")
        self.vars = {}
        for key, label in GENERAL:
            var = tk.BooleanVar(value=self.settings.get(key))
            self.vars[key] = var
            ttk.Checkbutton(general, text=label, variable=var,
                            command=lambda k=key: self._toggled(k)).pack(anchor="w", pady=3)

        files = ttk.Frame(nb, padding=12)
        nb.add(files, text="Settings file")
        ttk.Label(files, text="Settings are stored in:").pack(anchor="w")
        ttk.Label(files, text=self.settings.path or "(memory only)", wraplength=460).pack(anchor="w", pady=2)
        self.problems = ttk.Label(files, wraplength=460, justify="left")
        self.problems.pack(anchor="w", pady=6)
        ttk.Button(files, text="Reset all settings...", command=self._reset).pack(anchor="w", pady=6)
        self._show_problems()

        ttk.Button(self, text="Close", command=self.destroy).pack(pady=(0, 8))
        self.settings.subscribe(self._setting_changed)
        self.bind("<Destroy>", self._destroyed)

    def _destroyed(self, ev):
        if ev.widget is self:
            self.settings.unsubscribe(self._setting_changed)

    def _show_problems(self):
        probs = self.settings.problems
        self.problems.config(text="Problems found:\n" + "\n".join("- " + p for p in probs) if probs
                             else "No problems found.")

    def _toggled(self, key):
        self.settings.set(key, bool(self.vars[key].get()))
        self.settings.save()
        self._show_problems()

    def _setting_changed(self, key, value):
        var = self.vars.get(key)
        if var is not None and bool(var.get()) != bool(value):
            var.set(bool(value))

    def _reset(self):
        if not messagebox.askyesno("Reset all settings", "Put every setting back to its default?", parent=self):
            return
        self.settings.reset_all()
        self.settings.save()
        self._show_problems()
        messagebox.showinfo("Settings reset", "Done. The window size changes the next time you start.", parent=self)
