"""GTK 3 helpers shared by the windows (GTK 3.24 via PyGObject; nothing else needs installing)."""
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402,F401  (re-exported: import GTK only through here)

ICON = Path(__file__).resolve().parent.parent / "icon.png"


def fmt_time(t):
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else ""
    except (OverflowError, OSError, ValueError):
        return ""


def _message(parent, kind, title, text, buttons=Gtk.ButtonsType.OK):
    dlg = Gtk.MessageDialog(transient_for=parent, modal=True, message_type=kind, buttons=buttons, text=title)
    dlg.format_secondary_text(text)
    res = dlg.run()
    dlg.destroy()
    return res


def info(parent, title, text):
    _message(parent, Gtk.MessageType.INFO, title, text)


def error(parent, title, text):
    _message(parent, Gtk.MessageType.ERROR, title, text)


def ask_yes_no(parent, title, text):
    return _message(parent, Gtk.MessageType.QUESTION, title, text, Gtk.ButtonsType.YES_NO) == Gtk.ResponseType.YES


def prompt(parent, title, label, initial=""):
    """One-line text prompt; returns the text or None."""
    dlg = Gtk.Dialog(title=title, transient_for=parent, modal=True)
    dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "OK", Gtk.ResponseType.OK)
    dlg.set_default_response(Gtk.ResponseType.OK)
    box = dlg.get_content_area()
    box.set_spacing(6)
    box.set_border_width(10)
    box.add(Gtk.Label(label=label, xalign=0))
    entry = Gtk.Entry(text=initial, activates_default=True, width_chars=34)
    box.add(entry)
    dlg.show_all()
    entry.select_region(0, -1)
    res = dlg.run()
    text = entry.get_text()
    dlg.destroy()
    return text if res == Gtk.ResponseType.OK and text else None


def button(label, callback, tooltip=None):
    b = Gtk.Button(label=label)
    b.connect("clicked", lambda _b: callback())
    if tooltip:
        b.set_tooltip_text(tooltip)
    return b


def scrolled(child, shadow=Gtk.ShadowType.IN):
    sw = Gtk.ScrolledWindow(shadow_type=shadow)
    sw.add(child)
    return sw


def monospace_view(editable=False, wrap=Gtk.WrapMode.NONE):
    tv = Gtk.TextView(editable=editable, monospace=True, wrap_mode=wrap, cursor_visible=editable)
    tv.set_left_margin(4)
    tv.set_right_margin(4)
    return tv
