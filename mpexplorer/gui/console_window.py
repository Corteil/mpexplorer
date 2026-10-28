"""REPL console window: type Python, run it on the board, see the output as it arrives."""
import queue
import tkinter as tk
from tkinter import messagebox, ttk

from .terminal_widget import TerminalWidget
from .widgets import AppWindow


class ConsoleWindow(AppWindow):
    def __init__(self, app, session):
        super().__init__(app)
        self.session = session
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
        self.activity = tk.BooleanVar(value=False)
        self.activity_btn = ttk.Checkbutton(bar, text="Show activity", variable=self.activity,
                                            command=self._toggle_activity)
        self.activity_btn.pack(side="left", padx=10)
        mode = ttk.Frame(self)
        mode.pack(fill="x", padx=6)
        self.mode = tk.StringVar(value="emulated")
        ttk.Label(mode, text="Mode:").pack(side="left")
        ttk.Radiobutton(mode, text="Emulated (raw REPL)", variable=self.mode, value="emulated",
                        command=self._mode_changed).pack(side="left", padx=4)
        ttk.Radiobutton(mode, text="Terminal (real REPL)", variable=self.mode, value="terminal",
                        command=self._mode_changed).pack(side="left")
        self.term = None

        self.emu = ttk.Frame(self)
        self.emu.pack(fill="both", expand=True)
        body = ttk.Frame(self.emu)
        body.pack(fill="both", expand=True, padx=4)
        # small requested height: the default (24 lines) squeezed the input box out of view
        self.out = tk.Text(body, state="disabled", wrap="word", font="TkFixedFont", height=8)
        sb = ttk.Scrollbar(body, command=self.out.yview)
        self.out.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.out.pack(fill="both", expand=True)
        self.out.tag_configure("err", foreground="#c0392b")
        self.out.tag_configure("echo", foreground="#2060c0")
        self.out.tag_configure("trace", foreground="#808080")

        self.prompt = ttk.Label(self.emu)
        self.prompt.pack(anchor="w", padx=6)
        self.inp = tk.Text(self.emu, height=4, font="TkFixedFont", undo=True)
        self.inp.pack(fill="x", padx=4, pady=(0, 4))
        self._set_prompt()
        self.inp.bind("<Return>", self._enter)
        self.inp.bind("<Shift-Return>", lambda _e: None)    # default: insert a newline
        self.inp.bind("<Up>", lambda e: self._history(-1))
        self.inp.bind("<Down>", lambda e: self._history(1))
        self.inp.bind("<Control-d>", self._ctrl_d)
        self.bind("<Escape>", lambda _e: self.interrupt() if self.term is None else None)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._drain()

    def _take_focus(self):
        """A new Toplevel starts with focus on the window itself, not the input box, so typing goes nowhere."""
        self.lift()
        self.inp.focus_force()

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
                text, tag = self._q.get_nowait()
                self.write(text, tag)
        except queue.Empty:
            pass
        self.after(50, self._drain)

    def _emit(self, text, is_err):
        self._q.put((text, "err" if is_err else None))

    def _set_prompt(self):
        self.prompt.config(text=(
            "Program input: Enter sends this line to the running program (input())" if self.busy else
            "Python: Enter = run, Shift+Enter = new line, Up/Down = history, Ctrl+D = soft reset, Esc = interrupt"))

    # -- emulated <-> terminal --------------------------------------------
    def _mode_changed(self):
        if self.mode.get() == "terminal":
            self._enter_terminal()
        else:
            self._leave_terminal()

    def _enter_terminal(self):
        if self.busy or self.app.runner.busy:
            messagebox.showinfo("Busy", "Wait for the running command or file operation to finish first.", parent=self)
            self.mode.set("emulated")
            return
        self.emu.pack_forget()
        self.term = TerminalWidget(self, self.session.repl, on_error=lambda _e: None)
        self.term.pack(fill="both", expand=True, padx=4, pady=4)
        try:
            self.term.start()
        except Exception as e:
            self.term.destroy()
            self.term = None
            self.emu.pack(fill="both", expand=True)
            self.mode.set("emulated")
            messagebox.showerror("Terminal", str(e), parent=self)
            return
        self.activity_btn.state(["disabled"])
        self.run_btn.state(["disabled"])
        self.stop_btn.state(["disabled"])
        self.app.set_device_paused(True)
        self.title("REPL console - terminal (real REPL)")

    def _leave_terminal(self):
        if self.term is None:
            return
        self.term.stop()
        self.term.destroy()
        self.term = None
        self.emu.pack(fill="both", expand=True)
        for w in (self.activity_btn, self.run_btn, self.stop_btn):
            w.state(["!disabled"])
        self.app.set_device_paused(False)
        self.title("REPL console")
        self.inp.focus_set()

    # -- activity trace ----------------------------------------------------
    def _toggle_activity(self):
        self.session.repl.trace = self._on_trace if self.activity.get() else None
        if self.activity.get():
            self.write("[activity] showing everything sent to and received from the board\n", "trace")

    def _on_trace(self, kind, text, limit=200):
        """Called on the worker thread for every transfer with the board, file operations included."""
        text = text.replace("\r", "").replace("\n", " | ")
        if len(text) > limit:
            text = text[:limit] + "... (+%d chars)" % (len(text) - limit)
        self._q.put(("  [%-5s] %s\n" % (kind, text), "trace"))

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
        if not line.strip():
            return
        self.inp.delete("1.0", "end")
        self._hist = None
        if self.busy:                   # a program is running: this line answers its input()
            try:                        # no local echo: the board echoes what input() reads (seen on real MicroPython)
                self.session.send_input(line)
            except Exception as e:
                self.write("Could not send input: %s\n" % e, "err")
            return
        first, *rest = line.split("\n")
        self.write(">>> " + first + "\n" + "".join("... " + r + "\n" for r in rest), "echo")
        self._start(lambda: self.session.run(line, self._emit))

    def _ctrl_d(self, _event):
        if self.inp.get("1.0", "end-1c").strip() or self.busy:
            return None                 # only an empty prompt means "reset", as at the real REPL
        self._start(lambda: self.session.soft_reset(self._emit))
        return "break"

    def _start(self, action):
        self.busy = True
        self._set_prompt()
        self.run_btn.config(text="Send input (Enter)")

        def done(_res, exc):
            self.busy = False
            if self.winfo_exists():
                self._set_prompt()
                self.run_btn.config(text="Run (Enter)")
                if exc:
                    self.write("%s\n" % exc, "err")
        self.app.runner.submit(lambda _prog: action(), done, "Console", quiet=True)

    def interrupt(self):
        if self.busy:
            try:
                self.session.interrupt()
            except Exception as e:
                self.write("Interrupt failed: %s\n" % e, "err")

    def close(self):
        self._leave_terminal()
        self.session.repl.trace = None
        self.interrupt()
        self.app.console = None
        self.destroy()
