"""GTK 3 main window: two panes, toolbar, job progress. Same behaviour as the Tk version (mpexplorer/gui/app.py)."""
import queue
import threading

from gi.repository import GdkPixbuf, GLib, Gtk

from .. import __version__
from ..backends import CHUNK, DeviceBackend, LocalBackend, human
from ..clipboard import Clipboard
from ..console import ConsoleSession
from ..errors import BackendError, Cancelled
from ..jobs import JobRunner
from ..ops import ConflictPolicy, copy_tree, delete_tree, is_within, move_item, total_size, unique_name
from ..ports import list_ports
from ..sync import apply_plan, build_plan
from ..transport import RawREPL
from .common import ICON, ask_yes_no, button, error, fmt_time, info, prompt
from .console import ConsoleWindow
from .dialogs import OutputWindow, SyncDialog, ask_conflict
from .editor import Editor
from .pane import Pane

MAX_EDIT_BYTES = 256 * 1024


class App(Gtk.Window):
    def __init__(self):
        super().__init__(title="MicroPython Explorer %s" % __version__)
        self.set_default_size(1180, 680)
        try:
            self.set_icon(GdkPixbuf.Pixbuf.new_from_file(str(ICON)))
        except GLib.Error:
            pass                                    # a missing icon is cosmetic only
        self.runner = JobRunner()
        self.clipboard = Clipboard()
        self.local = LocalBackend()
        self.device = None
        self.session = None
        self.console = None
        self.windows = []                           # open editors and the console
        self.show_hidden = False
        self.drag_source = None
        self._asks = queue.Queue()

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(root)
        root.pack_start(self._build_menu(), False, False, 0)
        root.pack_start(self._build_toolbar(), False, False, 4)
        split = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, margin_start=4, margin_end=4)
        self.left = Pane(self, "Host")
        self.right = Pane(self, "Device")
        split.pack1(self.left, True, False)
        split.pack2(self.right, True, False)
        self.panes = (self.left, self.right)
        self.active = self.left
        root.pack_start(split, True, True, 2)
        root.pack_start(self._build_statusbar(), False, False, 3)

        self.left.set_location(self.local, self.local.root)
        self.right.clear()
        self.connect("delete-event", lambda *_: self._quit() or False)
        self.connect("destroy", lambda _w: Gtk.main_quit())
        self.show_all()
        GLib.timeout_add(100, self._poll)

    # -- ui construction ---------------------------------------------------
    def _build_menu(self):
        bar = Gtk.MenuBar()

        def menu(title):
            item, sub = Gtk.MenuItem(label=title), Gtk.Menu()
            item.set_submenu(sub)
            bar.append(item)
            return sub

        def add(sub, label, cb):
            it = Gtk.MenuItem(label=label)
            it.connect("activate", lambda _i: cb())
            sub.append(it)
            return it

        f = menu("File")
        add(f, "Connect", self.connect_device)
        add(f, "Disconnect", self.disconnect)
        f.append(Gtk.SeparatorMenuItem())
        add(f, "New folder    F7", self.new_folder)
        add(f, "New file", self.new_file)
        f.append(Gtk.SeparatorMenuItem())
        add(f, "Quit", self.close)
        e = menu("Edit")
        add(e, "Copy    Ctrl+C", lambda: self.copy("copy"))
        add(e, "Cut    Ctrl+X", lambda: self.copy("cut"))
        add(e, "Paste    Ctrl+V", self.paste)
        add(e, "Select all    Ctrl+A", lambda: self.active.select_all())
        e.append(Gtk.SeparatorMenuItem())
        add(e, "Rename    F2", self.rename)
        add(e, "Delete    Del", self.delete)
        add(e, "Properties", self.properties)
        v = menu("View")
        hidden = Gtk.CheckMenuItem(label="Show hidden files")
        hidden.connect("toggled", lambda i: self._set_hidden(i.get_active()))
        v.append(hidden)
        add(v, "Refresh    F5", lambda: [p.refresh() for p in self.panes])
        d = menu("Device")
        add(d, "REPL console...", self.open_console)
        add(d, "Run selected script", self.run_script)
        add(d, "Free space", self.free_space)
        add(d, "Soft reset", self.soft_reset)
        t = menu("Tools")
        add(t, "Sync this folder to the other pane...", self.sync_folders)
        w = menu("Window")
        w.connect("show", lambda m: self._fill_window_menu(m))
        return bar

    def _set_hidden(self, on):
        self.show_hidden = on
        for p in self.panes:
            p._render()

    def _fill_window_menu(self, menu):
        for child in menu.get_children():
            menu.remove(child)

        def add(label, cb):
            it = Gtk.MenuItem(label=label)
            it.connect("activate", lambda _i: cb())
            menu.append(it)
        add("Main window", self.present)
        for win in self.windows:
            add(win.get_title(), win.present)
        menu.append(Gtk.SeparatorMenuItem())
        add("Minimise all", lambda: [w.iconify() for w in self.windows + [self]])
        add("Restore all", lambda: [w.present() for w in [self] + self.windows])
        menu.show_all()

    def _build_toolbar(self):
        bar = Gtk.Box(spacing=4, margin_start=4, margin_end=4)
        bar.pack_start(Gtk.Label(label="Port"), False, False, 0)
        self.port_box = Gtk.ComboBoxText.new_with_entry()
        self.port_box.set_size_request(340, -1)
        bar.pack_start(self.port_box, False, False, 4)
        bar.pack_start(button("Scan", self.scan_ports), False, False, 0)
        self.connect_btn = button("Connect", self.toggle_connection)
        bar.pack_start(self.connect_btn, False, False, 4)
        bar.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 6)
        bar.pack_start(button("Upload >>", lambda: self.transfer(self.left, self.right)), False, False, 0)
        bar.pack_start(button("<< Download", lambda: self.transfer(self.right, self.left)), False, False, 4)
        bar.pack_start(button("Copy", lambda: self.copy("copy")), False, False, 0)
        bar.pack_start(button("Cut", lambda: self.copy("cut")), False, False, 0)
        bar.pack_start(button("Paste", self.paste), False, False, 0)
        bar.pack_start(button("New folder", self.new_folder), False, False, 6)
        bar.pack_start(button("Rename", self.rename), False, False, 0)
        bar.pack_start(button("Delete", self.delete), False, False, 0)
        self.scan_ports()
        return bar

    def _build_statusbar(self):
        bar = Gtk.Box(spacing=6, margin_start=4, margin_end=4)
        self.status = Gtk.Label(label="Ready", xalign=0, ellipsize=3)       # 3 = end
        bar.pack_start(self.status, True, True, 0)
        self.bar = Gtk.ProgressBar()
        self.bar.set_size_request(260, -1)
        bar.pack_start(self.bar, False, False, 0)
        bar.pack_start(button("Cancel", self.runner.cancel_all), False, False, 0)
        return bar

    def context_menu(self):
        m = Gtk.Menu()

        def add(label, cb):
            it = Gtk.MenuItem(label=label)
            it.connect("activate", lambda _i: cb())
            m.append(it)

        sel = self.active.selected()
        if len(sel) == 1:
            add("Open", self.active._open)
        other = self.other_pane(self.active)
        add("Send to %s" % other.title, lambda: self.transfer(self.active, other))
        m.append(Gtk.SeparatorMenuItem())
        add("Copy", lambda: self.copy("copy"))
        add("Cut", lambda: self.copy("cut"))
        add("Paste", self.paste)
        m.append(Gtk.SeparatorMenuItem())
        add("Rename", self.rename)
        add("Delete", self.delete)
        add("New folder", self.new_folder)
        add("New file", self.new_file)
        add("Properties", self.properties)
        m.show_all()
        return m

    # -- panes -------------------------------------------------------------
    def set_active(self, pane):
        self.active = pane

    def set_drag_source(self, pane):
        self.drag_source = pane

    def other_pane(self, pane):
        return self.right if pane is self.left else self.left

    def set_status(self, text):
        self.status.set_text(text)

    def _need_backend(self, pane=None):
        pane = pane or self.active
        if not pane.backend:
            info(self, "Not connected", "Connect to a device first.")
            return None
        if pane.paused:
            info(self, "Terminal active", "The console is in terminal mode. Switch it back to emulated "
                 "mode to use the device files.")
            return None
        return pane

    def set_device_paused(self, paused):
        """Terminal mode owns the serial line, so the device pane is frozen until it is released."""
        if self.right.backend:
            self.right.set_paused(paused)

    # -- connection --------------------------------------------------------
    def scan_ports(self):
        ports = list_ports()
        self.port_box.remove_all()
        for p in ports:
            self.port_box.append_text(p)
        entry = self.port_box.get_child()
        if ports and not entry.get_text():
            self.port_box.set_active(0)

    def toggle_connection(self):
        if self.device:
            self.disconnect()
        else:
            self.connect_device()

    def connect_device(self):
        port = self.port_box.get_child().get_text().split("  ")[0].strip()
        if not port:
            info(self, "No port", "Pick a serial port first (Scan).")
            return
        self.disconnect()
        self.set_status("Connecting to %s ..." % port)
        self.connect_btn.set_label("Connecting...")
        self.connect_btn.set_sensitive(False)

        def work(prog):
            repl = RawREPL.open(port)
            try:
                repl.enter_raw()
                return DeviceBackend(repl)
            except BaseException:
                repl.close()
                raise

        def done(dev, exc):
            self.connect_btn.set_sensitive(True)
            if exc:
                self.connect_btn.set_label("Connect")
                self.set_status("Connection failed")
                error(self, "Connection failed", str(exc))
                return
            self.device = dev
            self.session = ConsoleSession(dev.repl)
            self.right.set_location(dev, dev.root)
            self.connect_btn.set_label("Disconnect")
            self.set_status("Connected to %s" % port)
        self.runner.submit(work, done, "Connecting", quiet=True)

    def disconnect(self):
        if self.device:
            dev, self.device = self.device, None
            self.session = None
            if self.console:
                self.console.close()
            self.right.clear()
            self.runner.submit(lambda prog: dev.close(), label="Disconnecting", quiet=True)
            self.connect_btn.set_label("Connect")
            self.connect_btn.set_sensitive(True)
            self.set_status("Disconnected")

    def _quit(self):
        self.runner.cancel_all()
        if self.device:
            try:
                self.device.close()
            except Exception:
                pass

    # -- clipboard and transfers -------------------------------------------
    def copy(self, mode):
        pane = self.active
        items = [(pane.backend, p) for _n, p in pane.selected()]
        if items:
            self.clipboard.set(mode, items)
            self.set_status("%s %d item(s)" % ("Copied" if mode == "copy" else "Cut", len(items)))

    def paste(self):
        pane = self._need_backend()
        if pane and self.clipboard.items:
            move = self.clipboard.mode == "cut"
            self._start_copy(self.clipboard.items, pane.backend, pane.cwd, move)
            if move:
                self.clipboard.clear()

    def transfer(self, src, dst):
        if not (self._need_backend(src) and self._need_backend(dst)):
            return False
        items = [(src.backend, p) for _n, p in src.selected()]
        if items:
            self._start_copy(items, dst.backend, dst.cwd, move=False)
        return False

    def ask_conflict(self, name):
        """Called on the worker thread; blocks until the GUI thread has asked the user."""
        req = {"name": name, "event": threading.Event(), "result": ("cancel", False)}
        self._asks.put(req)
        req["event"].wait()
        return req["result"]

    def _start_copy(self, items, dst_be, dst_dir, move):
        verb = "Moving" if move else "Copying"

        def work(prog):
            prog.total = sum(total_size(be, p) for be, p in items)
            policy = ConflictPolicy(self.ask_conflict)
            for be, path in items:
                name = be.basename(path)
                dst = dst_be.join(dst_dir, name)
                if be is dst_be and dst == path:
                    if move:
                        continue
                    dst = unique_name(dst_be, dst)
                elif be is dst_be and is_within(be, dst_dir, path):
                    raise BackendError("Cannot put a folder inside itself: %s" % name)
                if move:
                    move_item(be, path, dst_be, dst, prog, policy)
                else:
                    copy_tree(be, path, dst_be, dst, prog, policy)
            return policy.skipped

        def done(skipped, exc):
            self._refresh_all()
            if isinstance(exc, Cancelled):
                self.set_status("Cancelled")
            elif exc:
                self.set_status("Failed")
                error(self, "%s failed" % verb, str(exc))
            else:
                self.set_status("Done" + (" (%d skipped)" % skipped if skipped else ""))
        self.runner.submit(work, done, verb + " ...")

    def _refresh_all(self):
        for p in self.panes:
            p.refresh()

    # -- file operations ---------------------------------------------------
    def _simple_job(self, fn, label, error_title):
        def done(_res, exc):
            self._refresh_all()
            if exc and not isinstance(exc, Cancelled):
                error(self, error_title, str(exc))
        self.runner.submit(fn, done, label)

    def delete(self):
        pane = self._need_backend()
        sel = pane.selected() if pane else []
        if not sel:
            return
        if not ask_yes_no(self, "Delete", "Permanently delete %d item(s)?\n\n%s" % (
                len(sel), "\n".join(n for n, _p in sel[:8]))):
            return
        be = pane.backend

        def work(prog):
            for _n, p in sel:
                delete_tree(be, p, prog)
        self._simple_job(work, "Deleting ...", "Delete failed")

    def new_folder(self):
        pane = self._need_backend()
        name = pane and prompt(self, "New folder", "Folder name:")
        if name:
            be, path = pane.backend, pane.backend.join(pane.cwd, name)
            self._simple_job(lambda prog: be.mkdir(path), "Creating folder", "Cannot create folder")

    def new_file(self):
        pane = self._need_backend()
        name = pane and prompt(self, "New file", "File name:")
        if name:
            be, path = pane.backend, pane.backend.join(pane.cwd, name)
            self._simple_job(lambda prog: be.open_write(path).close(), "Creating file", "Cannot create file")

    def rename(self):
        pane = self._need_backend()
        sel = pane.selected() if pane else []
        if len(sel) != 1:
            return
        old_name, old = sel[0]
        new_name = prompt(self, "Rename", "New name:", old_name)
        if not new_name or new_name == old_name:
            return
        be, new = pane.backend, pane.backend.join(pane.cwd, new_name)

        def work(prog):
            if be.stat(new) is not None:
                raise BackendError("'%s' already exists" % new_name)
            be.rename(old, new)
        self._simple_job(work, "Renaming", "Rename failed")

    def properties(self):
        pane = self._need_backend()
        sel = pane.selected() if pane else []
        if len(sel) != 1:
            return
        be, path = pane.backend, sel[0][1]

        def work(prog):
            return be.stat(path), total_size(be, path), be.free_space(path)

        def done(res, exc):
            if exc:
                error(self, "Properties", str(exc))
                return
            st, size, free = res
            info(self, "Properties", "%s\n\nType: %s\nSize: %s (%d bytes)\nModified: %s\nFree space: %s" % (
                path, "Folder" if st.is_dir else "File", human(size), size, fmt_time(st.mtime), human(free)))
        self.runner.submit(work, done, "Reading properties", quiet=True)

    # -- editor and device tools ------------------------------------------
    def open_file(self, pane, path, size):
        if size > MAX_EDIT_BYTES:
            info(self, "Too large", "Files over %s are not opened in the editor." % human(MAX_EDIT_BYTES))
            return
        be = pane.backend

        def work(prog):
            chunks = []
            with be.open_read(path) as r:
                while True:
                    data = r.read(CHUNK)
                    if not data:
                        return b"".join(chunks)
                    chunks.append(data)

        def done(data, exc):
            if exc:
                error(self, "Cannot open file", str(exc))
                return
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                info(self, "Binary file", "%s is not UTF-8 text, so it is not opened." % path)
                return
            Editor(self, be, path, text)
        self.runner.submit(work, done, "Reading " + path, quiet=True)

    def write_file(self, be, path, data, on_done):
        def work(prog):
            with be.open_write(path) as w:
                for i in range(0, len(data), CHUNK):
                    w.write(data[i:i + CHUNK])

        def done(res, exc):
            on_done(res, exc)
            self._refresh_all()
        self.runner.submit(work, done, "Saving " + path, quiet=True)

    def run_script(self):
        pane = self.right
        sel = pane.selected() if pane.backend and pane.backend.key == "device" else []
        if len(sel) != 1 or not sel[0][0].endswith(".py"):
            info(self, "Run", "Select one .py file on the device first.")
            return
        be, path = pane.backend, sel[0][1]
        self.runner.submit(lambda prog: be.run(path), lambda out, exc: OutputWindow(self, path, out, exc),
                           "Running " + path, quiet=True)

    def open_console(self):
        if not self.session:
            info(self, "Not connected", "Connect to a device first.")
        elif self.console:
            self.console.present()
        else:
            self.console = ConsoleWindow(self, self.session)

    def sync_folders(self):
        src = self.active
        dst = self.other_pane(src)
        if not (self._need_backend(src) and self._need_backend(dst)):
            return
        s_be, s_dir, d_be, d_dir = src.backend, src.cwd, dst.backend, dst.cwd
        if s_be is d_be and (is_within(s_be, d_dir, s_dir) or is_within(s_be, s_dir, d_dir)):
            error(self, "Sync", "The two folders must be separate (neither inside the other).")
            return

        def done(plan, exc):
            if exc:
                if not isinstance(exc, Cancelled):
                    error(self, "Sync", str(exc))
                self.set_status("Sync cancelled" if isinstance(exc, Cancelled) else "Sync failed")
            elif not plan.actions:
                self.set_status("Folders are already in sync")
                info(self, "Sync", "Nothing to do: the folders already match.")
            else:
                SyncDialog(self, plan)
        self.runner.submit(lambda prog: build_plan(s_be, s_dir, d_be, d_dir, prog), done, "Comparing folders ...")

    def run_sync(self, plan, include_delete):
        def done(_res, exc):
            self._refresh_all()
            if isinstance(exc, Cancelled):
                self.set_status("Sync cancelled")
            elif exc:
                self.set_status("Sync failed")
                error(self, "Sync failed", str(exc))
            else:
                self.set_status("Sync complete")
        self.runner.submit(lambda prog: apply_plan(plan, prog, include_delete), done, "Syncing ...")

    def free_space(self):
        if self.device and self._need_backend(self.right):
            dev = self.device
            self.runner.submit(lambda prog: dev.free_space("/"),
                               lambda res, exc: info(self, "Free space",
                                                     str(exc) if exc else "Device: %s free" % human(res)),
                               "Checking space", quiet=True)

    def soft_reset(self):
        if not (self.device and self._need_backend(self.right)):
            return
        if ask_yes_no(self, "Soft reset", "Soft-reset the board? Any running program stops."):
            dev = self.device
            self.runner.submit(lambda prog: dev.soft_reset(), lambda r, e: self._refresh_all(),
                               "Resetting", quiet=True)

    # -- main loop tick ----------------------------------------------------
    def _poll(self):
        for job, res, exc in self.runner.drain():
            try:
                if job.on_done:
                    job.on_done(res, exc)
                elif exc:
                    self.set_status(str(exc))
            except Exception as e:           # a broken callback must not kill the poller
                self.set_status("Internal error: %s" % e)
        try:
            req = self._asks.get_nowait()
        except queue.Empty:
            req = None
        if req:
            req["result"] = ask_conflict(self, req["name"])
            req["event"].set()
        job = self.runner.current
        if job and not job.quiet:
            p = job.prog
            self.bar.set_fraction(min(1.0, p.done / p.total) if p.total else 0)
            self.set_status("%s  %s / %s  (%s/s)" % (p.label, human(p.done), human(p.total), human(p.rate())))
        elif not job:
            self.bar.set_fraction(0)
        return True


def run():
    GLib.set_prgname("mpexplorer")              # becomes the Wayland app_id: the taskbar finds mpexplorer.desktop
    GLib.set_application_name("MicroPython Explorer")
    Gtk.Window.set_default_icon_name("mpexplorer")
    App()
    Gtk.main()
