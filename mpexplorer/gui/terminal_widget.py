"""Tk terminal: keystrokes go straight to the board's REPL, its output is drawn from an AnsiScreen."""
import queue
import tkinter as tk
from tkinter import ttk

from ..screen import AnsiScreen
from ..terminal import TerminalSession, key_bytes, paste_bytes

BG, FG = "#14181d", "#d8dee9"


class TerminalWidget(ttk.Frame):
    def __init__(self, master, repl, on_error=None):
        super().__init__(master)
        self.screen = AnsiScreen()
        self._rx = queue.Queue()
        self._on_error = on_error
        self.session = TerminalSession(repl, self._rx.put, self._failed)
        self._alive = False

        self.text = tk.Text(self, font="TkFixedFont", wrap="char", bg=BG, fg=FG, insertwidth=0,
                            state="disabled", cursor="xterm", takefocus=True)
        sb = ttk.Scrollbar(self, command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.text.pack(fill="both", expand=True)
        self.text.tag_configure("cursor", background=FG, foreground=BG)

        t = self.text
        t.bind("<Key>", self._key)
        t.bind("<Button-1>", lambda _e: t.focus_set())
        t.bind("<Control-Shift-C>", self._copy)
        t.bind("<Control-Shift-V>", self._paste)
        t.bind("<Shift-Insert>", self._paste)
        t.bind("<Button-2>", self._paste)

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        self.session.start()
        self._alive = True
        self.text.focus_set()
        self._poll()

    def stop(self):
        self._alive = False
        self.session.stop()

    def _failed(self, exc):
        self._rx.put(("\r\n[connection lost: %s]\r\n" % exc).encode())
        if self._on_error:
            self._on_error(exc)

    # -- input -------------------------------------------------------------
    def _key(self, event):
        ctrl = bool(event.state & 0x4)
        if ctrl and event.state & 0x1 and event.keysym.lower() in ("c", "v"):
            return None                                   # Ctrl+Shift+C / V are copy / paste, bound separately
        data = key_bytes(event.keysym, event.char, ctrl)
        if data:
            self.session.send(data)
        return "break"

    def _copy(self, _event=None):
        try:
            sel = self.text.get("sel.first", "sel.last")
        except tk.TclError:
            return "break"
        self.clipboard_clear()
        self.clipboard_append(sel)
        return "break"

    def _paste(self, _event=None):
        try:
            data = paste_bytes(self.clipboard_get())
        except tk.TclError:
            return "break"
        if data:
            self.session.send(data)
        return "break"

    # -- output ------------------------------------------------------------
    def _poll(self):
        if not self._alive:
            return
        try:
            while True:
                self.screen.feed(self._rx.get_nowait())
        except queue.Empty:
            pass
        if self.screen.dirty:
            self._render()
        self.after(30, self._poll)

    def _render(self):
        sc = self.screen
        sc.dirty = False
        at_bottom = self.text.yview()[1] >= 0.999
        lines = list(sc.lines)
        if sc.col >= len(lines[sc.row]):
            lines[sc.row] = lines[sc.row].ljust(sc.col) + " "     # room to draw the cursor block
        t = self.text
        t.config(state="normal")
        t.delete("1.0", "end")
        t.insert("1.0", "\n".join(lines))
        t.tag_remove("cursor", "1.0", "end")
        t.tag_add("cursor", "%d.%d" % (sc.row + 1, sc.col), "%d.%d" % (sc.row + 1, sc.col + 1))
        t.config(state="disabled")
        if at_bottom:
            t.see("end")
