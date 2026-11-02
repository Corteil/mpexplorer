"""The sources manager: the custom firmware and library sources, with presets, Check, and trusted sites.
One widget per section, used inside Preferences (Sources page, both) and in a dialog opened by the Manage sources...
buttons (one section). The logic is in ..sources, ..custom_firmware and ..custom_libraries
(docs/CUSTOM_SOURCES_PLAN.md)."""
import threading

from .. import custom_firmware, custom_libraries, sources
from .common import GLib, Gtk, ask_yes_no, info, scrolled


def _firmware_newest(src):
    catalog = custom_firmware.make_catalog(sources.validate_firmware_source(src))
    if src["kind"] != "github":
        return "one file"
    builds, _saved = catalog.releases()
    return "%s (%s)" % (builds[0].version, builds[0].date) if builds else "none"


def _library_newest(src):
    catalog = custom_libraries.make_catalog(custom_libraries.validate_library_source(src))
    libs, _saved = catalog.libraries()
    if src["kind"] == "bundle":
        return "release %s" % catalog.tag
    if src["kind"] == "folder":
        return "%d librar%s" % (len(libs), "y" if len(libs) == 1 else "ies")
    return libs[0].version if libs else "none"


SECTIONS = {
    "firmware": {"title": "Firmware sources", "key": "sources.firmware", "presets": sources.FIRMWARE_PRESETS,
                 "validate": sources.validate_firmware_source, "check": custom_firmware.check_source,
                 "newest": _firmware_newest},
    "libraries": {"title": "Library sources", "key": "sources.libraries", "presets": sources.LIBRARY_PRESETS,
                  "validate": custom_libraries.validate_library_source, "check": custom_libraries.check_source,
                  "newest": _library_newest},
}


def where(src):
    return src.get("repo") or src.get("url") or src.get("path") or ""


class AddSourceDialog(Gtk.Dialog):
    """Name, kind, address and (for a repo) the file name pattern; Add runs the Check before anything is saved."""

    section = "firmware"
    title = "Add a firmware source"
    name_hint = "A name for the list, e.g. Pimoroni MicroPython"

    def __init__(self, parent, settings, check=None):
        super().__init__(title=self.title, transient_for=parent, modal=True)
        self.settings, self.saved, self.checking = settings, None, False
        self.check = check or SECTIONS[self.section]["check"]
        self.set_default_size(520, -1)
        box = self.get_content_area()
        box.set_spacing(8)
        box.set_border_width(12)
        grid = Gtk.Grid(column_spacing=10, row_spacing=6)
        self.name = Gtk.Entry(hexpand=True, placeholder_text=self.name_hint)
        grid.attach(Gtk.Label(label="Name", xalign=1), 0, 0, 1, 1)
        grid.attach(self.name, 1, 0, 1, 1)
        self.build_form(grid)
        box.pack_start(grid, False, False, 0)
        self.result = Gtk.Label(xalign=0, wrap=True, selectable=True)
        box.pack_start(self.result, False, False, 0)
        self.cancel_btn = self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        self.add_btn = self.add_button("Check and add", Gtk.ResponseType.OK)
        self.set_default_response(Gtk.ResponseType.OK)
        self.connect("response", self._response)
        self.show_all()
        self._kind_changed()

    def build_form(self, grid):
        self.is_repo = Gtk.RadioButton(label="A GitHub repository with release files")
        self.is_url = Gtk.RadioButton(label="One firmware file at a web address", group=self.is_repo)
        self.is_repo.connect("toggled", lambda _b: self._kind_changed())
        grid.attach(self.is_repo, 1, 1, 1, 1)
        grid.attach(self.is_url, 1, 2, 1, 1)
        self.address_label = Gtk.Label(xalign=1)
        self.address = Gtk.Entry(hexpand=True)
        grid.attach(self.address_label, 0, 3, 1, 1)
        grid.attach(self.address, 1, 3, 1, 1)
        self.pattern_label = Gtk.Label(label="File names", xalign=1)
        self.pattern = Gtk.Entry(hexpand=True, text=sources.DEFAULT_PATTERN)
        self.pattern.set_tooltip_text("A regular expression with (?P<board>...) and (?P<version>...); Check tries it "
                                      "on the file names")
        grid.attach(self.pattern_label, 0, 4, 1, 1)
        grid.attach(self.pattern, 1, 4, 1, 1)
        self.firmware = Gtk.ComboBoxText()
        self.firmware.append("micropython", "MicroPython")
        self.firmware.append("circuitpython", "CircuitPython")
        self.firmware.set_active(0)
        grid.attach(Gtk.Label(label="Puts on the board", xalign=1), 0, 5, 1, 1)
        grid.attach(self.firmware, 1, 5, 1, 1)

    def _kind_changed(self):
        repo = self.is_repo.get_active()
        self.address_label.set_text("Repository" if repo else "Address")
        self.address.set_placeholder_text("owner/name, e.g. pimoroni/pimoroni-pico" if repo else "https://...")
        self.pattern.set_visible(repo)
        self.pattern_label.set_visible(repo)

    def source(self):
        text = self.address.get_text().strip()
        src = {"name": self.name.get_text(), "kind": "github" if self.is_repo.get_active() else "url",
               "firmware": self.firmware.get_active_id()}
        src["repo" if self.is_repo.get_active() else "url"] = text
        if self.is_repo.get_active():
            src["pattern"] = self.pattern.get_text()
        return src

    def _response(self, _d, res):
        if res != Gtk.ResponseType.OK:
            if not self.checking:
                self.destroy()
            return
        if self.checking:
            return
        self.checking = True
        self.add_btn.set_sensitive(False)
        self.result.set_text("Checking ...")

        def work():
            try:
                got = self.check(self.source(), self.settings)
            except Exception as e:                  # a bug in a check must not leave the dialog stuck
                got = sources.Check(False, "Could not check: %s" % e, None)
            GLib.idle_add(self._checked, got)
        threading.Thread(target=work, daemon=True).start()

    def _checked(self, got):
        self.checking = False
        self.add_btn.set_sensitive(True)
        if not got.ok:
            self.result.set_text("Not added: %s" % got.message)
            return
        host = got.trust_host
        if host and not ask_yes_no(self, "Trust this site?", "%s\n\nThis is not GitHub. If you add it, lists and files "
                                   "will be fetched from %s from now on (installs from it still ask first).\n\n"
                                   "Trust %s?" % (got.message, host, host)):
            self.result.set_text("Not added: the site was not trusted.")
            return
        src = SECTIONS[self.section]["validate"](self.source())
        if got.trust_host:
            sources.trust_host(self.settings, got.trust_host)
        sources.add_source(self.settings, self.section, src)
        self.saved = src
        self.destroy()


class AddLibrarySourceDialog(AddSourceDialog):
    """A library source: a bundle repo (release zips with a file prefix, like Adafruit's) or a `package.json` repo."""

    section = "libraries"
    title = "Add a library source"
    name_hint = "A name for the list, e.g. SparkFun Qwiic"

    def build_form(self, grid):
        self.is_repo = Gtk.RadioButton(label="A bundle on GitHub (release zips, like the Adafruit bundles)")
        self.is_url = Gtk.RadioButton(label="A package.json repository (the mip format)", group=self.is_repo)
        self.is_folder = Gtk.RadioButton(label="A folder or .zip file on this computer (your own libraries)",
                                         group=self.is_repo)
        self.is_index = Gtk.RadioButton(label="A package index like micropython-lib's (a folder with index.json)",
                                        group=self.is_repo)
        for radio in (self.is_repo, self.is_url, self.is_index, self.is_folder):
            radio.connect("toggled", lambda _b: self._kind_changed())
        grid.attach(self.is_repo, 1, 1, 1, 1)
        grid.attach(self.is_url, 1, 2, 1, 1)
        grid.attach(self.is_index, 1, 3, 1, 1)
        grid.attach(self.is_folder, 1, 4, 1, 1)
        self.address_label = Gtk.Label(xalign=1)
        self.address = Gtk.Entry(hexpand=True)
        grid.attach(self.address_label, 0, 5, 1, 1)
        grid.attach(self.address, 1, 5, 1, 1)
        self.browse = Gtk.Box(spacing=6)
        for label, folder in (("Choose a folder...", True), ("Choose a .zip...", False)):
            b = Gtk.Button(label=label)
            b.connect("clicked", lambda _b, f=folder: self._browse(f))
            self.browse.pack_start(b, False, False, 0)
        grid.attach(self.browse, 1, 6, 1, 1)
        self.pattern_label = Gtk.Label(label="File prefix", xalign=1)
        self.pattern = Gtk.Entry(hexpand=True, placeholder_text="the start of the release file names, e.g. qwiic-py")
        grid.attach(self.pattern_label, 0, 7, 1, 1)
        grid.attach(self.pattern, 1, 7, 1, 1)
        self.firmware = Gtk.ComboBoxText()
        self.firmware.append("micropython", "MicroPython")
        self.firmware.append("circuitpython", "CircuitPython")
        self.firmware.set_active(1)
        grid.attach(Gtk.Label(label="For", xalign=1), 0, 8, 1, 1)
        grid.attach(self.firmware, 1, 8, 1, 1)

    def _browse(self, folder):
        action = Gtk.FileChooserAction.SELECT_FOLDER if folder else Gtk.FileChooserAction.OPEN
        dlg = Gtk.FileChooserDialog(title="Choose a folder" if folder else "Choose a .zip file", transient_for=self,
                                    action=action)
        dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Choose", Gtk.ResponseType.OK)
        if not folder:
            zips = Gtk.FileFilter()
            zips.set_name("Zip files")
            zips.add_pattern("*.zip")
            dlg.add_filter(zips)
        path = dlg.get_filename() if dlg.run() == Gtk.ResponseType.OK else None
        dlg.destroy()
        if path:
            self.address.set_text(path)

    def _kind_changed(self):
        bundle, folder = self.is_repo.get_active(), self.is_folder.get_active()
        index = self.is_index.get_active()
        self.address_label.set_text("Folder or .zip" if folder else "Index address" if index else
                                    "Repository" if bundle else "Repository or address")
        self.address.set_placeholder_text("/home/pi/my-libs or /home/pi/my-libs.zip" if folder else
                                          "https://example.com/pi/v2 (the folder with index.json)" if index else
                                          "owner/name, e.g. sparkfun/qwiic_py" if bundle else
                                          "owner/name (add @branch), or https://.../package.json")
        self.pattern.set_visible(bundle)
        self.pattern_label.set_visible(bundle)
        self.browse.set_visible(folder)
        self.firmware.set_active_id("circuitpython" if bundle else "micropython")

    def source(self):
        text = self.address.get_text().strip()
        firmware = self.firmware.get_active_id()
        if self.is_folder.get_active():
            return {"name": self.name.get_text(), "kind": "folder", "path": text, "for": firmware}
        if self.is_index.get_active():
            return {"name": self.name.get_text(), "kind": "index", "url": text, "for": firmware}
        if self.is_repo.get_active():
            return {"name": self.name.get_text(), "kind": "bundle", "repo": text, "prefix": self.pattern.get_text(),
                    "for": firmware}
        key = "url" if text.startswith("https://") else "repo"
        return {"name": self.name.get_text(), "kind": "mip", key: text, "for": firmware}


class SourcesManager(Gtk.Box):
    """The list of custom sources with Add (presets and Custom), Remove, Check, Check all, and the trusted sites."""

    def __init__(self, app, parent, check=None, section="firmware"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=10)
        self.app, self.settings, self.parent = app, app.settings, parent
        self.section, self.cfg = section, SECTIONS[section]
        self.check = check or self.cfg["check"]
        self.presets = self.cfg["presets"]
        self.pack_start(Gtk.Label(label=self.cfg["title"], xalign=0), False, False, 0)
        self.store = Gtk.ListStore(str, str, str)                           # name, where, newest
        self.view = Gtk.TreeView(model=self.store)
        for i, head in enumerate(("Name", "Repository or address", "Newest")):
            self.view.append_column(Gtk.TreeViewColumn(head, Gtk.CellRendererText(), text=i))
        self.view.get_selection().connect("changed", lambda _s: self._sensitive())
        self.pack_start(scrolled(self.view), True, True, 0)

        buttons = Gtk.Box(spacing=6)
        self.add_btn = Gtk.MenuButton(label="Add")
        self.add_menu = Gtk.Menu()
        self.add_btn.set_popup(self.add_menu)
        buttons.pack_start(self.add_btn, False, False, 0)
        self.remove_btn = self._button("Remove", self.remove_selected, buttons)
        self.check_btn = self._button("Check", self.check_selected, buttons)
        self.check_all_btn = self._button("Check all", self.check_all, buttons)
        self.pack_start(buttons, False, False, 0)
        self.status = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.pack_start(self.status, False, False, 0)

        self.pack_start(Gtk.Label(label="Trusted sites (other than GitHub)", xalign=0), False, False, 4)
        self.hosts = Gtk.ListStore(str)
        hosts_view = Gtk.TreeView(model=self.hosts, headers_visible=False)
        hosts_view.append_column(Gtk.TreeViewColumn("", Gtk.CellRendererText(), text=0))
        self.hosts_view = hosts_view
        hosts_view.get_selection().connect("changed", lambda _s: self._sensitive())
        sw = scrolled(hosts_view)
        sw.set_size_request(-1, 70)
        self.pack_start(sw, False, False, 0)
        self.untrust_btn = self._button("Stop trusting", self.untrust_selected, None)
        self.pack_start(self.untrust_btn, False, False, 0)
        self.untrust_btn.set_halign(Gtk.Align.START)
        self.settings.subscribe(self._setting_changed)
        self.connect("destroy", lambda _w: self.settings.unsubscribe(self._setting_changed))
        self.refresh()
        self.show_all()

    @staticmethod
    def _button(label, callback, box):
        b = Gtk.Button(label=label)
        b.connect("clicked", lambda _b: callback())
        if box is not None:
            box.pack_start(b, False, False, 0)
        return b

    def _setting_changed(self, key, _value):
        if key.startswith("sources."):
            self.refresh()

    # -- what is shown ---------------------------------------------------------------------------------------------
    def refresh(self):
        selected = self.selected()
        self.store.clear()
        for src in self.settings.get(self.cfg["key"]):
            self.store.append([src["name"], where(src), ""])
        self.hosts.clear()
        for host in sorted(sources.trusted_hosts(self.settings)):
            self.hosts.append([host])
        for item in self.add_menu.get_children():
            self.add_menu.remove(item)
        have = {s["name"] for s in self.settings.get(self.cfg["key"])}
        for preset in self.presets:
            item = Gtk.MenuItem(label="%s (%s)" % (preset["name"], where(preset)))
            item.set_sensitive(preset["name"] not in have)
            item.connect("activate", lambda _i, p=preset: self.add_preset(p))
            self.add_menu.append(item)
        self.add_menu.append(Gtk.SeparatorMenuItem())
        custom = Gtk.MenuItem(label="Custom...")
        custom.connect("activate", lambda _i: self.add_custom())
        self.add_menu.append(custom)
        self.add_menu.show_all()
        if selected:
            for row in self.store:
                if row[0] == selected:
                    self.view.get_selection().select_iter(row.iter)
        self._sensitive()

    def selected(self):
        model, it = self.view.get_selection().get_selected()
        return model[it][0] if it else ""

    def _sensitive(self):
        one = bool(self.selected())
        self.remove_btn.set_sensitive(one)
        self.check_btn.set_sensitive(one)
        self.check_all_btn.set_sensitive(len(self.store) > 0)
        self.untrust_btn.set_sensitive(self.hosts_view.get_selection().get_selected()[1] is not None)

    # -- the actions -----------------------------------------------------------------------------------------------
    def _background(self, work, done):
        def run():
            try:
                res = work(), None
            except Exception as e:
                res = None, e
            GLib.idle_add(lambda: done(*res))
        threading.Thread(target=run, daemon=True).start()

    def add_preset(self, preset):
        """A preset still goes through the Check, then is saved: the repo may have moved since the plan was made."""
        self.status.set_text("Checking %s ..." % preset["name"])

        def done(got, exc):
            if exc or not got.ok:
                self.status.set_text("Not added: %s" % (exc or got.message))
                return
            sources.add_source(self.settings, self.section, self.cfg["validate"](preset))
            self.status.set_text("Added. %s" % got.message)
        self._background(lambda: self.check(preset, self.settings), done)

    def add_custom(self):
        dlg = (AddSourceDialog if self.section == "firmware" else AddLibrarySourceDialog)(self.parent, self.settings,
                                                                                         self.check)
        dlg.connect("destroy", lambda _d: self.status.set_text("Added %s." % dlg.saved["name"] if dlg.saved else ""))
        return dlg

    def remove_selected(self):
        name = self.selected()
        if name and ask_yes_no(self.parent, "Remove source",
                               'Remove "%s"? Boards already installed from it are not touched.' % name):
            sources.remove_source(self.settings, self.section, name)
            self.status.set_text('Removed "%s".' % name)

    def untrust_selected(self):
        model, it = self.hosts_view.get_selection().get_selected()
        if it is None:
            return
        host = model[it][0]
        users = [s["name"] for key in ("sources.firmware", "sources.libraries") for s in self.settings.get(key)
                 if sources.host_of(s.get("url", "")) == host]
        if users:
            info(self.parent, "Still in use", "%s is used by %s. Remove that source first." % (host, ", ".join(users)))
            return
        self.settings.set("sources.trusted_hosts", [h for h in self.settings.get("sources.trusted_hosts") if h != host])

    def check_selected(self):
        name = self.selected()
        src = next((s for s in self.settings.get(self.cfg["key"]) if s["name"] == name), None)
        if not src:
            return
        self.status.set_text("Checking %s ..." % name)
        def done(got, exc):
            self.status.set_text(str(exc) if exc else ("OK: " if got.ok else "Problem: ") + got.message)
        self._background(lambda: self.check(src, self.settings), done)

    def check_all(self):
        """The newest version each source offers, with no board involved (one request per source)."""
        self.status.set_text("Reading every source ...")
        found, newest = list(self.settings.get(self.cfg["key"])), self.cfg["newest"]

        def work():
            out = {}
            for src in found:
                try:
                    out[src["name"]] = newest(src)
                except Exception as e:
                    out[src["name"]] = "error: %s" % e
            return out

        def done(res, exc):
            if exc:
                self.status.set_text("Could not read the sources: %s" % exc)
                return
            for row in self.store:
                row[2] = res.get(row[0], "")
            self.status.set_text("Newest versions read.")
        self._background(work, done)


class SourcesDialog(Gtk.Dialog):
    """The manager in its own window, opened by the Manage sources... buttons; on_close runs when it closes."""

    def __init__(self, parent, app, on_close=None, check=None, section="firmware"):
        super().__init__(title="Manage sources", transient_for=parent, modal=True)
        self.set_default_size(620, 520)
        self.manager = SourcesManager(app, self, check, section)
        self.get_content_area().pack_start(self.manager, True, True, 0)
        self.add_button("Close", Gtk.ResponseType.CLOSE)
        self.connect("response", lambda *_: self.destroy())
        if on_close:
            self.connect("destroy", lambda _d: on_close())
        self.show_all()


class SourcesPage(Gtk.Box):
    """Preferences > Sources: the firmware and library managers one above the other."""

    def __init__(self, app, parent):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.managers = {name: SourcesManager(app, parent, section=name) for name in SECTIONS}
        for name, mgr in self.managers.items():
            self.pack_start(mgr, True, True, 0)
