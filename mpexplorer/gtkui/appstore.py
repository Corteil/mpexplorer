"""Tools > Badge apps: the badge app store (docs/APP_STORE_PLAN.md phase 2). Browse the catalogue, keep a local copy
for offline installs, mark favourites, and install, update or remove apps on a connected Tildagon OS badge. The logic
is in ..appstore; network work runs on a thread of its own, board work as jobs on the serial runner."""
import threading
import time

from .. import appstore as store_mod
from ..errors import Cancelled
from .common import GLib, Gtk, ask_yes_no, choose_local, error, info, prompt, scrolled

SHOW = (("all", "All apps"), ("favourites", "Favourites"), ("installed", "On the badge"),
        ("updates", "Updates available"), ("new", "Not on the badge"))
# list columns
FAV, NAME, AUTHOR, CATEGORY, VERSION, ON_BADGE, OFFLINE, KEY, CODE, STATE, TEXT = range(11)


def when(iso):
    """'2026-11-08T21:50:12Z' -> '8 Nov 21:50' (local time), or the text as it is if it cannot be read."""
    try:
        t = time.mktime(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
        return time.strftime("%d %b %H:%M", time.localtime(t)).lstrip("0")
    except (ValueError, TypeError, OverflowError):
        return iso or "?"


class BadgeAppsDialog(Gtk.Dialog):
    def __init__(self, app, backend=None, label="", pane=None, store=None, root="/"):
        """backend: the connected board's, or None to browse and download only. store, root: stand-ins for the
        store folder and the badge's file system root (tests)."""
        super().__init__(title="Badge apps", transient_for=app, modal=True)
        self.app, self.be, self.label, self.pane, self.root = app, backend, label, pane, root
        self.store = store or store_mod.Store(app.settings.get("appstore.folder") or None)
        self.apps, self.index, self.on_badge, self.os_version, self.free = [], {}, [], None, None
        self.busy, self.closed, self.job, self.cancel_flag, self.installed_any = False, False, None, False, False
        self.board_read = backend is None
        self.set_default_size(820, 600)
        box = self.get_content_area()
        box.set_spacing(6)
        box.set_border_width(10)

        top = Gtk.Box(spacing=8)
        self.store_line = Gtk.Label(xalign=0, label="Loading the app store ...", hexpand=True, ellipsize=3)
        top.pack_start(self.store_line, True, True, 0)
        self.refresh_btn = Gtk.Button(label="Refresh")
        self.refresh_btn.set_tooltip_text("Fetch the app store's list again")
        self.refresh_btn.connect("clicked", lambda _b: self.refresh())
        self.offline_btn = Gtk.Button(label="Download for offline")
        self.offline_btn.set_tooltip_text("Keep every app in the store folder, so they can be installed with no "
                                          "internet (about 12 MB the first time, then only new releases)")
        self.offline_btn.connect("clicked", lambda _b: self.download_offline())
        self.folder_btn = Gtk.Button(label="Store folder...")
        self.folder_btn.connect("clicked", lambda _b: self.choose_folder())
        for b in (self.folder_btn, self.offline_btn, self.refresh_btn):
            top.pack_end(b, False, False, 0)
        box.pack_start(top, False, False, 0)
        self.board_line = Gtk.Label(xalign=0, wrap=True)
        box.pack_start(self.board_line, False, False, 0)

        options = Gtk.Box(spacing=8)
        self.search = Gtk.SearchEntry(placeholder_text="Search names, authors and descriptions")
        self.search.connect("search-changed", lambda _e: self._refilter())
        options.pack_start(self.search, True, True, 0)
        options.pack_start(Gtk.Label(label="Category"), False, False, 0)
        self.category_box = Gtk.ComboBoxText()
        self.category_box.append("", "All")
        self.category_box.set_active_id("")
        self.category_box.connect("changed", lambda _c: self._refilter())
        options.pack_start(self.category_box, False, False, 0)
        options.pack_start(Gtk.Label(label="Show"), False, False, 0)
        self.show_box = Gtk.ComboBoxText()
        for key, text in SHOW:
            self.show_box.append(key, text)
        self.show_box.set_active_id("all")
        self.show_box.connect("changed", lambda _c: self._refilter())
        options.pack_start(self.show_box, False, False, 0)
        self.count = Gtk.Label(xalign=0, sensitive=False)
        options.pack_start(self.count, False, False, 0)
        box.pack_start(options, False, False, 0)

        self.model = Gtk.ListStore(bool, str, str, str, str, str, str, str, str, str, str)
        self.filter = self.model.filter_new()
        self.filter.set_visible_func(self._visible)
        self.sorted = Gtk.TreeModelSort(model=self.filter)
        view = Gtk.TreeView(model=self.sorted)
        star = Gtk.CellRendererToggle()
        star.connect("toggled", self._star_toggled)
        col = Gtk.TreeViewColumn("★", star, active=FAV)
        col.set_sort_column_id(FAV)
        view.append_column(col)
        for i, title in ((NAME, "App"), (AUTHOR, "Author"), (CATEGORY, "Category"), (VERSION, "Version"),
                         (ON_BADGE, "On the badge"), (OFFLINE, "Offline")):
            cell = Gtk.CellRendererText(ellipsize=3) if i in (NAME, AUTHOR) else Gtk.CellRendererText()
            col = Gtk.TreeViewColumn(title, cell, text=i)
            col.set_resizable(True)
            col.set_sort_column_id(i)
            if i in (NAME, AUTHOR):
                col.set_expand(True)
            view.append_column(col)
        view.get_selection().connect("changed", lambda _s: self._selected())
        view.set_search_column(NAME)
        self.view = view
        box.pack_start(scrolled(view), True, True, 0)

        self.details = Gtk.Label(xalign=0, yalign=0, wrap=True, selectable=True, label="Pick an app to see more.")
        self.details.set_size_request(-1, 90)
        box.pack_start(self.details, False, False, 0)
        self.note = Gtk.Label(xalign=0, wrap=True, selectable=True)
        box.pack_start(self.note, False, False, 0)
        self.bar = Gtk.ProgressBar(no_show_all=True)
        box.pack_start(self.bar, False, False, 0)

        self.close_btn = self.add_button("Close", Gtk.ResponseType.CLOSE)
        self.restart_btn = self.add_button("Restart the badge", 1)
        self.restart_btn.set_tooltip_text("Disconnect and restart the badge, so its menu shows the apps you installed")
        self.code_btn = self.add_button("Install by code...", 2)
        self.favs_btn = self.add_button("Install all favourites", 3)
        self.remove_btn = self.add_button("Remove", 4)
        self.install_btn = self.add_button("Install", Gtk.ResponseType.OK)
        self.connect("response", self._response)
        self.connect("delete-event", lambda *_: self._cancel() if self.busy else False)
        self.connect("destroy", lambda _w: setattr(self, "closed", True))
        self.show_all()
        self.bar.hide()
        self._board_text()
        self._sensitive()
        GLib.timeout_add(150, self._tick)
        self._show_saved()
        self.refresh()
        if backend is not None:
            self.read_board()

    # -- the catalogue ------------------------------------------------------------------------------------------------
    def _show_saved(self):
        """Fill the list from the saved copy at once (no waiting on the network); refresh() follows."""
        try:
            apps, fetched = self.store.load()
        except store_mod.AppStoreError:
            return
        self._fill(apps, {a.tarball_name for a in apps if self.store.has(a)})
        self.store_line.set_text("%d apps, saved list from %s: checking for a newer one ..." % (len(apps),
                                                                                               when(fetched)))

    def refresh(self):
        if self.busy:
            return
        self._lock(True, "Fetching the app store's list ...")

        def work():
            try:
                apps, fetched, online = self.store.catalogue()
                here = {a.tarball_name for a in apps if self.store.has(a)}
                res = (apps, fetched, online, here), None
            except Exception as e:      # noqa: BLE001 - shown in the dialog
                res = None, e
            GLib.idle_add(self._refreshed, *res)
        threading.Thread(target=work, name="appstore", daemon=True).start()

    def _refreshed(self, res, exc):
        if self.closed:
            return False
        self._lock(False)
        if exc:
            self.store_line.set_text("Could not load the app store: %s" % exc)
            return False
        apps, fetched, online, here = res
        self._fill(apps, here)
        kept = sum(1 for a in apps if a.tarball_name in here)
        self.store_line.set_text("%d apps, list from %s%s; %d of them kept for offline use." % (
            len(apps), when(fetched), "" if online else " (offline: the saved list)", kept))
        return False

    def _fill(self, apps, here):
        keep = self.selected_key()
        self.apps, self.index = apps, {a.key: a for a in apps}
        favs = self.store.favourites()
        self.model.clear()
        cats = sorted({a.category for a in apps if a.category})
        current = self.category_box.get_active_id() or ""
        self.category_box.remove_all()
        self.category_box.append("", "All")
        for c in cats:
            self.category_box.append(c, c)
        self.category_box.set_active_id(current if current in cats else "")
        for a in apps:
            self.model.append([a.key in favs, a.name, a.author, a.category, a.version, "",
                               "" if a.tarball_name in here else "not downloaded", a.key, a.code, "",
                               " ".join((a.name, a.author, a.description, a.title)).lower()])
        self._mark_badge()
        self._select_key(keep)

    # -- the board ------------------------------------------------------------------------------------------------
    def read_board(self):
        if self.be is None:
            return
        be, root = self.be, self.root

        def work(_prog):
            return (store_mod.board_os_version(be), store_mod.installed(be, root), store_mod.free_space(be, root))
        self.job = self.app.runner.submit(work, self._board_done, "Reading the badge's apps ...", quiet=True,
                                          inline=True, devices=(be,))

    def _board_done(self, res, exc):
        self.job = None
        if self.closed:
            return
        self.board_read = True
        if exc:
            self.board_line.set_text("Could not read the badge: %s" % exc)
        else:
            self.os_version, self.on_badge, self.free = res
            self._board_text()
            self._mark_badge()
        self._sensitive()
        self._selected()

    def is_badge(self):
        return self.be is not None and self.os_version is not None

    def _board_text(self):
        if self.be is None:
            text = "No badge connected: you can browse, mark favourites and download apps for offline use."
        elif not self.board_read:
            text = "Reading %s ..." % (self.label or "the board")
        elif self.os_version is None:
            text = "%s is not running Tildagon OS (the 2024 and 2026 badges), so apps cannot be installed on it." % (
                self.label or "This board")
        else:
            text = "For %s: Tildagon OS %s, %d apps on it%s." % (
                self.label or "the badge", self.os_version, len(self.on_badge),
                "" if self.free is None else ", %s free" % store_mod.kib(self.free))
        self.board_line.set_text(text)

    def _mark_badge(self):
        have = {i.folder: i for i in self.on_badge}
        for row in self.model:
            a = self.index.get(row[KEY])
            inst = have.get(a.folder) if a else None
            if inst is None:
                row[ON_BADGE], row[STATE] = "", ""
            elif store_mod.newer(a.version, inst.version):
                row[ON_BADGE], row[STATE] = "%s (update)" % inst.version, "update"
            else:
                row[ON_BADGE], row[STATE] = inst.version, "installed"
        self._update_count()

    # -- filtering and picking ---------------------------------------------------------------------------------------
    def _visible(self, model, it, _data):
        text = self.search.get_text().strip().lower()
        if text and text not in model[it][TEXT]:
            return False
        cat = self.category_box.get_active_id() or ""
        if cat and model[it][CATEGORY] != cat:
            return False
        show, state = self.show_box.get_active_id() or "all", model[it][STATE]
        return (show == "all" or (show == "favourites" and model[it][FAV]) or (show == "installed" and bool(state))
                or (show == "updates" and state == "update") or (show == "new" and not state))

    def _refilter(self):
        self.filter.refilter()
        self._update_count()

    def _update_count(self):
        total, shown = len(self.model), sum(1 for _ in self.filter)
        self.count.set_text("" if not total else "%d apps" % total if shown == total else "%d of %d" % (shown, total))

    def _row_of(self, sorted_path):
        filter_path = self.sorted.convert_path_to_child_path(Gtk.TreePath(sorted_path))
        return self.model[self.filter.convert_path_to_child_path(filter_path)]

    def _star_toggled(self, _cell, path):
        row = self._row_of(path)
        a = self.index.get(row[KEY])
        if a is None:
            return
        try:
            self.store.set_favourite(a, not row[FAV])
        except OSError as e:
            error(self, "Favourites", "Could not save the favourites in %s: %s" % (self.store.folder, e))
            return
        row[FAV] = not row[FAV]
        self._refilter()
        self._sensitive()

    def selected_key(self):
        model, it = self.view.get_selection().get_selected()
        return model[it][KEY] if it is not None else None

    def selected_app(self):
        return self.index.get(self.selected_key())

    def _select_key(self, key):
        if not key:
            return
        for row in self.sorted:
            if row[KEY] == key:
                self.view.get_selection().select_iter(row.iter)
                self.view.scroll_to_cell(row.path, None, False, 0, 0)
                return

    def installed_of(self, a):
        return next((i for i in self.on_badge if a is not None and i.folder == a.folder), None)

    def _selected(self):
        a = self.selected_app()
        if a is None:
            self.details.set_text("Pick an app to see more.")
        else:
            lines = ["%s %s by %s  (%s, code %s)" % (a.name, a.version, a.author, a.category or "no category", a.code),
                     a.description or "No description.",
                     "Licence: %s.  %s  Released %s." % (a.license or "not given", a.url, a.release_time[:10])]
            reqs = store_mod.requirements(a, self.os_version)
            if reqs:
                lines.append("Needs or can use: " + "; ".join(
                    "%s (%s%s)" % (r.text, "required" if r.required else "optional",
                                   "" if r.met is None else ", met" if r.met else ", NOT met on this badge")
                    for r in reqs) + ".")
            inst = self.installed_of(a)
            if inst is not None:
                lines.append("On the badge: %s/%s, version %s." % (inst.target, inst.folder, inst.version))
            self.details.set_text("\n".join(lines))
        self._sensitive()

    def _sensitive(self):
        a, idle, badge = self.selected_app(), not self.busy, self.is_badge()
        inst = self.installed_of(a)
        self.install_btn.set_label("Install" if inst is None else "Update" if store_mod.newer(a.version, inst.version)
                                   else "Reinstall")
        self.install_btn.set_sensitive(idle and badge and a is not None)
        self.remove_btn.set_sensitive(idle and badge and inst is not None)
        self.code_btn.set_sensitive(idle and badge and bool(self.apps))
        self.favs_btn.set_sensitive(idle and badge and bool(self.store.favourites()))
        self.restart_btn.set_sensitive(idle and badge and self.pane is not None)
        for w in (self.refresh_btn, self.offline_btn, self.folder_btn):
            w.set_sensitive(idle)

    # -- buttons ----------------------------------------------------------------------------------------------
    def _response(self, _d, res):
        if self.busy and res not in (Gtk.ResponseType.CLOSE, Gtk.ResponseType.DELETE_EVENT):
            return
        if res == Gtk.ResponseType.OK:
            self.install([self.selected_app()])
        elif res == 4:
            self.remove()
        elif res == 3:
            self.install_favourites()
        elif res == 2:
            self.install_by_code()
        elif res == 1:
            self.restart()
        elif self.busy:
            self._cancel()
        else:
            self.destroy()

    def _cancel(self):
        self.cancel_flag = True
        self.close_btn.set_sensitive(False)
        self.app.runner.cancel_all()
        return True

    def _lock(self, on, text=None):
        self.busy = on
        if on:
            self.cancel_flag = False
        for w in (self.view, self.search, self.show_box, self.category_box):
            w.set_sensitive(not on)
        self.bar.set_visible(on)
        self.bar.set_fraction(0)
        self.close_btn.set_label("Cancel" if on else "Close")
        self.close_btn.set_sensitive(True)
        if text is not None:
            self.note.set_text(text)
        self._sensitive()

    # -- the store folder and the offline copy ----------------------------------------------------------------
    def choose_folder(self):
        chosen = choose_local(self, self.app.settings, "appstore", "Choose the app store folder", "folder")
        if not chosen:
            return
        self.app.settings.set("appstore.folder", chosen)
        self.app.settings.save()
        self.store = store_mod.Store(chosen)
        self.note.set_text("Store folder: %s" % chosen)
        self.model.clear()
        self._show_saved()
        self.refresh()

    def download_offline(self):
        if self.busy or not self.apps:
            return
        self._lock(True, "Downloading the apps for offline use ...")
        apps = list(self.apps)

        def progress(done, total):
            GLib.idle_add(self._downloading, done, total)

        def work():
            try:
                res = self.store.download_all(apps, progress, lambda: self.cancel_flag), None
            except Exception as e:      # noqa: BLE001 - shown in the dialog
                res = None, e
            GLib.idle_add(self._downloaded, *res)
        threading.Thread(target=work, name="appstore-download", daemon=True).start()

    def _downloading(self, done, total):
        if not self.closed and self.busy:
            self.bar.set_fraction(done / max(total, 1))
            self.note.set_text("Downloading the apps for offline use ... %d of %d" % (done, total))
        return False

    def _downloaded(self, res, exc):
        if self.closed:
            return False
        self._lock(False)
        if exc:
            self.note.set_text("Stopped: %s" % exc)
        else:
            downloaded, present, failed = res
            text = "%d downloaded, %d already kept." % (downloaded, present)
            if failed:
                text += " %d could not be kept: %s." % (len(failed), "; ".join("%s (%s)" % (a.name, why)
                                                                             for a, why in failed[:5]))
            self.note.set_text(text)
        here = {a.tarball_name for a in self.apps if self.store.has(a)}
        for row in self.model:
            a = self.index.get(row[KEY])
            row[OFFLINE] = "" if a and a.tarball_name in here else "not downloaded"
        self._refreshed((self.apps, self.store.fetched(), True, here), None)
        return False

    # -- installing ---------------------------------------------------------------------------------------------
    def _check_requirements(self, apps):
        """Ask before installing an app whose required minimum OS the badge does not meet (decision 5: never block)."""
        lines = []
        for a in apps:
            for r in store_mod.unmet(store_mod.requirements(a, self.os_version)):
                lines.append("%s needs %s; this badge has %s." % (a.name, r.text, self.os_version))
        return not lines or ask_yes_no(self, "Badge apps", "\n".join(lines) + "\n\nInstall anyway?")

    def install(self, apps, confirm=True):
        apps = [a for a in apps if a is not None]
        if not apps or not self.is_badge() or not self._check_requirements(apps):
            return
        if confirm and len(apps) == 1:
            a, inst = apps[0], self.installed_of(apps[0])
            verb = ("Install" if inst is None else "Update" if store_mod.newer(a.version, inst.version)
                    else "Reinstall")
            if not ask_yes_no(self, "Badge apps", "%s %s %s on %s?\n\nIt goes in /%s/%s%s." % (
                    verb, a.name, a.version, self.label or "the badge", a.target, a.folder,
                    "" if inst is None else " (over version %s; files it no longer has are left)" % inst.version)):
                return
        self._lock(True, "Installing ...")
        be, store, root = self.be, self.store, self.root

        def work(prog):
            done, failed = [], []
            prog.items_word = "written"
            for n, a in enumerate(apps):
                prog.check()
                prog.label = "Installing %s (%d of %d)" % (a.name, n + 1, len(apps))
                try:
                    store_mod.install(store, be, a, root, on_progress=lambda i, t: setattr(prog, "items", i) or setattr(
                        prog, "items_total", t), cancelled=lambda: prog.check() or False)
                    done.append(a)
                except Cancelled:
                    raise
                except Exception as e:      # noqa: BLE001 - one app failing does not stop the rest
                    if "Cancelled" in str(e):
                        raise
                    failed.append((a, str(e)))
            return done, failed, store_mod.installed(be, root), store_mod.free_space(be, root)
        self.job = self.app.runner.submit(work, self._installed, "Installing badge apps ...", inline=True,
                                          devices=(be,))

    def _installed(self, res, exc):
        self.job = None
        if self.closed:
            return
        self._lock(False)
        if isinstance(exc, Cancelled) or (exc and "Cancelled" in str(exc)):
            self.note.set_text("Cancelled. An app part-way through may be incomplete: install it again.")
        elif exc:
            self.note.set_text("Failed: %s" % exc)
            error(self, "Badge apps", str(exc))
        else:
            done, failed, self.on_badge, self.free = res
            self.installed_any = self.installed_any or bool(done)
            text = "Installed %s." % ", ".join("%s %s" % (a.name, a.version) for a in done) if done else ""
            if failed:
                text += " Not installed: %s." % "; ".join("%s (%s)" % (a.name, why) for a, why in failed)
                error(self, "Badge apps", "\n".join("%s: %s" % (a.name, why) for a, why in failed))
            if done:
                text += " Press Restart the badge to see %s in its menu." % ("it" if len(done) == 1 else "them")
            self.note.set_text(text.strip())
            self._board_text()
            self._mark_badge()
            self.app.set_status("Badge apps installed" if done else "Badge apps")
            self.app._refresh_all()
        self.close_btn.set_sensitive(True)
        self._selected()

    def install_favourites(self):
        todo = store_mod.favourites_to_install(self.apps, self.store.favourites(), self.on_badge)
        if not todo:
            info(self, "Badge apps", "Every favourite is already on %s, at the store's version." % (
                self.label or "the badge"))
            return
        names = "\n".join("%s %s%s" % (a.name, a.version, " (update)" if self.installed_of(a) else "")
                          for a in todo)
        if ask_yes_no(self, "Install all favourites", "Put these on %s?\n\n%s" % (self.label or "the badge", names)):
            self.install(todo, confirm=False)

    def install_by_code(self):
        code = prompt(self, "Install by code", "The app's 8-digit code (from the app store's web page):")
        if code is None:
            return
        code = code.strip().replace(" ", "")
        a = next((x for x in self.apps if x.code == code), None)
        if a is None:
            error(self, "Install by code", "No app in the store has the code %s." % code)
            return
        self.show_box.set_active_id("all")
        self.search.set_text("")
        self._select_key(a.key)
        self.install([a])

    def remove(self):
        a = self.selected_app()
        inst = self.installed_of(a)
        if inst is None or not ask_yes_no(self, "Remove", "Delete %s (%s) and everything in it from %s?" % (
                inst.name, inst.path, self.label or "the badge")):
            return
        self._lock(True, "Removing %s ..." % inst.name)
        be, root = self.be, self.root

        def work(_prog):
            store_mod.remove(be, inst)
            return store_mod.installed(be, root), store_mod.free_space(be, root)
        self.job = self.app.runner.submit(work, self._removed, "Removing a badge app ...", inline=True, devices=(be,))

    def _removed(self, res, exc):
        self.job = None
        if self.closed:
            return
        self._lock(False)
        if exc:
            self.note.set_text("Failed: %s" % exc)
            error(self, "Remove", str(exc))
        else:
            self.on_badge, self.free = res
            self.installed_any = True
            self.note.set_text("Removed. Press Restart the badge to update its menu.")
            self._board_text()
            self._mark_badge()
            self.app._refresh_all()
        self._selected()

    def restart(self):
        if self.pane is None or not ask_yes_no(
                self, "Restart the badge", "Disconnect from %s and restart it, so its menu shows the apps you "
                "installed?" % (self.label or "the badge")):
            return
        pane = self.pane
        self.destroy()
        self.app.disconnect(pane, reset=True)

    def _tick(self):
        if self.closed:
            return False
        if self.job is not None and self.busy:
            self.bar.set_fraction(self.job.prog.fraction())
            text = self.job.prog.describe()
            if text:
                self.note.set_text(text)
        return True
