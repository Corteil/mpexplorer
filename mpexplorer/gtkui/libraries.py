"""Tools > Install libraries: pick libraries (micropython-lib, or the Adafruit and Community CircuitPython
bundles) and write them to the connected board's lib folder. The logic is in ..libraries and ..cpbundle; the files
are fetched here on the host and uploaded, nothing runs on the board."""
import threading
import time

from .. import cpbundle
from .. import libraries as lib
from ..errors import Cancelled
from .common import GLib, Gtk, ask_yes_no, error, scrolled


NAMES = {"micropython": "MicroPython", "circuitpython": "CircuitPython"}


class LibrariesDialog(Gtk.Dialog):
    def __init__(self, app, backend, label="", catalog=None, bundles=None):
        """catalog, bundles: stand-ins for the micropython-lib catalog and the bundle catalog factory (tests)."""
        super().__init__(title="Install libraries", transient_for=app, modal=True)
        self.app, self.be, self.label = app, backend, label
        # key -> (title, firmware the libraries are for, the catalog once made)
        make = bundles or (lambda key: cpbundle.Catalog(key))
        self.makers = {"micropython": lambda: catalog or lib.Catalog(), "adafruit": lambda: make("adafruit"),
                       "community": lambda: make("community")}
        self.titles = {"micropython": ("micropython-lib (MicroPython)", "micropython"),
                       "adafruit": ("Adafruit bundle (CircuitPython)", "circuitpython"),
                       "community": ("Community bundle (CircuitPython)", "circuitpython")}
        self.catalogs, self.source, self.load_id = {}, "micropython", 0
        self.libs, self.have, self.mpy, self.closed, self.busy, self.job = [], set(), None, False, False, None
        self.firmware, self.version, self.chose = None, "", False
        self.dest = backend.join(backend.mount, "lib") if hasattr(backend, "mount") else lib.DEST
        self.set_default_size(620, 520)
        box = self.get_content_area()
        box.set_spacing(6)
        box.set_border_width(10)
        top = Gtk.Box(spacing=8)
        top.pack_start(Gtk.Label(label="Libraries from", xalign=0), False, False, 0)
        self.source_box = Gtk.ComboBoxText()
        for key, (title, _fw) in self.titles.items():
            self.source_box.append(key, title)
        self.source_box.set_active_id(self.source)
        self.source_box.connect("changed", lambda _c: self._source_changed())
        top.pack_start(self.source_box, False, False, 0)
        top.pack_start(Gtk.Label(label="for %s" % (label or "the board"), xalign=0), False, False, 0)
        box.pack_start(top, False, False, 0)
        self.search = Gtk.SearchEntry(placeholder_text="Search")
        self.search.connect("search-changed", lambda _e: self.filter.refilter())
        box.pack_start(self.search, False, False, 0)
        self.store = Gtk.ListStore(bool, str, str, str, str)        # ticked, name, version, installed, description
        self.filter = self.store.filter_new()
        self.filter.set_visible_func(self._visible)
        view = Gtk.TreeView(model=self.filter)
        toggle = Gtk.CellRendererToggle()
        toggle.connect("toggled", self._toggled)
        view.append_column(Gtk.TreeViewColumn("", toggle, active=0))
        for i, title in ((1, "Library"), (2, "Version"), (3, "In /lib"), (4, "Description")):
            view.append_column(Gtk.TreeViewColumn(title, Gtk.CellRendererText(), text=i))
        self.view = view
        box.pack_start(scrolled(view), True, True, 0)
        self.note = Gtk.Label(xalign=0, wrap=True, selectable=True, label="Loading the list ...")
        box.pack_start(self.note, False, False, 0)
        self.force = Gtk.CheckButton(label="Install anyway", no_show_all=True)
        self.force.connect("toggled", lambda _c: self._sensitive())
        box.pack_start(self.force, False, False, 0)
        self.bar = Gtk.ProgressBar(no_show_all=True)
        box.pack_start(self.bar, False, False, 0)
        self.close_btn = self.add_button("Close", Gtk.ResponseType.CLOSE)
        self.check_btn = self.add_button("Check for updates", Gtk.ResponseType.APPLY)
        self.check_btn.set_sensitive(False)
        self.remove_btn = self.add_button("Remove", Gtk.ResponseType.REJECT)
        self.remove_btn.set_sensitive(False)
        self.install_btn = self.add_button("Install", Gtk.ResponseType.OK)
        self.install_btn.set_sensitive(False)
        self.connect("response", self._response)
        self.connect("delete-event", lambda *_: self._cancel() if self.busy else False)
        self.connect("destroy", lambda _w: setattr(self, "closed", True))
        self.show_all()
        self.bar.hide()
        GLib.timeout_add(150, self._tick)
        self._start_load()
        self.job = app.runner.submit(self._read_board, self._board_read, "Reading the board ...", quiet=True,
                                     inline=True, devices=(backend,))

    # -- loading ---------------------------------------------------------------------------------------------
    @property
    def catalog(self):
        if self.source not in self.catalogs:
            self.catalogs[self.source] = self.makers[self.source]()
        return self.catalogs[self.source]

    def _start_load(self):
        self.load_id += 1
        self.note.set_text("Loading the list ...")
        threading.Thread(target=self._load, args=(self.load_id, self.catalog), daemon=True).start()

    def _load(self, load_id, catalog):
        try:
            res = catalog.libraries(), None
        except Exception as e:
            res = None, e
        GLib.idle_add(self._loaded, res[0], res[1], load_id)

    def _source_changed(self):
        key = self.source_box.get_active_id()
        if not key or key == self.source or self.busy:
            if self.busy:
                self.source_box.set_active_id(self.source)
            return
        self.chose = True
        self.source = key
        self.store.clear()
        self._sensitive()
        self._start_load()

    def _read_board(self, _prog):
        return lib.board_mpy(self.be), set(lib.installed(self.be, self.dest)), lib.board_implementation(self.be)

    def _board_read(self, res, exc):
        self.job = None
        if self.closed:
            return
        if exc:
            self.note.set_text("Could not read the board: %s" % exc)
            return
        self.mpy, self.have, (self.firmware, self.version) = res
        self._mark_installed()
        if self.firmware == "circuitpython" and not self.chose and self.source == "micropython":
            self.source_box.set_active_id("adafruit")       # the usual choice for this board; the user can change it
        else:
            self._mismatch_note()
        self._sensitive()

    def libs_for(self):
        return self.titles[self.source][1]

    def mismatch(self):
        """True when this list is for the other firmware than the board runs: needs 'Install anyway'."""
        return bool(self.firmware) and self.firmware != self.libs_for()

    def _mismatch_note(self):
        self.force.set_visible(self.mismatch())
        if self.mismatch():
            self.note.set_text("This board runs %s but these libraries are for %s: tick \"Install anyway\" only if you "
                               "know they will work there (source files are used)." % (
                                   NAMES.get(self.firmware, self.firmware), NAMES[self.libs_for()]))

    def resolve_arg(self):
        """What `catalog.resolve` needs to pick the right files: micropython-lib wants the board's .mpy format number,
        the bundles want its CircuitPython major version. Libraries for the other firmware always get source files."""
        if self.mismatch() or not self.firmware:
            return None
        if self.libs_for() == "micropython":
            return self.mpy
        try:
            return int(self.version.split(".")[0])
        except ValueError:
            return None

    def _loaded(self, res, exc, load_id=None):
        if self.closed or (load_id is not None and load_id != self.load_id):
            return
        if exc:
            self.note.set_text("Could not load the list: %s" % exc)
            return
        self.libs, saved = res
        self.store.clear()
        for item in self.libs:
            self.store.append([False, item.name, item.version, "", item.description])
        self._mark_installed()
        self.note.set_text("%d libraries%s." % (len(self.libs), "" if saved is None else
                                                " (saved list from %s: no network)" % time.strftime(
                                                    "%Y-%m-%d", time.localtime(saved))))
        self._mismatch_note()
        self._sensitive()

    def _mark_installed(self):
        for row in self.store:
            row[3] = "yes" if row[1].replace("-", "_") in self.have or row[1] in self.have else ""

    # -- picking ---------------------------------------------------------------------------------------------
    def _visible(self, model, it, _data):
        text = self.search.get_text().strip().lower()
        return not text or text in model[it][1].lower() or text in model[it][4].lower()

    def _toggled(self, _cell, path):
        row = self.store[self.filter.convert_path_to_child_path(Gtk.TreePath(path))]
        row[0] = not row[0]
        self._sensitive()

    def ticked(self):
        return [r[1] for r in self.store if r[0]]

    def _sensitive(self):
        ready = bool(self.ticked()) and not self.busy
        self.install_btn.set_sensitive(ready and (not self.mismatch() or self.force.get_active()))
        self.remove_btn.set_sensitive(ready)
        self.check_btn.set_sensitive(not self.busy and any(r[3] for r in self.store))

    # -- installing ------------------------------------------------------------------------------------------
    def _response(self, _d, res):
        if res == Gtk.ResponseType.OK and not self.busy:
            self.install(self.ticked())
        elif res == Gtk.ResponseType.REJECT and not self.busy:
            self.remove(self.ticked())
        elif res == Gtk.ResponseType.APPLY and not self.busy:
            self.check_updates()
        elif res not in (Gtk.ResponseType.OK, Gtk.ResponseType.REJECT, Gtk.ResponseType.APPLY):
            if self.busy:
                self._cancel()
            else:
                self.destroy()

    def _cancel(self):
        self.close_btn.set_sensitive(False)
        self.app.runner.cancel_all()
        return True

    def _lock(self, on):
        self.busy = on
        self.view.set_sensitive(not on)
        self.search.set_sensitive(not on)
        self.bar.set_visible(on)
        self.close_btn.set_label("Cancel" if on else "Close")
        self._sensitive()

    def installed_names(self):
        return [r[1] for r in self.store if r[3]]

    def check_updates(self):
        """Compare every library already in /lib with the catalog's current copy (one bundle download, then cached)."""
        names = self.installed_names()
        if not names:
            return
        self._lock(True)
        self.note.set_text("Checking %d installed librar%s ..." % (len(names), "y" if len(names) == 1 else "ies"))
        be, catalog, arg, dest = self.be, self.catalog, self.resolve_arg(), self.dest
        catalog.on_progress = lambda done, total: GLib.idle_add(self._download, done, total)

        def work(prog):
            return lib.compare(catalog, be, names, arg, dest, cancelled=lambda: prog.check() or False)
        self.job = self.app.runner.submit(work, self._checked, "Checking libraries ...", inline=True, devices=(be,))

    def _checked(self, res, exc):
        self.job = None
        if self.closed:
            return
        self._lock(False)
        self.catalog.on_progress = None
        if exc:
            self.note.set_text("Could not check: %s" % exc)
            return
        words = {"current": "up to date", "differs": "update available", "incomplete": "incomplete"}
        for row in self.store:
            if row[1] in res:
                row[3] = words[res[row[1]]]
        newer = [n for n, v in res.items() if v != "current"]
        self.note.set_text("%d checked: %s." % (len(res), "%d to update (tick them and press Install)" % len(newer)
                                                if newer else "all up to date"))
        self._sensitive()

    def remove(self, names):
        if not names or not ask_yes_no(
                self, "Remove libraries", "Delete the files of %s from /lib on %s?\n\n"
                "Libraries they depend on are left alone." % (", ".join(names), self.label or "the board")):
            return
        self._lock(True)
        be, catalog, mpy = self.be, self.catalog, self.resolve_arg()

        def work(prog):
            return lib.remove(catalog, be, names, mpy, self.dest, cancelled=lambda: prog.check() or False)
        self.job = self.app.runner.submit(work, self._removed, "Removing libraries ...", inline=True, devices=(be,))

    def _removed(self, res, exc):
        self.job = None
        if self.closed:
            return
        self._lock(False)
        if exc:
            self.note.set_text("Failed: %s" % exc)
            error(self, "Remove libraries", str(exc))
        else:
            self.note.set_text("Removed %d file(s)." % res)
            self.app.set_status("Libraries removed")
            self.app._refresh_all()
        self.job = self.app.runner.submit(self._read_board, self._board_read, "Reading the board ...", quiet=True,
                                          inline=True, devices=(self.be,))

    def install(self, names):
        """Work out what the ticked libraries need (host, network), ask, then write them."""
        if not names or (self.mismatch() and not self.force.get_active()):
            return
        self._lock(True)
        self.note.set_text("Working out what is needed ...")
        catalog, arg = self.catalog, self.resolve_arg()
        catalog.on_progress = lambda done, total: GLib.idle_add(self._download, done, total)

        def work():
            try:
                res = catalog.resolve(names, arg), None
            except Exception as e:
                res = None, e
            GLib.idle_add(self._resolved, *res)
        threading.Thread(target=work, daemon=True).start()

    def _resolved(self, plan, exc):
        if self.closed:
            return
        if exc:
            self._lock(False)
            self.note.set_text("Could not install: %s" % exc)
            return
        lines, files = lib.summary(plan)
        if not ask_yes_no(self, "Install libraries", "Write %d file(s) to /lib on %s?\n\n%s\n\n"
                          "Identical files are skipped; changed files are overwritten."
                          % (files, self.label or "the board", "\n".join(lines))):
            self._lock(False)
            self.note.set_text("Nothing written.")
            return
        be, catalog = self.be, self.catalog
        catalog.on_progress = None

        def write(prog):
            prog.items_total, prog.items_word = files, "written"
            return lib.install(catalog, be, plan, self.dest, on_progress=lambda i, n: setattr(prog, "items", i),
                               cancelled=lambda: prog.check() or False)
        self.job = self.app.runner.submit(write, self._written, "Installing libraries ...", inline=True,
                                          devices=(be,))

    def _written(self, res, exc):
        self.job = None
        if self.closed:
            return
        self._lock(False)
        if isinstance(exc, Cancelled) or (exc and "Cancelled" in str(exc)):
            self.note.set_text("Cancelled.")
        elif exc:
            self.note.set_text("Failed: %s" % exc)
            error(self, "Install libraries", str(exc))
        else:
            self.note.set_text("Done: %d written, %d already there." % res)
            self.app.set_status("Libraries installed")
            self.app._refresh_all()
        self.close_btn.set_sensitive(True)
        self.job = self.app.runner.submit(self._read_board, self._board_read, "Reading the board ...", quiet=True,
                                          inline=True, devices=(self.be,))

    def _download(self, done, total):
        if not self.closed and self.busy and self.job is None:
            self.bar.set_fraction(done / total)
            self.note.set_text("Downloading the bundle ... %d of %d MB" % (done >> 20, total >> 20))

    def _tick(self):
        if self.closed:
            return False
        if self.job is not None and self.busy:
            self.bar.set_fraction(self.job.prog.fraction())
            self.note.set_text(self.job.prog.describe())
        return True
