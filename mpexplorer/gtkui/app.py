"""GTK 3 main window: two panes, toolbar, job progress."""
import os
import posixpath
import queue
import threading
import time
import weakref

from .. import __version__
from ..backends import CHUNK, DeviceBackend, Entry, LocalBackend, human
from ..clipboard import Clipboard
from ..console import ConsoleSession
from ..errors import BackendError, Cancelled
from ..jobs import JobRunner
from ..badgescreen import BadgeScreen
from ..openwith import EditManager, app_template, handler_for, parse_handler, remember_handler
from ..ops import ConflictPolicy, copy_tree, delete_items, is_within, move_item, total_size, unique_name
from ..ports import board_id, device_of, list_ports, short_model
from ..remember import device_start, host_start, port_choice, remember_board_name, remember_folder, remember_port
from ..settings import Settings
from ..transport import RawREPL
from ..watchdog import Watchdog
from .common import (ICON, Gdk, GdkPixbuf, Gio, GLib, Gtk, ask_yes_no, button, error, fmt_time, icon_button, info,
                     prompt, set_icon)
from .console import ConsoleWindow
from .dialogs import (OutputWindow, SyncDialog, TransferPopup, ask_conflict, ask_edit_conflict, ask_open_with,
                      ask_sync_target)
from .editor import Editor
from .viewer import Viewer
from .pane import Pane
from .prefs import PreferencesDialog

MAX_EDIT_BYTES = 256 * 1024
MAX_IMAGE_BYTES = 2 * 1024 * 1024


def cache_dir():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "mpexplorer", "open")


POPUP_DELAY = 1.0       # seconds a file job runs before its progress window opens
POPUP_MIN_VISIBLE = 3.0     # ... and the least time it then stays up, even if the job ends sooner
STATUS_HOLD = 5.0       # seconds a message stays in the status bar before it goes back to "Ready"


class App(Gtk.Window):
    def __init__(self, settings=None):
        super().__init__(title="MicroPython Explorer %s" % __version__)
        self.settings = settings if settings is not None else Settings.load()
        self._normal_size = (self.settings.get("window.width"), self.settings.get("window.height"))
        self._restore_window()
        try:
            self.set_icon(GdkPixbuf.Pixbuf.new_from_file(str(ICON)))
        except GLib.Error:
            pass                                    # a missing icon is cosmetic only
        self.runner = JobRunner()
        me = weakref.ref(self)              # the worker thread outlives windows: do not keep this one alive
        self.runner.hook = lambda job, phase, exc: me() and me()._badge_hook(job, phase, exc)
        self.badge = None                   # BadgeScreen while a board with a screen is connected
        self._popup, self._popup_job, self._popup_since, self._popup_dismissed = None, None, 0.0, False
        self._status_timer = None
        self.clipboard = Clipboard()
        self.local = LocalBackend()
        self.edits = EditManager(self.runner, cache_dir(), self._edit_say, self._edit_conflict, self._refresh_all)
        self.device = None
        self.session = None
        self.console = None
        self.windows = []                           # open editors and the console
        self.show_hidden = self.settings.get("general.show_hidden")
        self.board = None                           # settings key of the connected board
        self.settings.subscribe(self._setting_changed)
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

        for pane, which in ((self.left, "host"), (self.right, "device")):
            key, reverse = self.settings.get("general.sort." + which)
            pane._sort = (key, reverse)
        self.left.set_location(self.local, host_start(self.settings, self.local.root))
        self.right.clear()
        self.connect("delete-event", lambda *_: self._quit() or False)
        self.connect("configure-event", self._configured)
        self.connect("destroy", lambda _w: (self._stop_status_timer(), self.watchdog.stop(), Gtk.main_quit()))
        self.show_all()
        if self.settings.problems:
            self.set_status("Settings: " + self.settings.problems[0], keep=True)       # a warning stays
        self.watchdog = Watchdog()
        self.watchdog.start()
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
        add(f, "Open with...    Shift+Enter", self.open_with_selected)
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
        e.append(Gtk.SeparatorMenuItem())
        add(e, "Preferences...", self.preferences)
        v = menu("View")
        self.hidden_item = Gtk.CheckMenuItem(label="Show hidden files")
        self.hidden_item.set_active(self.show_hidden)
        self.hidden_item.connect("toggled", lambda i: self._set_hidden(i.get_active()))
        v.append(self.hidden_item)
        add(v, "Refresh    F5", lambda: [p.refresh() for p in self.panes])
        d = menu("Device")
        add(d, "REPL console...", self.open_console)
        add(d, "Run selected script", self.run_script)
        add(d, "Free space", self.free_space)
        add(d, "Set board time now", self.set_board_clock)
        add(d, "Soft reset", self.soft_reset)
        t = menu("Tools")
        add(t, "Sync this folder to the other pane...", self.sync_folders)
        w = menu("Window")
        w.connect("show", lambda m: self._fill_window_menu(m))
        return bar

    def _set_hidden(self, on):
        self.settings.set("general.show_hidden", on)        # _setting_changed does the rest
        self.settings.save()

    def _setting_changed(self, key, value):
        """The one place a changed setting reaches the window, whether the View menu or Preferences changed it."""
        if key == "general.show_hidden":
            self.show_hidden = bool(value)
            if self.hidden_item.get_active() != self.show_hidden:
                self.hidden_item.set_active(self.show_hidden)
            for p in self.panes:
                p._render()
        elif key == "general.device_mtimes":
            self.right.refresh()

    def preferences(self):
        dlg = PreferencesDialog(self)
        dlg.run()
        dlg.destroy()

    # -- remembered state --------------------------------------------------
    def _restore_window(self):
        s = self.settings
        if not s.get("general.remember_window"):
            self.set_default_size(1180, 680)
            return
        self.set_default_size(*self._normal_size)
        if s.get("window.maximized"):
            self.maximize()
        x, y = s.get("window.x"), s.get("window.y")
        if x is not None and y is not None and self._is_x11():
            self.move(x, y)                             # Wayland never lets a window place itself

    @staticmethod
    def _is_x11():
        display = Gdk.Display.get_default()
        return display is not None and "X11" in type(display).__name__

    def _configured(self, _w, ev):
        if not self.is_maximized():
            self._normal_size = (ev.width, ev.height)
        return False

    def folder_changed(self, pane):
        remember_folder(self.settings, pane is self.right, pane.cwd, self.board)
        if pane is self.right:
            self._badge_draw(folder=pane.cwd)

    def sort_changed(self, pane):
        which = "device" if pane is self.right else "host"
        self.settings.set("general.sort." + which, list(pane._sort))
        self.settings.save()

    def save_state(self):
        """Called on the way out: window geometry and everything that changed since the last save."""
        s = self.settings
        if s.get("general.remember_window"):
            s.set("window.width", self._normal_size[0])
            s.set("window.height", self._normal_size[1])
            s.set("window.maximized", self.is_maximized())
            if self._is_x11() and not self.is_maximized():
                x, y = self.get_position()
                s.set("window.x", x)
                s.set("window.y", y)
        s.save()

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
        """Two groups that share one row when the window is wide enough and wrap onto a second row when it is not."""
        conn = Gtk.Box(spacing=4)
        conn.pack_start(Gtk.Label(label="Port"), False, False, 0)
        self.port_box = Gtk.ComboBoxText.new_with_entry()
        self.port_box.set_size_request(340, -1)
        conn.pack_start(self.port_box, False, False, 4)
        conn.pack_start(icon_button("view-refresh", "Scan for serial ports", self.scan_ports, "Scan"), False, False, 0)
        self.connect_btn = icon_button("network-offline", "Connect to the board", self.toggle_connection, "Connect")
        conn.pack_start(self.connect_btn, False, False, 4)
        self.send_time_btn = icon_button(("appointment-soon", "appointment-new", "x-office-calendar"),
                                         "Set the board's clock to this computer's time (UTC)",
                                         self.set_board_clock, "Send time")
        conn.pack_start(self.send_time_btn, False, False, 0)
        files = Gtk.Box(spacing=4)
        for icon, tip, cb, label, pad in (
                ("go-next", "Upload selected to the device", lambda: self.transfer(self.left, self.right),
                 "Upload >>", 0),
                ("go-previous", "Download selected to the host", lambda: self.transfer(self.right, self.left),
                 "<< Download", 4),
                ("edit-copy", "Copy (Ctrl+C)", lambda: self.copy("copy"), "Copy", 0),
                ("edit-cut", "Cut (Ctrl+X)", lambda: self.copy("cut"), "Cut", 0),
                ("edit-paste", "Paste (Ctrl+V)", self.paste, "Paste", 0),
                ("folder-new", "New folder (F7)", self.new_folder, "New folder", 6),
                ("accessories-text-editor", "Rename (F2)", self.rename, "Rename", 0),
                (("user-trash", "edit-delete"), "Delete (Del)", self.delete, "Delete", 0)):
            files.pack_start(icon_button(icon, tip, cb, label), False, False, pad)
        bar = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=2, homogeneous=False,
                          column_spacing=16, row_spacing=4, margin_start=4, margin_end=4, valign=Gtk.Align.START)
        bar.add(conn)
        bar.add(files)
        for child in bar.get_children():
            child.set_can_focus(False)          # the buttons take focus, not the row wrappers
        self.scan_ports()
        return bar

    def _build_statusbar(self):
        """A centred progress bar (60% of the width) with the status text under it, lined up with the bar's left edge;
        Cancel beside the bar's right end, as tall as the bar and the text together."""
        grid = Gtk.Grid(column_homogeneous=True, row_spacing=2, column_spacing=6, margin_start=4, margin_end=4)
        grid.attach(Gtk.Label(label=""), 0, 0, 1, 2)                       # equal side columns: 20% | 60% | 20%
        self.bar = Gtk.ProgressBar(valign=Gtk.Align.CENTER, hexpand=True)
        grid.attach(self.bar, 1, 0, 3, 1)
        self.status = Gtk.Label(label="Ready", xalign=0, ellipsize=3, hexpand=True)       # 3 = end
        grid.attach(self.status, 1, 1, 3, 1)
        cancel = button("Cancel", self.runner.cancel_all, "Cancel the running job")
        cancel.set_halign(Gtk.Align.START)
        cancel.set_valign(Gtk.Align.FILL)       # stretches to the height of the bar and the text beside it
        grid.attach(cancel, 4, 0, 1, 2)
        return grid

    def context_menu(self):
        m = Gtk.Menu()

        def add(label, cb):
            it = Gtk.MenuItem(label=label)
            it.connect("activate", lambda _i: cb())
            m.append(it)

        sel = self.active.selected()
        if len(sel) == 1:
            add("Open", self.active._open)
            entry = self.active.entry(sel[0][0])
            if entry and not entry.is_dir:
                add("Open with...", self.open_with_selected)
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

    def set_status(self, text, keep=False):
        """Show text in the status bar. It goes back to "Ready" after STATUS_HOLD seconds, unless keep is set.
        A running job refreshes its progress every 100 ms, which restarts the countdown until the job ends."""
        self.status.set_text(text)
        self._stop_status_timer()
        if text != "Ready" and not keep:
            self._status_timer = GLib.timeout_add(int(STATUS_HOLD * 1000), self._status_ready)

    def _stop_status_timer(self):
        if self._status_timer is not None:
            GLib.source_remove(self._status_timer)
            self._status_timer = None

    def _status_ready(self):
        self._status_timer = None
        self.status.set_text("Ready")
        return False

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
        ports = list_ports(self.settings.get("boards.names"))
        self.port_box.remove_all()
        for p in ports:
            self.port_box.append_text(p)
        entry = self.port_box.get_child()
        choice = port_choice(self.settings, ports)
        if choice is not None and not entry.get_text():
            self.port_box.set_active(choice)

    def toggle_connection(self):
        if self.device:
            self.disconnect()
        else:
            self.connect_device()

    def connect_device(self):
        port = device_of(self.port_box.get_child().get_text())
        if not port:
            info(self, "No port", "Pick a serial port first (Scan).")
            return
        self.disconnect()
        self.set_status("Connecting to %s ..." % port)
        self._connect_state("connecting")
        self.connect_btn.set_sensitive(False)

        board = board_id(port)

        def work(prog):
            repl = RawREPL.open(port)
            try:
                repl.enter_raw()
                dev = DeviceBackend(repl)
                try:
                    machine = repl.exec("import os;print(os.uname().machine)").decode().strip()
                except Exception:
                    machine = ""            # a build without os.uname: the port list keeps its generic name
                return dev, device_start(self.settings, dev, board), machine
            except BaseException:
                repl.close()
                raise

        def done(res, exc):
            self.connect_btn.set_sensitive(True)
            if exc:
                self._connect_state("connect")
                self.set_status("Connection failed")
                error(self, "Connection failed", str(exc))
                return
            self.connected(res[0], res[1], port, board, res[2])
        self.runner.submit(work, done, "Connecting", quiet=True)

    def connected(self, dev, start, port, board, machine=""):
        """The connection is up: show the board, and remember the port, its model (and later its folders)."""
        self.device = dev
        remember_board_name(self.settings, board, machine)
        self.right.detail = "  ".join(x for x in (port, short_model(machine) if machine else "") if x)
        self.board = board
        self.session = ConsoleSession(dev.repl)
        remember_port(self.settings, port)
        self.right.set_location(dev, start)
        self._connect_state("disconnect")
        self.connect_btn.set_sensitive(True)
        self.set_status("Connected to %s" % port)
        if self.settings.get("general.set_board_clock"):
            self._set_clock(dev, "Connected to %s; " % port)
        self.badge = BadgeScreen(dev.repl) if self.settings.get("general.badge_screen") else None
        self._badge_draw(folder=start)

    def _connect_state(self, state):
        """The Connect button shows what a click would do: plug in, unplug, or wait."""
        icon, tip, label = {"connect": ("network-offline", "Connect to the board", "Connect"),
                            "connecting": ("emblem-synchronizing", "Connecting...", "Connecting..."),
                            "disconnect": ("network-wired", "Disconnect from the board", "Disconnect")}[state]
        set_icon(self.connect_btn, icon, tip, label)

    def _badge_draw(self, **kw):
        """Queue a redraw of the badge's status screen (a no-op without one)."""
        badge = self.badge
        if badge:
            self.runner.submit(lambda prog: badge.show(**kw), label="Badge screen", quiet=True)

    def _badge_hook(self, job, phase, exc):
        """Worker thread, around every job: say what the board is doing (quiet jobs are not worth a redraw)."""
        badge = self.badge
        if not badge or job.quiet:
            return
        if phase == "start":
            badge.show(job.status, kind="busy")
        else:
            badge.show("Cancelled" if isinstance(exc, Cancelled) else "Failed" if exc else "Done")

    def disconnect(self):
        if self.device:
            dev, self.device, board = self.device, None, self.board
            self.board = None
            self._release_edits(dev, board)
            self.session = None
            if self.console:
                self.console.close()
            self.right.clear()
            reset = self.settings.get("general.reset_on_disconnect")
            if self.badge and not reset:
                self._badge_draw(status="press reset button", kind="gone")
            self.badge = None
            self.runner.submit(lambda prog: dev.close(reset), label="Disconnecting", quiet=True)
            self._connect_state("connect")
            self.connect_btn.set_sensitive(True)
            self.set_status("Disconnected")

    def _release_edits(self, dev, board):
        """Disconnecting: send saves still waiting (before the board is closed), then drop the cache copies."""
        if not any(s.board == board for s in self.edits.sessions):
            return

        def done(left, exc):
            kept = self.edits.close_board(board)
            if exc or left or kept:
                names = ", ".join(sorted(set(left or []) | set(kept)))
                error(self, "Edits not uploaded", "These files have saves that were not sent to the board:\n\n%s\n\n"
                      "Your copies are kept in %s" % (names or str(exc), cache_dir()))
        self.runner.submit(lambda prog: self.edits.flush(dev, board), done, "Uploading saved edits", quiet=True)

    def _quit(self):
        self.save_state()
        self.runner.cancel_all()
        for _ in range(20):                         # an interrupted script needs a moment to let go of the serial line
            if not self.runner.busy:
                break
            time.sleep(0.1)
        if self.device and not self.runner.busy:
            try:
                self.edits.flush(self.device, self.board)         # saves still waiting go up before the board is closed
                self.edits.close_board(self.board)
            except Exception:
                pass
        if self.device:
            try:
                reset = self.settings.get("general.reset_on_disconnect")
                if self.badge and not reset:
                    self.badge.show("press reset button", kind="gone")
                self.badge = None
                self.device.close(reset)
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
        first, rest = items[0], len(items) - 1
        word = ("Uploading" if dst_be.key == "device" and first[0].key != "device" else
                "Downloading" if first[0].key == "device" and dst_be.key != "device" else verb)
        status = "%s %s%s" % (word, first[0].basename(first[1]), " +%d" % rest if rest else "")
        self.runner.submit(work, done, verb + " ...", status=status)

    def _refresh_all(self):
        for p in self.panes:
            p.refresh()

    # -- file operations ---------------------------------------------------
    def _simple_job(self, fn, label, error_title):
        def done(_res, exc):
            self._refresh_all()
            if isinstance(exc, Cancelled):
                self.set_status("Cancelled")
            elif exc:
                self.set_status("Failed")
                error(self, error_title, str(exc))
            else:
                self.set_status("Done")
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
            delete_items(be, [p for _n, p in sel], prog)
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
    def open_external(self, pane, path, template):
        """Open a file in an external program: a host file directly, a device file as an edit-in-place session."""
        if pane.backend.key == "local":
            err = self.edits.open_host_file(path, template)
            if err:
                error(self, "Cannot open file", err)
        elif self.device:
            self.edits.edit_device_file(self.device, self.board, path, template)

    def open_default(self, pane, path, size):
        """Double-click / Enter: the program saved for this file type, else the built-in editor."""
        self.open_with(pane, path, size, handler_for(self.settings, posixpath.basename(path)))

    def open_with_selected(self):
        """File menu / Shift+Enter / context menu: choose the program for the one selected file."""
        pane = self.active
        sel = pane.selected()
        entry = pane.entry(sel[0][0]) if len(sel) == 1 else None
        if entry is None or entry.is_dir:
            info(self, "Open with", "Select one file first.")
            return
        self.choose_open_with(pane, sel[0][1], entry.size)

    def choose_open_with(self, pane, path, size):
        name = posixpath.basename(path)
        can_run = name.endswith(".py") and pane.backend.key == "device"
        picked = ask_open_with(self, name, can_run)
        if picked is None:
            return
        handler, custom, remember = picked
        if remember:
            remember_handler(self.settings, name, handler, custom)
        elif custom:
            remember_handler(self.settings, "", handler, custom)        # the command is kept; no default is set
        self.open_with(pane, path, size, handler)

    def open_with(self, pane, path, size, handler):
        """Open a file the way `handler` says: 'editor' | 'run' | 'app:<desktop id>' | 'custom:<name>' (openwith.py)."""
        try:
            kind, value = parse_handler(self.settings, handler)
            if kind == "app":
                desktop = Gio.DesktopAppInfo.new(value)
                if desktop is None:
                    raise ValueError("the application %s is not installed any more" % value)
                kind, value = "command", app_template(desktop.get_commandline())
        except ValueError as e:
            error(self, "Cannot open file", str(e))
            return
        if kind == "command":
            self.open_external(pane, path, value)
        elif value == "run":
            self.run_path(pane, path)
        elif value in ("hex", "image"):
            self.view_file(pane, path, size, value)
        else:
            self.open_file(pane, path, size)

    def _edit_say(self, text):
        self.set_status(text)

    def _edit_conflict(self, name):
        return ask_edit_conflict(self, name)

    # -- editor and device tools (built-in) ---------------------------------
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

    def view_file(self, pane, path, size, kind):
        limit = MAX_IMAGE_BYTES if kind == "image" else MAX_EDIT_BYTES
        if size > limit:
            info(self, "Too large", "Files over %s are not opened in the %s viewer." % (human(limit), kind))
            return
        be = pane.backend

        def work(prog):
            with be.open_read(path) as r:
                return b"".join(iter(lambda: r.read(CHUNK), b""))

        def done(data, exc):
            if exc:
                error(self, "Cannot open file", str(exc))
            else:
                Viewer(self, be, path, data, kind)
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
        self.run_path(pane, sel[0][1])

    def run_path(self, pane, path):
        be = pane.backend
        if be.key != "device" or not path.endswith(".py"):
            info(self, "Run", "Only a .py file on the device can be run.")
            return
        self.runner.submit(lambda prog: be.run(path), lambda out, exc: OutputWindow(self, path, out, exc),
                           "Running " + path, quiet=True, on_cancel=be.repl.interrupt)     # Cancel / Quit send Ctrl-C

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
        picked = src.selected()                         # one selected folder is synced instead of the current folder
        chosen = picked[0][1] if len(picked) == 1 and (src.entry(picked[0][0]) or Entry("", False)).is_dir else None
        s_be, s_dir, d_be = src.backend, chosen or src.cwd, dst.backend
        d_dir = ask_sync_target(self, s_be, s_dir, bool(chosen), d_be, dst.cwd)
        if d_dir is None:
            return
        if s_be is d_be and (is_within(s_be, d_dir, s_dir) or is_within(s_be, s_dir, d_dir)):
            error(self, "Sync", "The two folders must be separate (neither inside the other).")
            return

        SyncDialog(self, s_be, s_dir, d_be, d_dir)

    def free_space(self):
        if self.device and self._need_backend(self.right):
            dev = self.device
            self.runner.submit(lambda prog: dev.free_space("/"),
                               lambda res, exc: info(self, "Free space",
                                                     str(exc) if exc else "Device: %s free" % human(res)),
                               "Checking space", quiet=True)

    def set_board_clock(self):
        if self.device and self._need_backend(self.right):
            self._set_clock(self.device)

    def _set_clock(self, dev, prefix=""):
        def done(res, exc):
            self.set_status(prefix + ("could not set the board clock: %s" % exc if exc
                                      else "board clock set to %04d-%02d-%02d %02d:%02d:%02d UTC" % tuple(res)))
        self.runner.submit(lambda prog: dev.set_clock(), done, "Setting the clock", quiet=True)

    def soft_reset(self):
        if not (self.device and self._need_backend(self.right)):
            return
        if ask_yes_no(self, "Soft reset", "Soft-reset the board? Any running program stops."):
            dev = self.device
            self.runner.submit(lambda prog: dev.soft_reset(), lambda r, e: self._refresh_all(),
                               "Resetting", quiet=True)

    # -- main loop tick ----------------------------------------------------
    def _poll(self):
        self.watchdog.beat()
        try:
            self.edits.tick()
        except Exception as e:           # a broken tick must not stop the poller
            self.set_status("Internal error: %s" % e)
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
        if job and not job.quiet and not job.inline:
            p = job.prog
            if p.known():
                self.bar.set_fraction(p.fraction())
            else:
                self.bar.pulse()                    # nothing to measure against yet
            self.set_status(p.describe())
        elif not job:
            self.bar.set_fraction(0)
        self._update_popup(job)
        return True

    def _update_popup(self, job):
        """Open a progress window once a file job has run for POPUP_DELAY seconds; close it when the job ends, but not
        before it has been up for POPUP_MIN_VISIBLE seconds (until then it shows the finished state)."""
        now = time.monotonic()
        watched = job if job and not job.quiet and not job.inline else None
        popup = self._popup
        if popup and popup.job is not watched:                    # the popup's job has ended
            popup.finish()
            if now - popup.opened >= POPUP_MIN_VISIBLE:
                popup.close_window()
        if watched is not self._popup_job:                        # another job, or none: start the clock again
            self._popup_job, self._popup_since, self._popup_dismissed = watched, now, False
        if watched is None or self._popup_dismissed:
            return
        if self._popup is None and now - self._popup_since >= POPUP_DELAY:
            self._popup = TransferPopup(self, watched)
        if self._popup and self._popup.job is watched:
            self._popup.update()

def run():
    GLib.set_prgname("mpexplorer")              # becomes the Wayland app_id: the taskbar finds mpexplorer.desktop
    GLib.set_application_name("MicroPython Explorer")
    Gtk.Window.set_default_icon_name("mpexplorer")
    App()
    Gtk.main()
