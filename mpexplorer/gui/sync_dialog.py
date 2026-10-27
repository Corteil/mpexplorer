"""Shows what a folder sync would do and lets the user apply it."""
import tkinter as tk
from tkinter import ttk

from ..backends import human

LABELS = {"mkdir": "Create folder", "copy": "Copy", "delete": "Delete"}


class SyncDialog(tk.Toplevel):
    def __init__(self, app, plan):
        super().__init__(app)
        self.app, self.plan = app, plan
        self.title("Sync folders")
        self.geometry("680x460")
        self.transient(app)
        ttk.Label(self, text="%s  >>  %s" % (plan.src, plan.dst)).pack(anchor="w", padx=8, pady=(8, 2))
        self.summary = ttk.Label(self)
        self.summary.pack(anchor="w", padx=8)
        for note in plan.notes:
            ttk.Label(self, text=note, foreground="#a06000").pack(anchor="w", padx=8)
        self.mirror = tk.BooleanVar(value=False)
        n_del = len(plan.of("delete"))
        chk = ttk.Checkbutton(self, text="Also delete %d item(s) that exist only at the destination (mirror)" % n_del,
                              variable=self.mirror, command=self._fill)
        chk.pack(anchor="w", padx=8, pady=4)
        if not n_del:
            chk.state(["disabled"])

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=8)
        self.tree = ttk.Treeview(body, columns=("action", "size", "why"), show="headings")
        for col, text, w in (("action", "Action", 110), ("size", "Size", 80), ("why", "Why", 90)):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=w, anchor="w")
        self.tree.heading("action", text="Action / path")
        self.tree.column("action", width=380)
        sb = ttk.Scrollbar(body, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)

        row = ttk.Frame(self)
        row.pack(pady=8)
        self.apply_btn = ttk.Button(row, text="Apply", command=self._apply)
        self.apply_btn.pack(side="left", padx=4)
        ttk.Button(row, text="Cancel", command=self.destroy).pack(side="left")
        self._fill()
        self.grab_set()

    def _shown(self):
        return [a for a in self.plan.actions if a.kind != "delete" or self.mirror.get()]

    def _fill(self):
        self.tree.delete(*self.tree.get_children())
        shown = self._shown()
        for a in shown:
            self.tree.insert("", "end", values=("%s  %s" % (LABELS[a.kind], a.rel or "(folder)"),
                                                "" if a.is_dir else human(a.size), a.reason))
        copies = [a for a in shown if a.kind == "copy"]
        self.summary.config(text="%d to copy (%s), %d folder(s) to create, %d to delete, %d already identical" % (
            len(copies), human(sum(a.size for a in copies)), sum(a.kind == "mkdir" for a in shown),
            sum(a.kind == "delete" for a in shown), self.plan.unchanged))
        self.apply_btn.state(["!disabled" if shown else "disabled"])

    def _apply(self):
        mirror = self.mirror.get()
        self.destroy()
        self.app.run_sync(self.plan, mirror)
