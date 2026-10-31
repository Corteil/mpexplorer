"""Read-only viewers: a hex dump and an image."""
from ..backends import human
from ..viewers import hexdump
from .common import GdkPixbuf, Gtk, monospace_view, scrolled


class Viewer(Gtk.Window):
    def __init__(self, app, backend, path, data, kind):
        super().__init__(title="%s: %s" % (backend.label, path))
        self.set_default_size(720, 520)
        self.set_icon(app.get_icon())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(box)
        self.info = Gtk.Label(label="%s, %s" % (path, human(len(data))), xalign=0, margin=4)
        box.pack_start(self.info, False, False, 0)
        if kind == "image":
            body = self._image(data)
        else:
            body = monospace_view(editable=False)
            body.get_buffer().set_text(hexdump(data))
        box.pack_start(scrolled(body), True, True, 0)
        app.windows.append(self)
        self.connect("destroy", lambda _w: app.windows.remove(self) if self in app.windows else None)
        self.show_all()

    def _image(self, data):
        loader = GdkPixbuf.PixbufLoader()
        try:
            loader.write(data)
            loader.close()
            pix = loader.get_pixbuf()
        except Exception as e:
            self.info.set_text("%s (not a picture GTK can show: %s)" % (self.info.get_text(), e))
            return Gtk.Label()
        self.info.set_text("%s, %dx%d" % (self.info.get_text(), pix.get_width(), pix.get_height()))
        return Gtk.Image.new_from_pixbuf(pix)
