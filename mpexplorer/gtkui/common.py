"""GTK 3 helpers shared by the windows (GTK 3.24 via PyGObject; nothing else needs installing)."""
import os
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402,F401  (re-exported: import GTK only through here)

from .. import filechoice  # noqa: E402

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


def find_app(app_id, file_name=None):
    """The installed application with this id, or None. Linux ids are desktop files (DesktopAppInfo). Windows has no
    DesktopAppInfo and its ids are exe paths: they are looked up where the Open with... list found them, among the apps
    for this file's type (a Microsoft Store Python is listed there but not in get_all()), then among all apps."""
    desktop = getattr(Gio, "DesktopAppInfo", None)
    if desktop is not None:
        try:
            return desktop.new(app_id)
        except TypeError:
            return None
    apps = []
    if file_name:
        ctype, _uncertain = Gio.content_type_guess(file_name, None)
        apps += Gio.AppInfo.get_recommended_for_type(ctype) + Gio.AppInfo.get_all_for_type(ctype)
    apps += Gio.AppInfo.get_all()
    wanted = app_id.lower()                 # Windows paths: the case can differ between lists
    return next((a for a in apps if (a.get_id() or "").lower() == wanted), None)


def ask_yes_no(parent, title, text):
    return _message(parent, Gtk.MessageType.QUESTION, title, text, Gtk.ButtonsType.YES_NO) == Gtk.ResponseType.YES


def prompt(parent, title, label, initial="", allow_empty=False):
    """One-line text prompt; returns the text or None (cancelled). An empty answer is None unless allow_empty."""
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
    return text if res == Gtk.ResponseType.OK and (text or allow_empty) else None


def button(label, callback, tooltip=None):
    b = Gtk.Button(label=label)
    b.connect("clicked", lambda _b: callback())
    if tooltip:
        b.set_tooltip_text(tooltip)
    return b


def _icon_name(icon):
    """The first icon of `icon` (a name or a tuple of alternatives) that the current theme has, else None. Each name's
    one-colour '-symbolic' form comes first, so the buttons match: the Adwaita bundled on Windows has folder-new and
    the rest only in that form (they showed as words), and its coloured bin stood out (owner, 2026-11-07)."""
    theme = Gtk.IconTheme.get_default()
    for name in (icon,) if isinstance(icon, str) else icon:
        for candidate in (name + "-symbolic", name):
            if theme.has_icon(candidate):
                return candidate
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


ACTIONS = {"open": Gtk.FileChooserAction.OPEN, "save": Gtk.FileChooserAction.SAVE,
           "folder": Gtk.FileChooserAction.SELECT_FOLDER}


def choose_file(parent, title, action="open", filters=(), start=None, save_name=None, multiple=False):
    """Every file chooser goes through here (docs/FILE_CHOOSER_PLAN.md). Gtk.FileChooserNative shows the system's own
    dialog: Explorer on Windows, the desktop's (portal) dialog on Linux, GTK's where there is no portal.
    filters: [(name, [patterns])]; "All files" is added after them. Returns a path, a list for multiple, or None.
    Tests replace this function, so no real dialog opens under xvfb or on the Windows runner."""
    accept = {"open": "Open", "save": "Save", "folder": "Choose"}[action]
    dlg = Gtk.FileChooserNative.new(title, parent, ACTIONS[action], accept, "Cancel")
    if start:
        dlg.set_current_folder(start)
    if action == "save":
        dlg.set_do_overwrite_confirmation(True)
        if save_name:
            dlg.set_current_name(save_name)
    if multiple:
        dlg.set_select_multiple(True)
    if filters and action != "folder":
        for name, patterns in list(filters) + [("All files", ["*"])]:
            f = Gtk.FileFilter()
            f.set_name(name)
            for p in patterns:
                f.add_pattern(p)
            dlg.add_filter(f)
    try:
        if dlg.run() != Gtk.ResponseType.ACCEPT:
            return None
        return list(dlg.get_filenames()) if multiple else dlg.get_filename()
    finally:
        dlg.destroy()


def choose_local(parent, settings, kind, title, action="open", filters=(), save_name=None, multiple=False):
    """choose_file starting where the last choice of this kind was made, remembering where this one was."""
    start = filechoice.start_folder(settings, kind) if settings is not None else None
    chosen = choose_file(parent, title, action, filters, start, save_name, multiple)
    if chosen and settings is not None:
        filechoice.remember(settings, kind, chosen)
    return chosen


class PathChooser(Gtk.Box):
    """A path box with a Browse... button: typing or pasting a path works too. on_change() after either."""

    def __init__(self, parent_of, settings, kind, title, filters=(), placeholder="", on_change=None):
        super().__init__(spacing=6)
        self._parent_of, self.settings, self.kind, self.title, self.filters = parent_of, settings, kind, title, filters
        self.entry = Gtk.Entry(hexpand=True, placeholder_text=placeholder)
        self.button = Gtk.Button(label="Browse...")
        self.button.connect("clicked", lambda _b: self.browse())
        self.pack_start(self.entry, True, True, 0)
        self.pack_start(self.button, False, False, 0)
        if on_change:
            self.entry.connect("changed", lambda _e: on_change())

    def browse(self):
        path = choose_local(self._parent_of(), self.settings, self.kind, self.title, filters=self.filters)
        if path:
            self.entry.set_text(path)

    def get_filename(self):
        """The path typed or chosen (~ expanded), or None when the box is empty."""
        text = self.entry.get_text().strip().strip('"')
        return os.path.expanduser(text) if text else None

    def set_filename(self, path):
        self.entry.set_text(path or "")


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
