"""Minimal text editor that saves straight back to the file's backend."""
from ..backends import human
from .common import Gdk, Gtk, icon_button, monospace_view, scrolled


class Editor(Gtk.Window):
    def __init__(self, app, backend, path, text):
        super().__init__(title="%s: %s" % (backend.label, path))
        self.app, self.backend, self.path = app, backend, path
        self.set_default_size(720, 520)
        self.set_icon(app.get_icon())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(box)
        bar = Gtk.Box(spacing=8, margin=2)
        bar.pack_start(icon_button("document-save", "Save (Ctrl+S)", self.save, "Save (Ctrl+S)"), False, False, 0)
        self.status = Gtk.Label(label="", xalign=0)
        bar.pack_start(self.status, True, True, 0)
        box.pack_start(bar, False, False, 0)

        # Unsaved-changes prompt: an info bar inside the window, not a modal dialog
        self.confirm = Gtk.InfoBar(message_type=Gtk.MessageType.QUESTION, no_show_all=True)
        self.confirm.get_content_area().add(Gtk.Label(label="This file has unsaved changes."))
        self.confirm.add_button("Save and close", 1)
        self.confirm.add_button("Discard changes", 2)
        self.confirm.add_button("Keep editing", 3)
        self.confirm.connect("response", self._confirmed)
        box.pack_start(self.confirm, False, False, 0)

        self.view = monospace_view(editable=True)
        self.buf = self.view.get_buffer()
        self.buf.set_text(text)
        self.buf.set_modified(False)
        self.buf.connect("modified-changed", lambda _b: self.status.set_text("") if self.buf.get_modified() else None)
        box.pack_start(scrolled(self.view), True, True, 0)
        self.connect("key-press-event", self._key)
        self.connect("delete-event", self._delete)
        app.windows.append(self)
        self.connect("destroy", lambda _w: app.windows.remove(self) if self in app.windows else None)
        self.show_all()
        self.view.grab_focus()

    def _key(self, _w, ev):
        if ev.state & Gdk.ModifierType.CONTROL_MASK and Gdk.keyval_name(ev.keyval) in ("s", "S"):
            self.save()
            return True
        if Gdk.keyval_name(ev.keyval) == "Escape" and self.confirm.get_visible():
            self.confirm.hide()
            return True
        return False

    def save(self, close=False):
        start, end = self.buf.get_bounds()
        data = self.buf.get_text(start, end, True).encode("utf-8")
        self.status.set_text("Saving...")

        def done(_res, exc):
            if exc:
                self.status.set_text("Save failed: %s" % exc)
            else:
                self.buf.set_modified(False)
                self.status.set_text("Saved %s" % human(len(data)))
                if close:
                    self.destroy()
        self.app.write_file(self.backend, self.path, data, done)

    def _delete(self, _w, _ev):
        if self.buf.get_modified():
            self.confirm.show()
            self.confirm.show_all()
            return True                 # stay open until the bar is answered
        return False

    def _confirmed(self, _bar, res):
        if res == 1:
            self.save(close=True)
        elif res == 2:
            self.buf.set_modified(False)
            self.destroy()
        self.confirm.hide()
