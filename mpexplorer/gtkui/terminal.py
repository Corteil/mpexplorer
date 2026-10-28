"""GTK terminal: keystrokes go straight to the board's REPL, its output is drawn by a VTE terminal widget.

VTE (gir1.2-vte-2.91) is optional: without it the console's Terminal mode stays disabled.
"""
import queue

import gi

from ..terminal import TerminalSession, paste_bytes
from .common import Gdk, GLib, Gtk

try:
    gi.require_version("Vte", "2.91")
    from gi.repository import Vte
except (ValueError, ImportError):
    Vte = None

BG, FG = "#14181d", "#d8dee9"


def available():
    return Vte is not None


def _rgba(hex_colour):
    c = Gdk.RGBA()
    c.parse(hex_colour)
    return c


class TerminalWidget(Gtk.Box):
    def __init__(self, repl, on_error=None):
        super().__init__(spacing=0)
        self._rx = queue.Queue()
        self._on_error = on_error
        self._alive = False
        self._timer = None
        self.session = TerminalSession(repl, self._rx.put, self._failed)
        self.term = Vte.Terminal()
        self.term.set_colors(_rgba(FG), _rgba(BG), None)
        self.term.set_scrollback_lines(2000)
        self.term.set_cursor_blink_mode(Vte.CursorBlinkMode.ON)
        self.term.connect("commit", lambda _t, text, _n: self.session.send(text.encode("utf-8")))
        self.term.connect("key-press-event", self._key)
        self.term.connect("button-press-event", self._button)
        sb = Gtk.Scrollbar(orientation=Gtk.Orientation.VERTICAL, adjustment=self.term.get_vadjustment())
        self.pack_start(self.term, True, True, 0)
        self.pack_start(sb, False, False, 0)

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        self.session.start()
        self._alive = True
        self.term.grab_focus()
        self._timer = GLib.timeout_add(20, self._drain)

    def stop(self):
        self._alive = False
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = None
        self.session.stop()

    def _failed(self, exc):
        self._rx.put(("\r\n[connection lost: %s]\r\n" % exc).encode())
        if self._on_error:
            self._on_error(exc)

    # -- input -------------------------------------------------------------
    def _key(self, _w, ev):
        name = Gdk.keyval_name(ev.keyval) or ""
        ctrl = ev.state & Gdk.ModifierType.CONTROL_MASK
        shift = ev.state & Gdk.ModifierType.SHIFT_MASK
        if ctrl and shift and name.lower() == "c":
            self.term.copy_clipboard_format(Vte.Format.TEXT)
            return True
        if (ctrl and shift and name.lower() == "v") or (shift and name == "Insert"):
            self._paste(Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD))
            return True
        return False                                        # VTE turns everything else into bytes (commit)

    def _button(self, _w, ev):
        if ev.type == Gdk.EventType.BUTTON_PRESS and ev.button == 2:
            self._paste(Gtk.Clipboard.get(Gdk.SELECTION_PRIMARY))
            return True
        return False

    def _paste(self, clipboard):
        text = clipboard.wait_for_text()
        if text:
            data = paste_bytes(text)
            if data:
                self.session.send(data)

    # -- output ------------------------------------------------------------
    def _drain(self):
        try:
            while True:
                self.term.feed(self._rx.get_nowait())
        except queue.Empty:
            pass
        return self._alive
