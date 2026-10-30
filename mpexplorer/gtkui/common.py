"""GTK 3 helpers shared by the windows (GTK 3.24 via PyGObject; nothing else needs installing)."""
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402,F401  (re-exported: import GTK only through here)

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


def _icon_name(icon):
    """The first icon of `icon` (a name or a tuple of alternatives) that the current theme has, else None."""
    theme = Gtk.IconTheme.get_default()
    for name in (icon,) if isinstance(icon, str) else icon:
        if theme.has_icon(name):
            return name
    return None


def set_icon(b, icon, tooltip, label):
    """Show `icon` on button b with a hover hint; a text button (`label`) when the theme lacks the icon."""
    name = _icon_name(icon)
    if name:
        b.set_label("")
        b.set_image(Gtk.Image.new_from_icon_name(name, Gtk.IconSize.LARGE_TOOLBAR))
        b.set_always_show_image(True)
    else:
        b.set_image(None)
        b.set_label(label)
    b.set_tooltip_text(tooltip)


def icon_button(icon, tooltip, callback, label):
    b = Gtk.Button()
    set_icon(b, icon, tooltip, label)
    b.connect("clicked", lambda _b: callback())
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
