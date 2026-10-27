"""Tkinter widgets: file pane, conflict dialog, text editor."""
import time
import tkinter as tk
from tkinter import messagebox, ttk

from ..backends import human


def fmt_time(t):
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else ""
    except (OverflowError, OSError, ValueError):
        return ""


def _inside(widget, ancestor):
    """True if widget is ancestor or one of its descendants (Tk paths are no good: '.!pane2' starts with '.!pane')."""
    while widget is not None:
        if widget is ancestor:
            return True
        widget = widget.master
    return False


class Pane(ttk.LabelFrame):
    """One directory listing bound to a backend (host or device)."""

    def __init__(self, master, app, title):
        super().__init__(master, text=title)
        self.app = app
        self.title = title
        self.backend = None
        self.cwd = ""
        self._good = ""
        self._entries = []
        self._token = 0
        self._sort = ("name", False)
        self._drag = None
        self.paused = False             # True while a terminal owns the device
        self.path_var = tk.StringVar()
        self.filter_var = tk.StringVar()

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=2, pady=2)
        ttk.Button(bar, text="Up", width=4, command=self.up).pack(side="left")
        ttk.Button(bar, text="Refresh", width=8, command=self.refresh).pack(side="left", padx=2)
        entry = ttk.Entry(bar, textvariable=self.path_var)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda _e: self.go(self.path_var.get()))
        ttk.Label(bar, text="Filter").pack(side="left", padx=(6, 2))
        ttk.Entry(bar, textvariable=self.filter_var, width=12).pack(side="left")
        self.filter_var.trace_add("write", lambda *_: self._render())

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(body, columns=("size", "modified"), selectmode="extended")
        self.tree.heading("#0", text="Name", command=lambda: self.sort_by("name"))
        self.tree.heading("size", text="Size", command=lambda: self.sort_by("size"))
        self.tree.heading("modified", text="Modified", command=lambda: self.sort_by("modified"))
        self.tree.column("#0", width=260)
        self.tree.column("size", width=80, anchor="e")
        self.tree.column("modified", width=130)
        sb = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)

        t = self.tree
        t.bind("<FocusIn>", lambda _e: app.set_active(self))
        t.bind("<Double-1>", self._open)
        t.bind("<Return>", self._open)
        t.bind("<BackSpace>", lambda _e: self.up())
        t.bind("<Button-3>", self._context)
        t.bind("<ButtonPress-1>", self._press)
        t.bind("<ButtonRelease-1>", self._release)
        t.bind("<Control-c>", lambda _e: app.copy("copy") or "break")
        t.bind("<Control-x>", lambda _e: app.copy("cut") or "break")
        t.bind("<Control-v>", lambda _e: app.paste() or "break")
        t.bind("<Control-a>", lambda _e: self.select_all() or "break")
        t.bind("<Delete>", lambda _e: app.delete())
        t.bind("<F2>", lambda _e: app.rename())
        t.bind("<F5>", lambda _e: self.refresh())
        t.bind("<F7>", lambda _e: app.new_folder())

    # -- state -------------------------------------------------------------
    def set_location(self, backend, path):
        self.backend, self.cwd, self._good = backend, path, ""
        self.config(text=self.title if backend else self.title + " (not connected)")
        self.refresh()

    def set_paused(self, paused):
        self.paused = paused
        if self.backend:
            self.config(text=self.title + (" (terminal active - paused)" if paused else ""))
            if not paused:
                self.refresh()

    def clear(self):
        self.paused = False
        self.backend = None
        self._entries = []
        self.config(text=self.title + " (not connected)")
        self.path_var.set("")
        self._render()

    def select_all(self):
        self.tree.selection_set(self.tree.get_children())

    def selected(self):
        """[(name, full_path)] for the selected rows."""
        if not self.backend or self.paused:
            return []
        return [(n, self.backend.join(self.cwd, n)) for n in self.tree.selection()]

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
        self.path_var.set(self.cwd)
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
            messagebox.showerror("Cannot open folder", "%s\n\n%s" % (bad, exc))
            if self._good:
                self.cwd = self._good
                self.path_var.set(self.cwd)
            return
        self._good = self.cwd
        self._entries = entries
        self._render()

    def _open(self, _event=None):
        sel = self.tree.selection()
        if len(sel) != 1 or not self.backend or self.paused:
            return "break"
        e = self.entry(sel[0])
        if e and e.is_dir:
            self.go(self.backend.join(self.cwd, e.name))
        elif e:
            self.app.open_file(self, self.backend.join(self.cwd, e.name), e.size)
        return "break"

    # -- display -----------------------------------------------------------
    def sort_by(self, key):
        self._sort = (key, not self._sort[1] if self._sort[0] == key else False)
        self._render()

    def _render(self):
        keep = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        flt = self.filter_var.get().lower()
        show_hidden = self.app.show_hidden.get()
        rows = [e for e in self._entries
                if (show_hidden or not e.name.startswith(".")) and flt in e.name.lower()]
        key = {"name": lambda e: e.name.lower(), "size": lambda e: e.size,
               "modified": lambda e: e.mtime}[self._sort[0]]
        rows.sort(key=key, reverse=self._sort[1])
        rows.sort(key=lambda e: not e.is_dir)       # folders always first
        for e in rows:
            self.tree.insert("", "end", iid=e.name, text=("[D] " if e.is_dir else "     ") + e.name,
                             values=("" if e.is_dir else human(e.size), fmt_time(e.mtime)))
        self.tree.selection_set([n for n in keep if self.tree.exists(n)])

    # -- context menu and drag between panes -------------------------------
    def _context(self, event):
        row = self.tree.identify_row(event.y)
        if row and row not in self.tree.selection():
            self.tree.selection_set(row)
        self.tree.focus_set()
        self.app.context_menu().tk_popup(event.x_root, event.y_root)

    def _press(self, event):
        row = self.tree.identify_row(event.y)
        if row and row in self.tree.selection() and not event.state & 0x0005:   # no Shift/Ctrl
            self._drag = (row, self.tree.selection())
            return "break"                  # keep the multi-selection while dragging
        self._drag = None

    def _release(self, event):
        if not self._drag:
            return
        row, _sel = self._drag
        self._drag = None
        target = self.winfo_toplevel().winfo_containing(event.x_root, event.y_root)
        other = self.app.other_pane(self)
        if _inside(target, other):
            self.app.transfer(self, other)
        elif self.tree.identify_row(event.y) == row:
            self.tree.selection_set(row)    # a plain click on a selected row selects just it


class ConflictDialog(tk.Toplevel):
    def __init__(self, master, name):
        super().__init__(master)
        self.title("File exists")
        self.transient(master)
        self.result = ("cancel", False)
        ttk.Label(self, text="'%s' already exists at the destination." % name).pack(padx=14, pady=(12, 6))
        self.everywhere = tk.BooleanVar()
        ttk.Checkbutton(self, text="Apply to all", variable=self.everywhere).pack()
        row = ttk.Frame(self)
        row.pack(padx=10, pady=10)
        for text, action in (("Overwrite", "overwrite"), ("Skip", "skip"),
                             ("Keep both", "rename"), ("Cancel", "cancel")):
            ttk.Button(row, text=text, command=lambda a=action: self._pick(a)).pack(side="left", padx=3)
        self.protocol("WM_DELETE_WINDOW", lambda: self._pick("cancel"))
        self.grab_set()

    def _pick(self, action):
        self.result = (action, self.everywhere.get())
        self.destroy()


class Editor(tk.Toplevel):
    """Minimal text editor that saves straight back to the file's backend."""

    def __init__(self, app, backend, path, text):
        super().__init__(app)
        self.app, self.backend, self.path = app, backend, path
        self.title("%s: %s" % (backend.label, path))
        self.transient(app)         # tell the WM the editor belongs to the main window (else clicks fall through to it)
        self.geometry("720x520")
        bar = ttk.Frame(self)
        bar.pack(fill="x")
        ttk.Button(bar, text="Save (Ctrl+S)", command=self.save).pack(side="left", padx=2, pady=2)
        self.status = ttk.Label(bar, text="")
        self.status.pack(side="left", padx=8)
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        self.text = tk.Text(body, undo=True, wrap="none", font="TkFixedFont")
        sb = ttk.Scrollbar(body, command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.text.pack(fill="both", expand=True)
        self.text.insert("1.0", text)
        self.text.edit_reset()
        self.text.edit_modified(False)
        self.bind("<Control-s>", lambda _e: self.save() or "break")
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(50, self._take_focus)

    def _take_focus(self):
        """A new Toplevel starts with focus on the window itself, not the text box, so typing goes nowhere."""
        self.lift()
        self.text.focus_force()

    def save(self):
        data = self.text.get("1.0", "end-1c").encode("utf-8")
        self.status.config(text="Saving...")

        def done(_res, exc):
            if exc:
                messagebox.showerror("Save failed", str(exc), parent=self)
                self.status.config(text="Save failed")
            else:
                self.text.edit_modified(False)
                self.status.config(text="Saved %s" % human(len(data)))
        self.app.write_file(self.backend, self.path, data, done)

    def _close(self):
        if self.text.edit_modified() and not messagebox.askyesno(
                "Discard changes?", "This file has unsaved changes. Close anyway?", parent=self):
            return
        self.destroy()
