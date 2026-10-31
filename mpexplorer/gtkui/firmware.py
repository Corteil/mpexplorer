"""Tools > Install firmware: put MicroPython on a board in its bootloader. The logic is in ..firmware."""
import os
import threading

from .. import firmware as fw
from ..backends import human
from ..errors import Cancelled
from ..firmware_catalog import Catalog, default_build, saved_text
from .common import GLib, Gtk, ask_yes_no, error

STEPS = ("Pico or Pico 2: hold the BOOTSEL button while plugging it in.\n"
         "Tildagon: power it off from its menu, then hold boop while plugging USB into USB in. Keep holding for a "
         "few seconds.\n"
         "The board appears here, and any serial connection to it ends.")
SAME_CHIP = ("The bootloader names the chip, not the board: a Pico and a Pico W read the same but need "
             "different firmware. Choose your exact board.")


class FirmwareDialog(Gtk.Dialog):
    def __init__(self, app):
        super().__init__(title="Install firmware", transient_for=app, modal=True)
        self.app, self.drives, self.info, self.running, self.result = app, [], None, False, None
        self.catalog = Catalog()
        self.boards, self.builds, self.shown_builds, self.list_chip, self.list_error = [], [], [], None, ""
        self.load_id, self.closed, self.outcome, self.tool_problem = 0, False, "", ""
        self.cancel_event = threading.Event()
        self.set_default_size(560, -1)
        box = self.get_content_area()
        box.set_spacing(8)
        box.set_border_width(12)
        box.pack_start(Gtk.Label(label=STEPS, xalign=0, wrap=True), False, False, 0)

        grid = Gtk.Grid(column_spacing=10, row_spacing=6)
        grid.attach(Gtk.Label(label="Found", xalign=1), 0, 0, 1, 1)
        self.board_box = Gtk.ComboBoxText(hexpand=True)
        self.board_box.connect("changed", lambda _b: self._drive_changed())
        grid.attach(self.board_box, 1, 0, 1, 1)

        self.use_site = Gtk.RadioButton(label="Download it")
        self.use_file = Gtk.RadioButton(label="Use a file on this computer", group=self.use_site)
        self.use_site.connect("toggled", lambda _b: self._source_changed())
        grid.attach(self.use_site, 1, 1, 1, 1)
        self.site_rows = Gtk.Grid(column_spacing=10, row_spacing=6)
        self.site_rows.attach(Gtk.Label(label="Board", xalign=1), 0, 0, 1, 1)
        self.product_box = Gtk.ComboBoxText(hexpand=True)
        self.product_box.connect("changed", lambda _b: self._product_changed())
        self.site_rows.attach(self.product_box, 1, 0, 1, 1)
        self.site_rows.attach(Gtk.Label(label="Version", xalign=1), 0, 1, 1, 1)
        self.build_box = Gtk.ComboBoxText(hexpand=True)
        self.build_box.connect("changed", lambda _b: self._check())
        self.site_rows.attach(self.build_box, 1, 1, 1, 1)
        self.all_builds = Gtk.CheckButton(label="Show preview builds and other variants")
        self.all_builds.connect("toggled", lambda _b: self._fill_builds())
        self.site_rows.attach(self.all_builds, 1, 2, 1, 1)
        grid.attach(self.site_rows, 1, 2, 1, 1)

        grid.attach(self.use_file, 1, 3, 1, 1)
        self.chooser = Gtk.FileChooserButton(title="Choose a firmware file", action=Gtk.FileChooserAction.OPEN)
        uf2 = Gtk.FileFilter()
        uf2.set_name("Firmware (*.uf2, *.bin)")
        uf2.add_pattern("*.uf2")
        uf2.add_pattern("*.UF2")
        uf2.add_pattern("*.bin")
        self.chooser.add_filter(uf2)
        self.chooser.connect("file-set", lambda _c: self._check())
        grid.attach(self.chooser, 1, 4, 1, 1)
        box.pack_start(grid, False, False, 0)

        box.pack_start(Gtk.Label(label=SAME_CHIP, xalign=0, wrap=True, sensitive=False), False, False, 0)
        self.note = Gtk.Label(xalign=0, wrap=True, selectable=True)
        box.pack_start(self.note, False, False, 0)
        self.bar = Gtk.ProgressBar(no_show_all=True)
        box.pack_start(self.bar, False, False, 0)

        self.cancel_btn = self.add_button("Cancel", Gtk.ResponseType.NONE)
        self.connect_btn = self.add_button("Connect", Gtk.ResponseType.APPLY)
        self.install_btn = self.add_button("Install", Gtk.ResponseType.OK)
        self.close_btn = self.add_button("Close", Gtk.ResponseType.CLOSE)
        for b in (self.cancel_btn, self.connect_btn):
            b.set_no_show_all(True)
            b.hide()                                                # add_button has already shown them
        self.connect("response", self._response)
        self.connect("delete-event", lambda *_a: self.running)      # the copy cannot be left unwatched
        self.show_all()
        self.scan()
        self._drive_changed()
        self._source_changed()
        self.timer = GLib.timeout_add_seconds(1, self._tick)
        self.connect("destroy", self._destroyed)

    def _destroyed(self, _dlg):
        self.closed = True
        GLib.source_remove(self.timer)

    def _in_background(self, work, done):
        """Network reads must not stall the window; a result that arrives after a newer request is dropped."""
        self.load_id += 1
        token = self.load_id

        def finish(res, exc):
            if not self.closed and token == self.load_id:
                done(res, exc)

        def run():
            try:
                res, exc = work(), None
            except fw.FirmwareError as e:
                res, exc = None, e
            GLib.idle_add(finish, res, exc)
        threading.Thread(target=run, daemon=True).start()

    # -- what is plugged in ------------------------------------------------
    def scan(self):
        """Keep the list current, so a board shows up as soon as it is plugged in."""
        found = fw.detect_all()
        if [d.path for d in found] == [d.path for d in self.drives]:
            return
        chosen = self.drive()
        self.drives = found
        self.board_box.remove_all()
        for d in found:
            name = fw.CHIP_NAMES.get(d.chip, "Unsupported (%s)" % d.board_id)
            self.board_box.append_text("%s at %s" % (name, d.path))
        paths = [d.path for d in found]
        if found:
            self.board_box.set_active(paths.index(chosen.path) if chosen and chosen.path in paths else 0)
        self._drive_changed()

    def _tick(self):
        if not self.running:
            self.scan()
        return GLib.SOURCE_CONTINUE

    def drive(self):
        i = self.board_box.get_active()
        return self.drives[i] if 0 <= i < len(self.drives) else None

    def _drive_changed(self):
        drive = self.drive()
        chip = drive.chip if drive else None
        if chip != self.list_chip:
            self.list_chip = chip
            self._load_boards(chip)
        self.tool_problem = ""
        if drive and drive.method == "esptool":
            try:
                fw.find_esptool()
            except fw.FirmwareError as e:
                self.tool_problem = str(e)
        self._check()

    # -- the site's lists ----------------------------------------------------
    def _load_boards(self, chip):
        self.boards, self.builds, self.list_error = [], [], ""
        self.product_box.remove_all()
        self.build_box.remove_all()
        if chip:
            self._in_background(lambda: self.catalog.boards(chip), self._boards_loaded)

    def _boards_loaded(self, res, exc):
        if exc:
            self.list_error = str(exc)
        else:
            self.boards, saved = res
            self.list_error = "Offline: using the list saved on %s." % saved_text(saved) if saved else ""
            for b in self.boards:
                self.product_box.append_text(b.label)      # none is preselected: only the owner knows which it is
        self._check()

    def product(self):
        i = self.product_box.get_active()
        return self.boards[i] if 0 <= i < len(self.boards) else None

    def _product_changed(self):
        self.outcome = ""
        self.builds = []
        self.build_box.remove_all()
        board = self.product()
        if board:
            self._in_background(lambda: self.catalog.builds(board.id), self._builds_loaded)
        self._check()

    def _builds_loaded(self, res, exc):
        if exc:
            self.list_error = str(exc)
        else:
            self.builds, saved = res
            self.list_error = "Offline: using the list saved on %s." % saved_text(saved) if saved else ""
        self._fill_builds()

    def _fill_builds(self):
        best = default_build(self.builds)
        everything = self.all_builds.get_active()
        self.shown_builds = [b for b in self.builds if everything or b is best]
        self.build_box.remove_all()
        for b in self.shown_builds:
            self.build_box.append_text(b.label + ("  (latest)" if b is best else ""))
        if self.shown_builds:
            self.build_box.set_active(self.shown_builds.index(best) if best else 0)
        self._check()

    def build(self):
        i = self.build_box.get_active()
        return self.shown_builds[i] if 0 <= i < len(self.shown_builds) else None

    def _source_changed(self):
        self.outcome = ""
        site = self.use_site.get_active()
        self.site_rows.set_sensitive(site)
        self.chooser.set_sensitive(not site)
        self._check()

    # -- the go/no-go ------------------------------------------------------
    def _check(self):
        """Install stays off until a board and a suitable image are both known."""
        if self.running or self.result:     # a list can arrive late: it must not touch a running or finished install
            return
        drive, self.info = self.drive(), None
        if self.use_site.get_active():
            ready = self._check_site(drive)
        else:
            ready = self._check_file(drive)
        if ready and self.tool_problem:
            self.note.set_text(self.tool_problem)
            ready = False
        self.install_btn.set_sensitive(ready)
        if self.outcome:
            self.note.set_text(self.outcome)

    def _check_site(self, drive):
        if not drive:
            text = "No board in its bootloader yet."
        elif not drive.chip:
            text = "No list for this bootloader: use a file instead."
        elif not self.boards:
            text = self.list_error or "Reading the board list ..."
        elif not self.product():
            text = "Choose your board. %s" % self.list_error
        elif not self.build():
            text = self.list_error or ("Reading the versions ..." if not self.builds else "No release for this board: "
                                       "tick the box below Version to see other builds.")
        else:
            self.note.set_text(("Will install %s. %s" % (self.build().filename, self.list_error)).strip())
            return True
        self.note.set_text(text.strip())
        return False

    def _check_file(self, drive):
        path = self.chooser.get_filename()
        if not drive:
            self.note.set_text("No board in its bootloader yet.")
        elif not path:
            self.note.set_text("Choose a firmware file.")
        else:
            try:
                self.info = fw.check_image(path, drive.chip)
                self.note.set_text("%s: %s firmware for the %s, %s\nSHA-256 %s" % (
                    os.path.basename(path), self.info.cpu, fw.CHIP_NAMES[drive.chip], human(self.info.size),
                    fw.sha256_of(path)))
                return True
            except (fw.FirmwareError, OSError) as e:
                self.note.set_text("Refused: %s" % e)
        return False

    def _response(self, _dlg, response):
        if response == Gtk.ResponseType.OK:
            self.install()
        elif response == Gtk.ResponseType.NONE:
            self.cancel_event.set()
            self.cancel_btn.set_sensitive(False)
        elif response == Gtk.ResponseType.APPLY:
            self._connect_new_port()
        elif not self.running:
            self.destroy()

    # -- the install -------------------------------------------------------
    def install(self):
        drive = self.drive()
        site = self.use_site.get_active()
        build, path = (self.build(), None) if site else (None, self.chooser.get_filename())
        what = "%s for the %s" % (build.label, self.product().label) if site else os.path.basename(path)
        also = "\n\nmpexplorer will disconnect from the current board first." if self.app.device else ""
        if not ask_yes_no(self, "Install firmware", "Install %s on the %s at %s?\n\nThis replaces the firmware on the "
                          "board in its bootloader. Files on the board usually stay, but copy off anything you "
                          "need first.%s" % (what, fw.CHIP_NAMES[drive.chip], drive.path, also)):
            return
        self.app.disconnect()
        self.running, self.outcome = True, ""
        self.cancel_event.clear()
        self.cancel_btn.set_sensitive(True)
        self.cancel_btn.show()
        self.bar.set_fraction(0)
        self.bar.show()
        for w in (self.install_btn, self.close_btn, self.board_box, self.chooser, self.site_rows, self.use_site,
                  self.use_file):
            w.set_sensitive(False)

        def progress(done, total):
            GLib.idle_add(self.bar.set_fraction, done / total)

        def stage(text):
            GLib.idle_add(self.note.set_text, text + " ...")

        def work():
            res, sha, exc = None, "", None
            try:
                image = self._fetch(build, path, drive, progress, stage)
                sha = fw.sha256_of(image)
                res = fw.install_and_verify(image, drive, on_progress=progress, on_stage=stage,
                                            cancelled=self.cancel_event.is_set)
            except Exception as e:
                exc = e
            GLib.idle_add(self._finished, res, sha, exc)
        threading.Thread(target=work, daemon=True).start()

    def _fetch(self, build, path, drive, progress, stage):
        if not build:
            return path
        stage("Getting %s" % build.filename)
        image = self.catalog.download(build, on_progress=progress, cancelled=self.cancel_event.is_set)
        try:
            fw.check_image(image, drive.chip)
        except fw.FirmwareError:
            self.catalog.forget(build)          # the next try downloads it again
            raise
        return image

    def _finished(self, res, sha, exc):
        self.running = False
        self.cancel_btn.hide()
        self.bar.hide()
        self.close_btn.set_sensitive(True)
        if res:
            self.result = res
            self.note.set_text("Installed: MicroPython %s on %s, now at %s.\nSHA-256 %s" % (
                res.release, res.machine, res.port, sha))
            self.connect_btn.show()
            return
        for w in (self.board_box, self.use_site, self.use_file):
            w.set_sensitive(True)
        self._source_changed()
        self.drives = []        # read them again: the board may have restarted or still be there
        self.board_box.remove_all()
        self.scan()
        self._check()
        if isinstance(exc, Cancelled):
            self.outcome = "Cancelled. The flash may be part written: install again before using the board."
        else:
            self.outcome = "Failed."
        self.note.set_text(self.outcome)
        if not isinstance(exc, Cancelled):
            error(self, "Install failed", str(exc))

    def _connect_new_port(self):
        port = self.result.port
        app = self.app
        self.destroy()
        app.scan_ports()
        app.port_box.get_child().set_text(port)
        app.connect_device()
