"""GTK 3 main window: two panes, toolbar, job progress."""
import os
import posixpath
import queue
import shutil
import threading
import time
import weakref

from .. import __version__
from ..backends import CHUNK, DeviceBackend, Entry, LocalBackend, human
from ..clipboard import Clipboard, copied_dir
from ..connection import Connection
from ..errors import BackendError, Cancelled
from ..jobs import JobRunner
from .. import circuitpython, paths
from ..badgescreen import BadgeScreen
from ..openwith import (SYSTEM_OPEN, EditManager, app_template, handler_for, looks_executable, parse_handler,
                        remember_handler)
from ..ops import ConflictPolicy, copy_tree, delete_items, is_within, move_item, total_size, unique_name
from .. import boardinfo, meminfo
from ..ports import board_id, device_of, list_boards, short_model
from ..remember import (chip_key, device_start, host_start, move_folder_to_chip, port_choice, remember_board_name,
                        remember_board_rts, remember_folder, remember_port, remember_source, source_choice)
from ..settings import Settings, segment
from ..transport import RawREPL
from ..watchdog import Watchdog
from .common import (ICON, Gdk, GdkPixbuf, Gio, GLib, Gtk, ask_yes_no, button, error, fmt_time, icon_button, info,
                     prompt, set_icon)
from .console import ConsoleWindow
from .dialogs import (OutputWindow, SyncDialog, TransferPopup, ask_conflict, ask_edit_conflict, ask_open_with,
                      ask_sync_target)
from .editor import Editor
from .firmware import BackupDialog, EraseDialog, FirmwareDialog
from .libraries import LibrariesDialog
from .viewer import Viewer
from .pane import HOST, Pane
from .prefs import PreferencesDialog

MAX_EDIT_BYTES = 256 * 1024
MAX_IMAGE_BYTES = 2 * 1024 * 1024


def cache_dir():
    return os.path.join(paths.cache_dir(), "open")


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
        self._popup, self._popup_job, self._popup_since, self._popup_dismissed = None, None, 0.0, False
        self._status_timer = None
        self.clipboard = Clipboard()
        self.local = LocalBackend()
        self.edits = EditManager(self.runner, cache_dir(), self._edit_say, self._edit_conflict, self._refresh_all)
        self.edits.set_paused(not self.settings.get("open_with.auto_upload"))
        self.conns = {}                             # pane -> the board connected in it
        self.windows = []                           # open editors and the console
        self.show_hidden = self.settings.get("general.show_hidden")
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
        self.scan_ports()
        left = source_choice(self.settings, "left", [HOST] + self._ports) or HOST
        self.left.fill_sources(self._ports, left, self._kinds)
        right = source_choice(self.settings, "right", [HOST] + self._ports)
        if right:
            self.right.fill_sources(self._ports, right, self._kinds)
        if right == HOST:
            self._show_host(self.right)
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
        add(d, "Board memory...", self.board_memory)
        add(d, "Set board time now", self.set_board_clock)
        add(d, "Name this board...", self.name_board)
        add(d, "Soft reset", self.soft_reset)
        t = menu("Tools")
        add(t, "Sync folders...", self.sync_folders)
        add(t, "Install firmware...", self.install_firmware)
        add(t, "Install libraries...", self.install_libraries)
        add(t, "Back up or restore flash...", lambda: BackupDialog(self))
        add(t, "Erase flash...", lambda: EraseDialog(self))
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
            for pane in self.conns:
                pane.refresh()
        elif key == "open_with.auto_upload":
            self.edits.set_paused(not value)
            self._sync_pause_button()

    def preferences(self):
        dlg = PreferencesDialog(self)
        dlg.run()
        dlg.destroy()

    def install_firmware(self):
        FirmwareDialog(self)

    def install_libraries(self):
        if not self.conn:
            info(self, "Not connected", "Connect to a device first.")
            return
        pane = self._need_backend(self._pane_of(self.conn))
        if pane:
            LibrariesDialog(self, pane.backend, self.conn.label or self.conn.port)

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
        conn = self.conns.get(pane)
        remember_folder(self.settings, bool(conn), pane.cwd, conn.board if conn else None)
        if conn and conn.badge:
            self.runner.submit(lambda prog: conn.badge.show(folder=pane.cwd), label="Badge screen", quiet=True)

    def sort_changed(self, pane):
        which = "device" if pane.title == "Device" else "host"
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
        conn.pack_start(icon_button("view-refresh", "Scan for serial ports", self.scan_ports, "Scan"), False, False, 0)
        self.send_time_btn = icon_button(("appointment-soon", "appointment-new", "x-office-calendar"),
                                         "Set the board's clock to this computer's time (UTC)",
                                         self.set_board_clock, "Send time")
        conn.pack_start(self.send_time_btn, False, False, 0)
        files = Gtk.Box(spacing=4)
        for icon, tip, cb, label, pad in (
                ("go-next", "Copy selected to the right pane", lambda: self.transfer(self.left, self.right),
                 "Copy to right", 0),
                ("go-previous", "Copy selected to the left pane", lambda: self.transfer(self.right, self.left),
                 "Copy to left", 4),
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
        return bar

    def _build_statusbar(self):
        """A centred progress bar (60% of the width) with the status text under it, lined up with the bar's left edge;
        Cancel beside the bar's right end, as tall as the bar and the text together."""
        grid = Gtk.Grid(column_homogeneous=True, row_spacing=2, column_spacing=6, margin_start=4, margin_end=4)
        self.pause_btn = Gtk.ToggleButton(label="Pause", halign=Gtk.Align.END, valign=Gtk.Align.FILL,
                                          no_show_all=True, tooltip_text="Pause uploads: hold saves made in "
                                          "an external editor; they are sent when you release it")
        self.pause_btn.connect("toggled", self._pause_toggled)
        side = Gtk.Box()                                                   # always shown, so the column keeps its width
        side.pack_start(self.pause_btn, True, True, 0)
        grid.attach(side, 0, 0, 1, 2)                                      # equal side columns: 20% | 60% | 20%
        self.bar = Gtk.ProgressBar(valign=Gtk.Align.CENTER, hexpand=True)
        grid.attach(self.bar, 1, 0, 3, 1)
        self.status = Gtk.Label(label="Ready", xalign=0, ellipsize=3, hexpand=True)       # 3 = end
        grid.attach(self.status, 1, 1, 3, 1)
        cancel = button("Cancel", self.runner.cancel_all, "Cancel the running job")
        cancel.set_halign(Gtk.Align.START)
        cancel.set_valign(Gtk.Align.FILL)       # stretches to the height of the bar and the text beside it
        grid.attach(cancel, 4, 0, 1, 2)
        return grid

    def _pause_toggled(self, btn):
        paused = btn.get_active()
        btn.set_label("Resume" if paused else "Pause")
        if paused == self.edits.paused:
            return
        self.edits.set_paused(paused)
        self.set_status("Uploads paused: saves wait until you release the button" if paused
                        else "Uploads resumed")

    def _sync_pause_button(self):
        """Shown while edit sessions are open (or uploads are held); mirrors the manager's state."""
        show = bool(self.edits.sessions) or self.edits.paused
        if self.pause_btn.get_visible() != show:
            self.pause_btn.set_visible(show)
        if self.pause_btn.get_active() != self.edits.paused:
            self.pause_btn.set_active(self.edits.paused)

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

    def set_device_paused(self, paused, conn):
        """Terminal mode owns the serial line, so the device pane is frozen until it is released."""
        pane = self._pane_of(conn)
        if pane and pane.backend:
            pane.set_paused(paused)

    # -- connection --------------------------------------------------------
    def scan_ports(self):
        entries = list_boards(self.settings)
        self._ports = [e.text for e in entries]
        self._kinds = {e.text: e.kind for e in entries}            # what each port is likely running: the badges
        choice = port_choice(self.settings, self._ports)
        first = self._ports[choice] if choice is not None and not self.right.source() else None
        self.right.fill_sources(self._ports, first, self._kinds)
        self.left.fill_sources(self._ports, None, self._kinds)

    def source_changed(self, pane):
        """Choosing Host takes effect at once. A port only takes effect with the pane's Connect button."""
        if pane.source() == HOST or pane.source() in self._ports:
            remember_source(self.settings, self._side(pane), pane.source())
        if pane.source() == HOST:
            self.disconnect(pane, after=lambda: self._show_host(pane))

    def _side(self, pane):
        return "left" if pane is self.left else "right"

    def _show_host(self, pane):
        pane.detail = ""
        pane.set_location(self.local, host_start(self.settings, self.local.root))

    def toggle_connection(self, pane=None):
        pane = pane or self.right
        if pane in self.conns:
            self.disconnect(pane)
        else:
            self.connect_device(pane)

    def connect_device(self, pane=None):
        if not pane:
            pane = self.active if device_of(self.active.source()) not in ("", HOST) else self.right
        port = device_of(pane.source())
        if not port or port == HOST:
            info(self, "No port", "Pick a board in this pane's list first (Scan).")
            return
        if any(conn.port == port and where is not pane for where, conn in self.conns.items()):
            info(self, "Already connected", "%s is connected in the other pane. A board can be open in one pane "
                 "at a time." % port)
            return
        self.disconnect(pane, after=lambda: self._connect_to(port, pane))

    def _connect_to(self, port, pane):
        self.set_status("Connecting to %s ..." % port)
        self._connect_state("connecting", pane)
        pane.connect_btn.set_sensitive(False)

        board = board_id(port)

        def work(prog):
            repl, rts = RawREPL.open_board(port, rts=self.settings.get("boards.rts").get(segment(board), False))
            try:
                firmware, version = circuitpython.detect(repl)
                if firmware == "circuitpython":         # files go through its CIRCUITPY drive, the rest over serial
                    dev = circuitpython.DriveBackend(circuitpython.find_drive(board.rsplit(":", 1)[-1]), repl)
                else:
                    dev = DeviceBackend(repl)
                try:
                    machine = repl.exec("import os;print(os.uname().machine)").decode().strip()
                except Exception:
                    machine = ""            # a build without os.uname: the port list keeps its generic name
                key = chip_key(repl) or board       # the USB id if its firmware cannot give a chip id
                return dev, device_start(self.settings, dev, key, board), machine, rts, key, firmware, version
            except BaseException:
                repl.close()
                raise

        def done(res, exc):
            pane.connect_btn.set_sensitive(True)
            if exc:
                self._connect_state("connect", pane)
                self.set_status("Connection failed")
                error(self, "Connection failed", str(exc))
                return
            move_folder_to_chip(self.settings, board, res[4])
            self.connected(res[0], res[1], port, res[4], res[2], rts=res[3], usb=board, pane=pane,
                           firmware=(res[5], res[6]))
        self.runner.submit(work, done, "Connecting", quiet=True)

    def connected(self, dev, start, port, board, machine="", rts=False, usb=None, pane=None, firmware=("", "")):
        """The connection is up: show the board, and remember the port, its model (and later its folders)."""
        pane = pane or self.right
        conn = self.conns[pane] = Connection.open_on(dev, port, board)
        self.set_active(pane)
        remember_board_name(self.settings, usb or board, machine)    # these are needed before a board can be read,
        remember_board_rts(self.settings, usb or board, rts)         # so they stay under its USB id
        fw_kind = firmware[0] if firmware[0] in boardinfo.FIRMWARE_NAMES else None
        if fw_kind:                  # learned: the badge no longer depends on the USB guess or the port it is on
            serial = (usb or "").rsplit(":", 1)[-1]
            if boardinfo.unique_usb_serial(serial):
                boardinfo.remember_firmware(self.settings, usb, fw_kind)
            boardinfo.remember_firmware(self.settings, board, fw_kind)
        boardinfo.remember_chip(self.settings, usb, board)
        conn.usb = usb or ""
        conn.model = short_model(machine) if machine else ""
        conn.firmware = "%s %s" % (boardinfo.FIRMWARE_NAMES[fw_kind], firmware[1]) if fw_kind else ""
        pane.detail = conn.label = self._label_of(conn)
        remember_port(self.settings, port)
        remember_source(self.settings, self._side(pane), port)
        pane.set_location(dev, start)
        pane.set_kind(fw_kind)
        self._connect_state("disconnect", pane)
        pane.connect_btn.set_sensitive(True)
        shown = boardinfo.quoted(boardinfo.nickname_of(self.settings, board)) or port
        self.set_status("Connected to %s" % shown)
        if self.settings.get("general.set_board_clock"):
            self._set_clock(dev, "Connected to %s; " % shown)
        screen = self.settings.get("general.badge_screen") and firmware[0] != "circuitpython"
        conn.badge = BadgeScreen(dev.repl) if screen else None
        if conn.badge:
            self.runner.submit(lambda prog: conn.badge.show(folder=start), label="Badge screen", quiet=True)

    def _label_of(self, conn):
        """'/dev/ttyACM4  "Bench CP"  Pico 2 (rp2350a)  CircuitPython 10.3.1'."""
        return boardinfo.connection_label(conn.port, boardinfo.nickname_of(self.settings, conn.board), conn.model,
                                          conn.firmware)

    def name_board(self, pane=None):
        """Device > Name this board (and the pencil button): a nickname for the connected board, kept on this PC."""
        pane = pane if pane in self.conns else (self._pane_of(self.conn) if self.conn else None)
        conn = self.conns.get(pane)
        if not conn:
            info(self, "Name this board", "Connect to the board first: its chip id is read when it connects, and "
                 "that is what keeps the name with the right board.")
            return
        warning = boardinfo.shared_usb_warning(conn.board)
        old = boardinfo.nickname_of(self.settings, conn.board)
        text = prompt(self, "Name this board", "Name for %s (up to %d characters; empty removes the name):%s"
                      % (conn.port, boardinfo.MAX_NICKNAME, "\n\n" + warning if warning else ""), old, allow_empty=True)
        if text is None:
            return
        clean = boardinfo.clean_nickname(text)
        taken = clean and clean != old and boardinfo.other_boards_named(self.settings, clean, conn.board)
        if taken and not ask_yes_no(self, "Name this board",
                                    'Another board is already called "%s". Use it for this one as well?' % clean):
            return
        boardinfo.set_nickname(self.settings, conn.board, clean)
        self.apply_names()
        self.set_status('Named %s "%s"' % (conn.port, clean) if clean else "Removed the name of %s" % conn.port)

    def apply_names(self):
        """A nickname changed: rebuild every place that shows it (labels, console titles, the port lists)."""
        for pane, conn in self.conns.items():
            pane.detail = conn.label = self._label_of(conn)
            pane._set_label(pane._caption(" (terminal active - paused)" if pane.paused else ""))
            if conn.console:
                conn.console.base_title = "REPL console - %s" % (conn.label or conn.port)
                conn.console.set_title(conn.console.base_title)
        chosen = {pane: pane.source() for pane in self.panes}
        self.scan_ports()
        for pane, old in chosen.items():
            device = device_of(old) if old and old != HOST else ""
            new = next((t for t in self._ports if device_of(t) == device), None) if device else None
            if new and new != old:               # the same port, now listed with its new name
                pane.fill_sources(self._ports, new, self._kinds)
                remember_source(self.settings, self._side(pane), new)

    # What the rest of the window asks about "the" board: the active pane's, else the only one connected
    @property
    def conn(self):
        return self.conns.get(self.active) or next(iter(self.conns.values()), None)

    @conn.setter
    def conn(self, value):
        self.conns[self.right] = value

    def _pane_of(self, conn):
        return next((pane for pane, c in self.conns.items() if c is conn), None)

    @property
    def console(self):
        """The console of the active pane's board."""
        return self.conn.console if self.conn else None

    @property
    def port_box(self):
        return self.right.source_box

    @property
    def connect_btn(self):
        return self.right.connect_btn

    @property
    def device(self):
        return self.conn.device if self.conn else None

    @property
    def board(self):
        return self.conn.board if self.conn else None

    @property
    def session(self):
        return self.conn.session if self.conn else None

    @property
    def badge(self):
        return self.conn.badge if self.conn else None

    def _connect_state(self, state, pane=None):
        """The Connect button shows what a click would do: plug in, unplug, or wait."""
        icon, tip, label = {"connect": ("network-offline", "Connect to the board", "Connect"),
                            "connecting": ("emblem-synchronizing", "Connecting...", "Connecting..."),
                            "disconnect": ("network-wired", "Disconnect from the board", "Disconnect")}[state]
        set_icon((pane or self.right).connect_btn, icon, tip, label)

    def _badge_draw(self, **kw):
        """Queue a redraw of the badge's status screen (a no-op without one)."""
        badge = self.badge
        if badge:
            self.runner.submit(lambda prog: badge.show(**kw), label="Badge screen", quiet=True)

    def _badge_hook(self, job, phase, exc):
        """Worker thread, around every job: say what the board is doing (quiet jobs are not worth a redraw)."""
        if job.quiet:
            return
        conns = list(self.conns.values())
        if job.devices is not None:
            conns = [c for c in conns if any(c.device is d for d in job.devices)]
        elif len(conns) > 1:                # it does not say which board it uses: tell no screen rather than guess
            conns = []
        for conn in conns:
            if not conn.badge:
                continue
            if phase == "start":
                conn.badge.show(job.status, kind="busy")
            else:
                conn.badge.show("Cancelled" if isinstance(exc, Cancelled) else "Failed" if exc else "Done")

    def disconnect(self, pane=None, after=None, keep_copied=True):
        """Close the board in a pane (by default the active one, else the only one). after runs once it is closed or
        queued to close. keep_copied: files copied from it are downloaded first, to paste on the next board."""
        pane = pane or self._pane_of(self.conn)
        conn = self.conns.get(pane)
        if not conn and after:
            after()
        if conn:
            del self.conns[pane]
            dev, board = conn.device, conn.board
            self._release_edits(dev, board)
            if conn.console:
                conn.console.close()
            pane.clear()
            if pane is self.left:
                self._show_host(pane)       # nothing connected here: the left pane goes back to the host
                pane.fill_sources(self._ports, HOST, self._kinds)
                remember_source(self.settings, "left", HOST)
            reset = self.settings.get("general.reset_on_disconnect")
            if conn.badge and not reset:
                self.runner.submit(lambda prog: conn.badge.show(status="press reset button", kind="gone"),
                                   label="Badge screen", quiet=True)
            self._connect_state("connect", pane)
            pane.connect_btn.set_sensitive(True)
            self.set_status("Disconnected")
            if keep_copied and self.clipboard.holds(dev):
                self._keep_copied(dev, reset, after)
                return
            self.runner.submit(lambda prog: dev.close(reset), label="Disconnecting", quiet=True)
            if after:
                after()

    def _keep_copied(self, dev, reset, after):
        """One job, so that Cancel cannot lose the close: it empties the queue. What follows starts when it ends."""
        items = self.clipboard.items

        def work(prog):
            try:
                policy = ConflictPolicy(self.ask_conflict)
                return self.clipboard.keep_copies(dev, self.local, copied_dir(), prog, policy)
            finally:
                dev.close(reset)

        def done(res, exc):
            if isinstance(exc, Cancelled) or exc:
                if self.clipboard.items is items:
                    self.clipboard.clear()
                self.set_status("Copied files not kept")
                if not isinstance(exc, Cancelled):
                    error(self, "Could not keep the copied files", str(exc))
            elif self.clipboard.items is items:
                self.clipboard.use_copies(*res)
                self.set_status("Copied files kept: connect to another board and paste")
            else:
                shutil.rmtree(res[1], ignore_errors=True)   # something else was copied meanwhile
            if after:
                after()
        self.runner.submit(work, done, "Keeping the copied files ...", status="Downloading the copied files",
                           devices=(dev,))

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
        self.clipboard.clear()
        self.runner.cancel_all()
        for _ in range(20):                         # an interrupted script needs a moment to let go of the serial line
            if not self.runner.busy:
                break
            time.sleep(0.1)
        for conn in list(self.conns.values()):
            if not self.runner.busy:
                try:
                    self.edits.flush(conn.device, conn.board)     # saves still waiting go up before the board is closed
                    self.edits.close_board(conn.board)
                except Exception:
                    pass
            try:
                reset = self.settings.get("general.reset_on_disconnect")
                if conn.badge and not reset:
                    conn.badge.show("press reset button", kind="gone")
                conn.badge = None
                conn.device.close(reset)
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
            live = [conn.device for conn in self.conns.values()]
            if any(isinstance(be, DeviceBackend) and be not in live for be, _path in self.clipboard.items):
                self.clipboard.clear()      # that board's connection is closed, so these can no longer be read
                info(self, "Copied from another board",
                     "Those files were copied from a board that is no longer connected, and could not be kept.\n\n"
                     "Connect to that board and copy them again.")
                return
            move = self.clipboard.mode == "cut"
            self._start_copy(self.clipboard.items, pane.backend, pane.cwd, move)
            if move:
                self.clipboard.clear()

    def transfer(self, src, dst, folder=None):
        if not (self._need_backend(src) and self._need_backend(dst)):
            return False
        items = [(src.backend, p) for _n, p in src.selected()]
        if items:
            self._start_copy(items, dst.backend, folder or dst.cwd, move=False)
        return False

    def dropped(self, src, dst, folder):
        """A drag ended. Across panes it copies, into the folder under it if there is one. Within a pane it moves
        into that folder, as a file manager does; with no folder under it there is nowhere to move to."""
        if src is not dst:
            return self.transfer(src, dst, folder)
        if not self._need_backend(src):
            return False
        items = [(src.backend, p) for _n, p in src.selected() if p != folder]
        if not folder:
            self.set_status("Drop on a folder to move into it")
        elif items:
            self._start_copy(items, src.backend, folder, move=True)
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
        self.runner.submit(work, done, verb + " ...", status=status, devices=[be for be, _p in items] + [dst_be])

    def _refresh_all(self):
        for p in self.panes:
            p.refresh()

    # -- file operations ---------------------------------------------------
    def _simple_job(self, fn, label, error_title, device=None):
        def done(_res, exc):
            self._refresh_all()
            if isinstance(exc, Cancelled):
                self.set_status("Cancelled")
            elif exc:
                self.set_status("Failed")
                error(self, error_title, str(exc))
            else:
                self.set_status("Done")
        self.runner.submit(fn, done, label, devices=(device,) if device else None)

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
        self._simple_job(work, "Deleting ...", "Delete failed", be)

    def new_folder(self):
        pane = self._need_backend()
        name = pane and prompt(self, "New folder", "Folder name:")
        if name:
            be, path = pane.backend, pane.backend.join(pane.cwd, name)
            self._simple_job(lambda prog: be.mkdir(path), "Creating folder", "Cannot create folder", be)

    def new_file(self):
        pane = self._need_backend()
        name = pane and prompt(self, "New file", "File name:")
        if name:
            be, path = pane.backend, pane.backend.join(pane.cwd, name)
            self._simple_job(lambda prog: be.open_write(path).close(), "Creating file", "Cannot create file", be)

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
        self._simple_job(work, "Renaming", "Rename failed", be)

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
        if getattr(pane.backend, "in_place", False):        # a host file, or a CircuitPython board's drive
            err = self.edits.open_host_file(path, template)
            if err:
                error(self, "Cannot open file", err)
        elif self.device:
            self.edits.edit_device_file(self.device, self.board, path, template)

    def open_default(self, pane, path, size):
        """Double-click / Enter: the program saved for this file type, else the built-in editor."""
        self.open_with(pane, path, size, handler_for(self.settings, posixpath.basename(path)))

    def _confirm_system_open(self, pane, path):
        """The system default may run a program instead of showing it: ask first for an executable-looking file."""
        name = posixpath.basename(path)
        mode = None
        if getattr(pane.backend, "in_place", False):
            try:
                mode = os.stat(path).st_mode
            except OSError:
                pass
        if not looks_executable(name, mode):
            return True
        return ask_yes_no(self, "Open with the system default?",
                          "%s looks like a program. The system default application may run it instead of "
                          "showing it.\n\nOpen it anyway?" % name)

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
        if kind == "builtin" and value == "system":
            if not self._confirm_system_open(pane, path):
                return
            kind, value = "command", SYSTEM_OPEN
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
        pane = self._pane_of(self.conn) or self.right
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
        conn = self.conn
        if not conn:
            info(self, "Not connected", "Connect to a device first.")
        elif conn.console:
            conn.console.present()
        else:
            conn.console = ConsoleWindow(self, conn)

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
        if self.device and self._need_backend(self._pane_of(self.conn)):
            dev = self.device
            self.runner.submit(lambda prog: dev.free_space("/"),
                               lambda res, exc: info(self, "Free space",
                                                     str(exc) if exc else "Device: %s free" % human(res)),
                               "Checking space", quiet=True)

    def board_memory(self):
        """Device > Board memory: flash chip, file system and RAM of the connected board (read-only)."""
        if not (self.device and self._need_backend(self._pane_of(self.conn))):
            return
        dev = self.device
        self.runner.submit(lambda prog: meminfo.describe(dev.memory()),
                           lambda res, exc: info(self, "Board memory", str(exc) if exc else res),
                           "Reading the board's memory", quiet=True)

    def set_board_clock(self):
        if self.device and self._need_backend(self._pane_of(self.conn)):
            self._set_clock(self.device)

    def _set_clock(self, dev, prefix=""):
        def done(res, exc):
            self.set_status(prefix + ("could not set the board clock: %s" % exc if exc
                                      else "board clock set to %04d-%02d-%02d %02d:%02d:%02d UTC" % tuple(res)))
        self.runner.submit(lambda prog: dev.set_clock(), done, "Setting the clock", quiet=True)

    def soft_reset(self):
        if not (self.device and self._need_backend(self._pane_of(self.conn))):
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
            self._sync_pause_button()
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
