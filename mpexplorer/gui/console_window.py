"""REPL console window: type Python, run it on the board, see the output as it arrives."""
import queue
import tkinter as tk
from tkinter import ttk


class ConsoleWindow(tk.Toplevel):
    def __init__(self, app, session):
        super().__init__(app)
        self.app, self.session = app, session
        self.title("REPL console")
        self.geometry("780x500")
        self.busy = False
        self._q = queue.Queue()
        self._hist = None               # index into session.history while browsing with Up/Down

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=4, pady=3)
        self.run_btn = ttk.Button(bar, text="Run (Enter)", command=self.run)
        self.run_btn.pack(side="left")
        self.stop_btn = ttk.Button(bar, text="Interrupt (Esc)", command=self.interrupt)
        self.stop_btn.pack(side="left", padx=4)
        ttk.Button(bar, text="Clear", command=self.clear).pack(side="left")
        ttk.Label(bar, text="Shift+Enter = new line.  State persists between runs.  input() is not supported."
                  ).pack(side="left", padx=10)

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=4)
        self.out = tk.Text(body, state="disabled", wrap="word", font="TkFixedFont")
        sb = ttk.Scrollbar(body, command=self.out.yview)
        self.out.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.out.pack(fill="both", expand=True)
        self.out.tag_configure("err", foreground="#c0392b")
        self.out.tag_configure("echo", foreground="#2060c0")

        self.inp = tk.Text(self, height=4, font="TkFixedFont", undo=True)
        self.inp.pack(fill="x", padx=4, pady=4)
        self.inp.bind("<Return>", self._enter)
        self.inp.bind("<Shift-Return>", lambda _e: None)    # default: insert a newline
        self.inp.bind("<Up>", lambda e: self._history(-1))
        self.inp.bind("<Down>", lambda e: self._history(1))
        self.bind("<Escape>", lambda _e: self.interrupt())
        self.inp.focus_set()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._drain()

    # -- output ------------------------------------------------------------
    def write(self, text, tag=None):
        self.out.config(state="normal")
        self.out.insert("end", text, tag)
        self.out.config(state="disabled")
        self.out.see("end")

    def clear(self):
        self.out.config(state="normal")
        self.out.delete("1.0", "end")
        self.out.config(state="disabled")

    def _drain(self):
        try:
            while True:
                text, is_err = self._q.get_nowait()
                self.write(text, "err" if is_err else None)
        except queue.Empty:
            pass
        self.after(50, self._drain)

    # -- input -------------------------------------------------------------
    def _enter(self, _event):
        self.run()
        return "break"

    def _history(self, step):
        if "\n" in self.inp.get("1.0", "end-1c"):
            return None                 # in a multi-line block Up/Down move the cursor
        hist = self.session.history
        if not hist:
            return "break"
        if self._hist is None:
            self._hist = len(hist)
        self._hist = max(0, min(len(hist), self._hist + step))
        self.inp.delete("1.0", "end")
        if self._hist < len(hist):
            self.inp.insert("1.0", hist[self._hist])
        return "break"

    def run(self):
        line = self.inp.get("1.0", "end-1c")
        if self.busy or not line.strip():
            return
        self.inp.delete("1.0", "end")
        self._hist = None
        first, *rest = line.split("\n")
        self.write(">>> " + first + "\n" + "".join("... " + r + "\n" for r in rest), "echo")
        self.busy = True
        self.run_btn.state(["disabled"])

        def work(_prog):
            return self.session.run(line, lambda text, is_err: self._q.put((text, is_err)))

        def done(_ok, exc):
            self.busy = False
            if self.winfo_exists():
                self.run_btn.state(["!disabled"])
                if exc:
                    self.write("%s\n" % exc, "err")
        self.app.runner.submit(work, done, "Console", quiet=True)

    def interrupt(self):
        if self.busy:
            try:
                self.session.interrupt()
            except Exception as e:
                self.write("Interrupt failed: %s\n" % e, "err")

    def close(self):
        self.interrupt()
        self.app.console = None
        self.destroy()
