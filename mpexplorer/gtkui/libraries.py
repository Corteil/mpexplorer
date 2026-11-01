"""Tools > Install libraries: pick MicroPython libraries and write them to the connected board's /lib.
The logic is in ..libraries; the files are fetched here on the host and uploaded, nothing runs on the board."""
import threading
import time

from .. import libraries as lib
from ..errors import Cancelled
from .common import GLib, Gtk, ask_yes_no, error, scrolled


class LibrariesDialog(Gtk.Dialog):
    def __init__(self, app, backend, label="", catalog=None):
        super().__init__(title="Install libraries", transient_for=app, modal=True)
        self.app, self.be, self.label = app, backend, label
        self.catalog = catalog or lib.Catalog()
        self.libs, self.have, self.mpy, self.closed, self.busy, self.job = [], set(), None, False, False, None
        self.firmware = None
        self.dest = backend.join(backend.mount, "lib") if hasattr(backend, "mount") else lib.DEST
        self.set_default_size(620, 520)
        box = self.get_content_area()
        box.set_spacing(6)
        box.set_border_width(10)
        box.pack_start(Gtk.Label(label="Libraries from micropython-lib, written to /lib on %s" % (label or "the board"),
                                 xalign=0), False, False, 0)
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
        threading.Thread(target=self._load, daemon=True).start()
        self.job = app.runner.submit(self._read_board, self._board_read, "Reading the board ...", quiet=True,
                                     inline=True, devices=(backend,))

    # -- loading ---------------------------------------------------------------------------------------------
    def _load(self):
        try:
            res = self.catalog.libraries(), None
        except Exception as e:
            res = None, e
        GLib.idle_add(self._loaded, *res)

    def _read_board(self, _prog):
        return lib.board_mpy(self.be), set(lib.installed(self.be, self.dest)), lib.board_firmware(self.be)

    def _board_read(self, res, exc):
        self.job = None
        if self.closed:
            return
        if exc:
            self.note.set_text("Could not read the board: %s" % exc)
            return
        self.mpy, self.have, self.firmware = res
        self._mark_installed()
        if self.is_circuitpython():
            self.mpy = None             # CircuitPython refuses MicroPython .mpy files (seen on a real board)
            self.force.show()
            self.note.set_text("This board runs CircuitPython. These are MicroPython libraries: tick "
                               "\"Install anyway\" only if you know they will work there (source files are used).")
        self._sensitive()

    def _loaded(self, res, exc):
        if self.closed:
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

    def is_circuitpython(self):
        return self.firmware == "circuitpython"

    def _sensitive(self):
        ready = bool(self.ticked()) and not self.busy
        self.install_btn.set_sensitive(ready and (not self.is_circuitpython() or self.force.get_active()))
        self.remove_btn.set_sensitive(ready)

    # -- installing ------------------------------------------------------------------------------------------
    def _response(self, _d, res):
        if res == Gtk.ResponseType.OK and not self.busy:
            self.install(self.ticked())
        elif res == Gtk.ResponseType.REJECT and not self.busy:
            self.remove(self.ticked())
        elif res not in (Gtk.ResponseType.OK, Gtk.ResponseType.REJECT):
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

    def remove(self, names):
        if not names or not ask_yes_no(
                self, "Remove libraries", "Delete the files of %s from /lib on %s?\n\n"
                "Libraries they depend on are left alone." % (", ".join(names), self.label or "the board")):
            return
        self._lock(True)
        be, catalog, mpy = self.be, self.catalog, self.mpy

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
        if not names or (self.is_circuitpython() and not self.force.get_active()):
            return
        self._lock(True)
        self.note.set_text("Working out what is needed ...")

        def work():
            try:
                res = self.catalog.resolve(names, self.mpy), None
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

    def _tick(self):
        if self.closed:
            return False
        if self.job is not None and self.busy:
            self.bar.set_fraction(self.job.prog.fraction())
            self.note.set_text(self.job.prog.describe())
        return True
