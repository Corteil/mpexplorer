"""REPL console window: type Python, run it on the board, see the output as it arrives.

Emulated mode (raw REPL) only. The real-REPL terminal mode of the Tk version needs a terminal widget; GTK 3 has none
installed here (no VTE), so that radio button is disabled.
"""
import queue

from .common import Gdk, GLib, Gtk, button, monospace_view, scrolled


class ConsoleWindow(Gtk.Window):
    def __init__(self, app, session):
        super().__init__(title="REPL console")
        self.app, self.session = app, session
        self.set_default_size(780, 500)
        self.set_icon(app.get_icon())
        self.busy = False
        self._q = queue.Queue()
        self._hist = None               # index into session.history while browsing with Up/Down

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, margin=4)
        self.add(box)
        bar = Gtk.Box(spacing=4)
        self.run_btn = button("Run (Enter)", self.run)
        self.stop_btn = button("Interrupt (Esc)", self.interrupt)
        bar.pack_start(self.run_btn, False, False, 0)
        bar.pack_start(self.stop_btn, False, False, 0)
        bar.pack_start(button("Clear", self.clear), False, False, 0)
        self.activity = Gtk.CheckButton(label="Show activity")
        self.activity.connect("toggled", lambda _c: self._toggle_activity())
        bar.pack_start(self.activity, False, False, 10)
        box.pack_start(bar, False, False, 0)

        mode = Gtk.Box(spacing=8)
        mode.pack_start(Gtk.Label(label="Mode:"), False, False, 0)
        emu = Gtk.RadioButton.new_with_label(None, "Emulated (raw REPL)")
        term = Gtk.RadioButton.new_with_label_from_widget(emu, "Terminal (real REPL)")
        term.set_sensitive(False)
        term.set_tooltip_text("Not available in the GTK interface yet (needs a terminal widget); use --tk")
        mode.pack_start(emu, False, False, 0)
        mode.pack_start(term, False, False, 0)
        box.pack_start(mode, False, False, 0)

        self.out = monospace_view(wrap=Gtk.WrapMode.WORD_CHAR)
        tags = self.out.get_buffer()
        tags.create_tag("err", foreground="#c0392b")
        tags.create_tag("echo", foreground="#2060c0")
        tags.create_tag("trace", foreground="#808080")
        box.pack_start(scrolled(self.out), True, True, 0)

        self.prompt = Gtk.Label(xalign=0)
        box.pack_start(self.prompt, False, False, 0)
        self.inp = monospace_view(editable=True)
        self.inp.set_size_request(-1, 80)
        box.pack_start(scrolled(self.inp), False, False, 0)
        self._set_prompt()
        self.inp.connect("key-press-event", self._key)
        self.connect("key-press-event", self._window_key)
        self.connect("delete-event", lambda *_: self.close() or True)
        app.windows.append(self)
        self.show_all()
        self.inp.grab_focus()
        self._drain_id = GLib.timeout_add(50, self._drain)

    # -- output ------------------------------------------------------------
    def write(self, text, tag=None):
        buf = self.out.get_buffer()
        if tag:
            buf.insert_with_tags_by_name(buf.get_end_iter(), text, tag)
        else:
            buf.insert(buf.get_end_iter(), text)
        self.out.scroll_to_mark(buf.create_mark(None, buf.get_end_iter(), False), 0.0, False, 0, 0)

    def clear(self):
        self.out.get_buffer().set_text("")

    def _drain(self):
        try:
            while True:
                text, tag = self._q.get_nowait()
                self.write(text, tag)
        except queue.Empty:
            pass
        return True

    def _emit(self, text, is_err):
        self._q.put((text, "err" if is_err else None))

    def _set_prompt(self):
        self.prompt.set_text(
            "Program input: Enter sends this line to the running program (input())" if self.busy else
            "Python: Enter = run, Shift+Enter = new line, Up/Down = history, Ctrl+D = soft reset, Esc = interrupt")

    # -- activity trace ----------------------------------------------------
    def _toggle_activity(self):
        on = self.activity.get_active()
        self.session.repl.trace = self._on_trace if on else None
        if on:
            self.write("[activity] showing everything sent to and received from the board\n", "trace")

    def _on_trace(self, kind, text, limit=200):
        """Called on the worker thread for every transfer with the board, file operations included."""
        text = text.replace("\r", "").replace("\n", " | ")
        if len(text) > limit:
            text = text[:limit] + "... (+%d chars)" % (len(text) - limit)
        self._q.put(("  [%-5s] %s\n" % (kind, text), "trace"))

    # -- input -------------------------------------------------------------
    def _text(self):
        buf = self.inp.get_buffer()
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)

    def _key(self, _w, ev):
        name = Gdk.keyval_name(ev.keyval)
        shift = ev.state & Gdk.ModifierType.SHIFT_MASK
        ctrl = ev.state & Gdk.ModifierType.CONTROL_MASK
        if name in ("Return", "KP_Enter") and not shift:
            self.run()
            return True
        if name in ("Up", "Down") and "\n" not in self._text():
            self._history(-1 if name == "Up" else 1)
            return True
        if ctrl and name in ("d", "D") and not self._text().strip() and not self.busy:
            self._start(lambda: self.session.soft_reset(self._emit))
            return True
        return False

    def _window_key(self, _w, ev):
        if Gdk.keyval_name(ev.keyval) == "Escape":
            self.interrupt()
            return True
        return False

    def _history(self, step):
        hist = self.session.history
        if not hist:
            return
        if self._hist is None:
            self._hist = len(hist)
        self._hist = max(0, min(len(hist), self._hist + step))
        self.inp.get_buffer().set_text(hist[self._hist] if self._hist < len(hist) else "")

    def run(self):
        line = self._text()
        if not line.strip():
            return
        self.inp.get_buffer().set_text("")
        self._hist = None
        if self.busy:                   # a program is running: this line answers its input()
            try:                        # no local echo: the board echoes what input() reads
                self.session.send_input(line)
            except Exception as e:
                self.write("Could not send input: %s\n" % e, "err")
            return
        first, *rest = line.split("\n")
        self.write(">>> " + first + "\n" + "".join("... " + r + "\n" for r in rest), "echo")
        self._start(lambda: self.session.run(line, self._emit))

    def _start(self, action):
        self.busy = True
        self._set_prompt()
        self.run_btn.set_label("Send input (Enter)")

        def done(_res, exc):
            self.busy = False
            if self.get_window() is not None:
                self._set_prompt()
                self.run_btn.set_label("Run (Enter)")
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
        GLib.source_remove(self._drain_id)
        self.session.repl.trace = None
        self.interrupt()
        self.app.console = None
        if self in self.app.windows:
            self.app.windows.remove(self)
        self.destroy()

