"""Main window: two panes, toolbar, job progress."""
import queue
import threading
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, scrolledtext, simpledialog, ttk

from .. import __version__
from ..backends import CHUNK, DeviceBackend, LocalBackend, human
from ..clipboard import Clipboard
from ..console import ConsoleSession
from ..errors import BackendError, Cancelled
from ..jobs import JobRunner
from ..ports import list_ports
from ..sync import apply_plan, build_plan
from ..ops import (ConflictPolicy, copy_tree, delete_tree, is_within, move_item, total_size,
                   unique_name)
from ..transport import DeviceError, RawREPL
from .console_window import ConsoleWindow
from . import wm
from .sync_dialog import SyncDialog
from .widgets import ConflictDialog, Editor, fmt_time, Pane

MAX_EDIT_BYTES = 256 * 1024
ICON = Path(__file__).resolve().parent.parent / "icon.png"


class App(tk.Tk):
    def __init__(self):
        # WM_CLASS is how the panel finds the launcher and icon (default "tk"/"Tk" matches nothing)
        super().__init__(baseName="mpexplorer", className="mpexplorer")
        self.title("MicroPython Explorer %s" % __version__)
        self.geometry("1180x680")
        try:
            self.icon = tk.PhotoImage(file=str(ICON))       # kept on self: Tk drops an image that gets collected
            self.iconphoto(True, self.icon)                 # True: every pop-up window gets it too
        except tk.TclError:
            pass                                            # missing icon is cosmetic only
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TButton", width=0, padding=(8, 3))     # clam forces a wide minimum; size to the label
        self.runner = JobRunner()
        self.clipboard = Clipboard()
        self.local = LocalBackend()
        self.device = None
        self.session = None
        self.console = None
        self.windows = []               # open editors and the console (AppWindow adds itself)
        self.show_hidden = tk.BooleanVar(value=False)
        self.show_hidden.trace_add("write", lambda *_: [p._render() for p in self.panes])
        self._asks = queue.Queue()

        self._build_menu()
        self._build_toolbar()
        split = ttk.PanedWindow(self, orient="horizontal")
        split.pack(fill="both", expand=True, padx=4, pady=2)
        self.left = Pane(split, self, "Host")
        self.right = Pane(split, self, "Device")
        split.add(self.left, weight=1)
        split.add(self.right, weight=1)
        self.panes = (self.left, self.right)
        self.active = self.left
        self._build_statusbar()

        self.left.set_location(self.local, self.local.root)
        self.right.clear()
        self.protocol("WM_DELETE_WINDOW", self._quit)
        self.update_idletasks()
        self.minsize(self.winfo_reqwidth(), 400)        # never narrower than the toolbar
        self.after(100, self._poll)

    # -- ui construction ---------------------------------------------------
    def _build_menu(self):
        m = tk.Menu(self)
        f = tk.Menu(m, tearoff=0)
        f.add_command(label="Connect", command=self.connect)
        f.add_command(label="Disconnect", command=self.disconnect)
        f.add_separator()
        f.add_command(label="New folder\tF7", command=self.new_folder)
        f.add_command(label="New file", command=self.new_file)
        f.add_separator()
        f.add_command(label="Quit", command=self._quit)
        m.add_cascade(label="File", menu=f)
        e = tk.Menu(m, tearoff=0)
        e.add_command(label="Copy\tCtrl+C", command=lambda: self.copy("copy"))
        e.add_command(label="Cut\tCtrl+X", command=lambda: self.copy("cut"))
        e.add_command(label="Paste\tCtrl+V", command=self.paste)
        e.add_command(label="Select all\tCtrl+A", command=lambda: self.active.select_all())
        e.add_separator()
        e.add_command(label="Rename\tF2", command=self.rename)
        e.add_command(label="Delete\tDel", command=self.delete)
        e.add_command(label="Properties", command=self.properties)
        m.add_cascade(label="Edit", menu=e)
        v = tk.Menu(m, tearoff=0)
        v.add_checkbutton(label="Show hidden files", variable=self.show_hidden)
        v.add_command(label="Refresh\tF5", command=lambda: [p.refresh() for p in self.panes])
        m.add_cascade(label="View", menu=v)
        d = tk.Menu(m, tearoff=0)
        d.add_command(label="REPL console...", command=self.open_console)
        d.add_command(label="Run selected script", command=self.run_script)
        d.add_command(label="Free space", command=self.free_space)
        d.add_command(label="Soft reset", command=self.soft_reset)
        m.add_cascade(label="Device", menu=d)
        t = tk.Menu(m, tearoff=0)
        t.add_command(label="Sync this folder to the other pane...", command=self.sync_folders)
        m.add_cascade(label="Tools", menu=t)
        wm = tk.Menu(m, tearoff=0, postcommand=lambda: self._fill_window_menu(wm))
        m.add_cascade(label="Window", menu=wm)
        self.config(menu=m)

    def _fill_window_menu(self, menu):
        menu.delete(0, "end")
        menu.add_command(label="Main window", command=lambda: self.show_window(self))
        for win in self.windows:
            menu.add_command(label=win.title(), command=lambda w=win: self.show_window(w))
        menu.add_separator()
        menu.add_command(label="Minimise all", command=self.minimise_all)
        menu.add_command(label="Restore all", command=self.restore_all)

    def show_window(self, win):
        """Bring a window back from the taskbar (or from behind others) and give it the keyboard."""
        # Tk can't tell a minimised window from a visible one here, and deiconify() does nothing for it under labwc
        if not wm.activate(win.winfo_id()):
            win.deiconify()
        win.lift()
        win.focus_force()

    def minimise_all(self):
        for win in self.windows + [self]:
            win.iconify()

    def restore_all(self):
        for win in [self] + self.windows:
            self.show_window(win)

    def _build_toolbar(self):
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=4, pady=4)
        ttk.Label(bar, text="Port").pack(side="left")
        self.port_var = tk.StringVar()
        self.port_box = ttk.Combobox(bar, textvariable=self.port_var, width=38)
        self.port_box.pack(side="left", padx=4)
        ttk.Button(bar, text="Scan", command=self.scan_ports).pack(side="left")
        self.connect_btn = ttk.Button(bar, text="Connect", command=self.toggle_connection)
        self.connect_btn.pack(side="left", padx=4)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Button(bar, text="Upload >>", command=lambda: self.transfer(self.left, self.right)).pack(side="left")
        ttk.Button(bar, text="<< Download",
                   command=lambda: self.transfer(self.right, self.left)).pack(side="left", padx=4)
        ttk.Button(bar, text="Copy", command=lambda: self.copy("copy")).pack(side="left", padx=(12, 0))
        ttk.Button(bar, text="Cut", command=lambda: self.copy("cut")).pack(side="left")
        ttk.Button(bar, text="Paste", command=self.paste).pack(side="left")
        ttk.Button(bar, text="New folder", command=self.new_folder).pack(side="left", padx=(12, 0))
        ttk.Button(bar, text="Rename", command=self.rename).pack(side="left")
        ttk.Button(bar, text="Delete", command=self.delete).pack(side="left")
        self.scan_ports()

    def _build_statusbar(self):
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=4, pady=3)
        self.status = ttk.Label(bar, text="Ready", anchor="w")
        self.status.pack(side="left", fill="x", expand=True)
        self.bar = ttk.Progressbar(bar, length=260, maximum=100)
        self.bar.pack(side="left", padx=6)
        self.cancel_btn = ttk.Button(bar, text="Cancel", command=self.runner.cancel_all)
        self.cancel_btn.pack(side="left")

    def context_menu(self):
        m = tk.Menu(self, tearoff=0)
        sel = self.active.selected()
        if len(sel) == 1:
            m.add_command(label="Open", command=self.active._open)
        other = self.other_pane(self.active)
        m.add_command(label="Send to %s" % other.title, command=lambda: self.transfer(self.active, other))
        m.add_separator()
        m.add_command(label="Copy", command=lambda: self.copy("copy"))
        m.add_command(label="Cut", command=lambda: self.copy("cut"))
        m.add_command(label="Paste", command=self.paste)
        m.add_separator()
        m.add_command(label="Rename", command=self.rename)
        m.add_command(label="Delete", command=self.delete)
        m.add_command(label="New folder", command=self.new_folder)
        m.add_command(label="New file", command=self.new_file)
        m.add_command(label="Properties", command=self.properties)
        return m

    # -- panes -------------------------------------------------------------
    def set_active(self, pane):
        self.active = pane

    def other_pane(self, pane):
        return self.right if pane is self.left else self.left

    def set_status(self, text):
        self.status.config(text=text)

    def _need_backend(self, pane=None):
        pane = pane or self.active
        if not pane.backend:
            messagebox.showinfo("Not connected", "Connect to a device first.")
            return None
        if pane.paused:
            messagebox.showinfo("Terminal active", "The console is in terminal mode. Switch it back to emulated "
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
        self.port_box["values"] = ports
        if ports and not self.port_var.get():
            self.port_var.set(ports[0])

    def toggle_connection(self):
        if self.device:
            self.disconnect()
        else:
            self.connect()

    def connect(self):
        port = self.port_var.get().split("  ")[0].strip()
        if not port:
            messagebox.showinfo("No port", "Pick a serial port first (Scan).")
            return
        self.disconnect()
        self.set_status("Connecting to %s ..." % port)
        self.connect_btn.config(text="Connecting...", state="disabled")

        def work(prog):
            repl = RawREPL.open(port)
            try:
                repl.enter_raw()
                return DeviceBackend(repl)
            except BaseException:
                repl.close()
                raise

        def done(dev, exc):
            if exc:
                self.connect_btn.config(text="Connect", state="normal")
                self.set_status("Connection failed")
                messagebox.showerror("Connection failed", str(exc))
                return
            self.device = dev
            self.session = ConsoleSession(dev.repl)
            self.right.set_location(dev, dev.root)
            self.connect_btn.config(text="Disconnect", state="normal")
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
            self.connect_btn.config(text="Connect", state="normal")
            self.set_status("Disconnected")

    def _quit(self):
        self.runner.cancel_all()
        if self.device:
            try:
                self.device.close()
            except Exception:
                pass
        self.destroy()

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
            return
        items = [(src.backend, p) for _n, p in src.selected()]
        if items:
            self._start_copy(items, dst.backend, dst.cwd, move=False)

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
                messagebox.showerror("%s failed" % verb, str(exc))
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
                messagebox.showerror(error_title, str(exc))
        self.runner.submit(fn, done, label)

    def delete(self):
        pane = self._need_backend()
        sel = pane.selected() if pane else []
        if not sel:
            return
        if not messagebox.askyesno("Delete", "Permanently delete %d item(s)?\n\n%s" % (
                len(sel), "\n".join(n for n, _p in sel[:8]))):
            return
        be = pane.backend

        def work(prog):
            for _n, p in sel:
                delete_tree(be, p, prog)
        self._simple_job(work, "Deleting ...", "Delete failed")

    def new_folder(self):
        pane = self._need_backend()
        name = pane and simpledialog.askstring("New folder", "Folder name:", parent=self)
        if name:
            be, path = pane.backend, pane.backend.join(pane.cwd, name)
            self._simple_job(lambda prog: be.mkdir(path), "Creating folder", "Cannot create folder")

    def new_file(self):
        pane = self._need_backend()
        name = pane and simpledialog.askstring("New file", "File name:", parent=self)
        if name:
            be, path = pane.backend, pane.backend.join(pane.cwd, name)
            self._simple_job(lambda prog: be.open_write(path).close(), "Creating file", "Cannot create file")

    def rename(self):
        pane = self._need_backend()
        sel = pane.selected() if pane else []
        if len(sel) != 1:
            return
        old_name, old = sel[0]
        new_name = simpledialog.askstring("Rename", "New name:", initialvalue=old_name, parent=self)
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
                messagebox.showerror("Properties", str(exc))
                return
            st, size, free = res
            messagebox.showinfo("Properties", "%s\n\nType: %s\nSize: %s (%d bytes)\nModified: %s\nFree space: %s" % (
                path, "Folder" if st.is_dir else "File", human(size), size, fmt_time(st.mtime), human(free)))
        self.runner.submit(work, done, "Reading properties", quiet=True)

    # -- editor and device tools ------------------------------------------
    def open_file(self, pane, path, size):
        if size > MAX_EDIT_BYTES:
            messagebox.showinfo("Too large", "Files over %s are not opened in the editor." % human(MAX_EDIT_BYTES))
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
                messagebox.showerror("Cannot open file", str(exc))
                return
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                messagebox.showinfo("Binary file", "%s is not UTF-8 text, so it is not opened." % path)
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
            messagebox.showinfo("Run", "Select one .py file on the device first.")
            return
        be, path = pane.backend, sel[0][1]

        def done(out, exc):
            win = tk.Toplevel(self)
            win.title("Output: " + path)
            win.transient(self)     # keeps it off the taskbar and stops clicks falling through to this window
            box = scrolledtext.ScrolledText(win, width=90, height=24, font="TkFixedFont")
            box.pack(fill="both", expand=True)
            box.insert("end", exc.traceback if isinstance(exc, DeviceError) else (str(exc) if exc else out))
        self.runner.submit(lambda prog: be.run(path), done, "Running " + path, quiet=True)

    def open_console(self):
        if not self.session:
            messagebox.showinfo("Not connected", "Connect to a device first.")
        elif self.console:
            self.show_window(self.console)
        else:
            self.console = ConsoleWindow(self, self.session)

    def sync_folders(self):
        src = self.active
        dst = self.other_pane(src)
        if not (self._need_backend(src) and self._need_backend(dst)):
            return
        s_be, s_dir, d_be, d_dir = src.backend, src.cwd, dst.backend, dst.cwd
        if s_be is d_be and (is_within(s_be, d_dir, s_dir) or is_within(s_be, s_dir, d_dir)):
            messagebox.showerror("Sync", "The two folders must be separate (neither inside the other).")
            return

        def done(plan, exc):
            if exc:
                if not isinstance(exc, Cancelled):
                    messagebox.showerror("Sync", str(exc))
                self.set_status("Sync cancelled" if isinstance(exc, Cancelled) else "Sync failed")
            elif not plan.actions:
                self.set_status("Folders are already in sync")
                messagebox.showinfo("Sync", "Nothing to do: the folders already match.")
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
                messagebox.showerror("Sync failed", str(exc))
            else:
                self.set_status("Sync complete")
        self.runner.submit(lambda prog: apply_plan(plan, prog, include_delete), done, "Syncing ...")

    def free_space(self):
        if self.device and self._need_backend(self.right):
            dev = self.device
            self.runner.submit(lambda prog: dev.free_space("/"),
                               lambda res, exc: messagebox.showinfo(
                                   "Free space", str(exc) if exc else "Device: %s free" % human(res)),
                               "Checking space", quiet=True)

    def soft_reset(self):
        if not (self.device and self._need_backend(self.right)):
            return
        if messagebox.askyesno("Soft reset", "Soft-reset the board? Any running program stops."):
            dev = self.device
            self.runner.submit(lambda prog: dev.soft_reset(), lambda r, e: self._refresh_all(),
                               "Resetting", quiet=True)

    # -- main loop tick ----------------------------------------------------
    def _poll(self):
        self.after(100, self._poll)         # reschedule first: dialogs below re-enter the loop
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
            dlg = ConflictDialog(self, req["name"])
            self.wait_window(dlg)
            req["result"] = dlg.result
            req["event"].set()
        job = self.runner.current
        if job and not job.quiet:
            p = job.prog
            self.bar["value"] = 100.0 * p.done / p.total if p.total else 0
            self.set_status("%s  %s / %s  (%s/s)" % (p.label, human(p.done), human(p.total), human(p.rate())))
        elif not job:
            self.bar["value"] = 0


def run():
    App().mainloop()
