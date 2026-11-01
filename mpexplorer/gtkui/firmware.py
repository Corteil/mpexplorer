"""Tools > Install firmware, Back up or restore flash, Erase flash. The logic is in ..firmware."""
import os
import threading
import time

from .. import firmware as fw
from ..backends import human
from ..errors import Cancelled
from ..firmware_catalog import NUKE_FILE, Build, Catalog, default_build, saved_text
from .common import GLib, Gtk, ask_yes_no, error

STEPS = ("To put a board in its bootloader:\n"
         "Pico or Pico 2: %s.\n"
         "Tildagon or 2026 badge: %s.\n"
         "2022 badge: %s.\n"
         "Another ESP32-S3 board: %s." % (fw.PICO_STEPS, fw.BADGES["TILDAGON"].steps, fw.BADGES["TIDAL"].steps,
                                          fw.ESP32_STEPS))
PICK_RP2 = ("Choose your exact board: the bootloader only shows the chip, and a Pico and a Pico W need different "
            "firmware.")
NO_DEVICE = "No device found yet.\n\n" + STEPS


class DeviceDialog(Gtk.Dialog):
    """What the three tools share: the device list and its details, and running a slow job with the window locked."""
    cancel_text = "Cancelled."
    fail_title = "It did not work"

    def __init__(self, app, title):
        super().__init__(title=title, transient_for=app, modal=True)
        self.app, self.drives, self.running, self.result = app, [], False, None
        self.catalog = Catalog()
        self.load_id, self.closed, self.outcome, self.tool_problem = 0, False, "", ""
        self.chip_reading, self.chip_error, self.chip_retry = None, "", 0
        self.running_info, self.info_reading, self.filling, self.prefer = {}, None, False, None
        self.cancel_event = threading.Event()
        self.set_default_size(560, -1)
        box = self.get_content_area()
        box.set_spacing(8)
        box.set_border_width(12)

        grid = Gtk.Grid(column_spacing=10, row_spacing=6)
        grid.attach(Gtk.Label(label="Device", xalign=1), 0, 0, 1, 1)
        self.board_box = Gtk.ComboBoxText(hexpand=True)
        self.board_box.connect("changed", lambda _b: self._picked())
        grid.attach(self.board_box, 1, 0, 1, 1)
        box.pack_start(grid, False, False, 0)
        frame = Gtk.Frame(label="Device details")
        self.details = Gtk.Label(xalign=0, wrap=True, selectable=True, margin=8)
        frame.add(self.details)
        box.pack_start(frame, False, False, 0)
        self.build_body(box)
        self.note = Gtk.Label(xalign=0, wrap=True, selectable=True)
        box.pack_start(self.note, False, False, 0)
        self.bar = Gtk.ProgressBar(no_show_all=True)
        box.pack_start(self.bar, False, False, 0)

        self.cancel_btn = self.add_button("Cancel", Gtk.ResponseType.NONE)
        self.cancel_btn.set_no_show_all(True)
        self.boot_btn = self.add_button("Start bootloader", Gtk.ResponseType.YES)
        self.boot_btn.set_no_show_all(True)
        self.boot_btn.set_tooltip_text("Restart this board in its bootloader, without its buttons. It stops what is "
                                       "running; its files stay.")
        self.add_action_buttons()
        self.close_btn = self.add_button("Close", Gtk.ResponseType.CLOSE)
        self.connect("response", self._response)
        self.connect("delete-event", lambda *_a: self.running)      # a job cannot be left unwatched
        self.show_all()
        self.cancel_btn.hide()
        self.boot_btn.hide()
        self.scan()
        self._picked()
        self.timer = GLib.timeout_add_seconds(1, self._tick)
        self.connect("destroy", self._destroyed)

    def build_body(self, box):
        pass

    def add_action_buttons(self):
        pass

    def locked_widgets(self):
        return (self.close_btn, self.board_box)

    def _destroyed(self, _dlg):
        self.closed = True
        GLib.source_remove(self.timer)

    def _in_background(self, work, done):
        """Slow reads must not stall the window; a result that arrives after a newer request is dropped."""
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
        paths = [d.path for d in found]
        self.running_info = {p: i for p, i in self.running_info.items() if p in paths}
        chosen = self.drive()
        self.drives = found
        self._fill_devices(self.prefer or (chosen.path if chosen else None))
        self.prefer = None
        self._picked()

    def _fill_devices(self, chosen):
        self.filling = True
        self.board_box.remove_all()
        for d in self.drives:
            self.board_box.append_text(fw.device_label(d, self.running_info.get(d.path)))
        paths = [d.path for d in self.drives]
        if paths:
            self.board_box.set_active(paths.index(chosen) if chosen in paths else 0)
        self.filling = False

    def _relabel(self):
        chosen = self.drive()
        self._fill_devices(chosen.path if chosen else None)

    def _tick(self):
        if not self.running:
            self.scan()
            if self.chip_error:                 # it may simply have not been in its bootloader yet
                self.chip_retry += 1
                if self.chip_retry >= 5:
                    self.chip_retry, self.chip_error = 0, ""
                    self._picked()
        return GLib.SOURCE_CONTINUE

    def drive(self):
        i = self.board_box.get_active()
        return self.drives[i] if 0 <= i < len(self.drives) else None

    def _picked(self):
        if self.filling:
            return
        drive = self.drive()
        self.tool_problem = ""
        if drive and drive.method == "esptool":
            try:
                fw.find_esptool()
                if not drive.esp_chip and not self.chip_error and self.chip_reading != drive.path:
                    self._read_chip(drive)
            except fw.FirmwareError as e:
                self.tool_problem = str(e)
        if drive and drive.method == "repl" and drive.path not in self.running_info \
                and self.info_reading != drive.path:
            self._read_running(drive)
        self._show_details(drive)
        self.device_changed(drive)
        self._check()

    def device_changed(self, drive):
        pass

    def _show_details(self, drive):
        if not drive:
            self.details.set_text(NO_DEVICE)
            return
        lines = fw.device_details(drive, self.running_info.get(drive.path))
        self.details.set_text("\n".join("%s: %s" % line for line in lines))

    def _read_chip(self, drive):
        self.chip_reading = drive.path

        def done(res, exc):
            self.chip_reading = None
            if exc:
                self.chip_error = str(exc)
            else:
                drive.esp_chip = res
            self._relabel()
            self._picked()
        self._in_background(lambda: fw.read_esp_chip(drive.path), done)

    def _read_running(self, drive):
        """Reading a running board stops its program and soft-resets it, so each one is read only once."""
        self.info_reading = drive.path

        def run():
            try:
                res = fw.read_running(drive.path)
            except fw.FirmwareError as e:
                res = str(e)
            GLib.idle_add(finish, res)

        def finish(res):
            if self.closed:
                return
            self.info_reading = None
            self.running_info[drive.path] = res
            self._relabel()
            self._picked()
        threading.Thread(target=run, daemon=True).start()

    def _chip_problem(self, drive):
        """Why nothing can be done with this device yet; "" once its details are known."""
        if not drive:
            return ""
        if drive.method == "repl":
            return "Reading the board ..." if drive.path not in self.running_info else ""
        if drive.method != "esptool":
            return ""
        if self.tool_problem:
            return self.tool_problem
        if drive.esp_chip:
            return ""
        return self.chip_error or "Reading the chip ..."

    def _check(self):
        if self.running or self.result:     # a list can arrive late: it must not touch a running or finished job
            return
        drive = self.drive()
        self.update_state(drive)
        self.boot_btn.set_visible(bool(drive) and fw.can_enter_bootloader(drive))
        self.boot_btn.set_sensitive(bool(drive) and not self._chip_problem(drive))
        if self.outcome:
            self.note.set_text(self.outcome)

    def update_state(self, drive):
        pass

    @staticmethod
    def _flash_bytes(drive):
        mb = drive.esp_chip.flash_mb if drive.esp_chip else None
        return mb * 1024 * 1024 if mb else fw.ESP_FLASH_BYTES

    def _describe(self, drive):
        return fw.device_label(drive, self.running_info.get(drive.path))

    def _response(self, _dlg, response):
        if response == Gtk.ResponseType.NONE:
            self.cancel_event.set()
            self.cancel_btn.set_sensitive(False)
        elif response == Gtk.ResponseType.YES:
            self.put_in_bootloader()
        elif response == Gtk.ResponseType.CLOSE or response == Gtk.ResponseType.DELETE_EVENT:
            if not self.running:
                self.destroy()
        else:
            self.action(response)

    def action(self, response):
        pass

    def put_in_bootloader(self):
        drive = self.drive()
        badge = drive.method == "badge"

        def done(found):
            self.prefer = found.path
            return "Now in its bootloader, at %s." % found.path
        self._start(lambda: fw.enter_bootloader(drive.path, find=fw.detect_rom if badge else None, normal_repl=badge),
                    done)

    # -- running a job -----------------------------------------------------
    def _start(self, work, done):
        """Run a slow job with the window locked; done(result) gives what to say when it has worked."""
        for pane in list(self.app.conns):           # the job needs its port now
            self.app.disconnect(pane, keep_copied=False)
        self.running, self.outcome = True, ""
        self.cancel_event.clear()
        self.cancel_btn.set_sensitive(True)
        self.cancel_btn.show()
        self.bar.set_fraction(0)
        self.bar.show()
        for w in self.locked_widgets():
            w.set_sensitive(False)
        self.boot_btn.set_sensitive(False)

        def run():
            try:
                res, exc = work(), None
            except Exception as e:
                res, exc = None, e
            GLib.idle_add(self._job_done, res, exc, done)
        threading.Thread(target=run, daemon=True).start()

    def _progress(self, done, total):
        GLib.idle_add(self.bar.set_fraction, done / total)

    def _stage(self, text):
        GLib.idle_add(self.note.set_text, text + " ...")

    def unlocked_widgets(self):
        return (self.board_box,)

    def after_unlock(self):
        pass

    def _job_done(self, res, exc, done):
        self.running = False
        self.cancel_btn.hide()
        self.bar.hide()
        self.close_btn.set_sensitive(True)
        text = done(res) if not exc else ""
        if self.result:                     # finished for good: only Close, or Connect, is left
            self.note.set_text(text)
            return
        for w in self.unlocked_widgets():
            w.set_sensitive(True)
        self.after_unlock()
        self.drives, self.running_info = [], {}     # read them again: the board may have restarted or still be there
        self.filling = True
        self.board_box.remove_all()
        self.filling = False
        self.scan()
        self._picked()
        self.outcome = self.cancel_text if isinstance(exc, Cancelled) else "Failed." if exc else text
        self.note.set_text(self.outcome)
        if exc and not isinstance(exc, Cancelled):
            error(self, self.fail_title, str(exc))

    def _erase_uf2(self, drive):
        """Erase a board on a UF2 drive with Raspberry Pi's tool; returns the drive when it comes back."""
        self._stage("Getting the erase tool")
        image = self.catalog.erase_tool(self._progress, self.cancel_event.is_set)
        try:
            fw.check_nuke_uf2(image)
        except fw.FirmwareError:
            self.catalog.forget(Build(NUKE_FILE, "", "", "", False))
            raise
        self._stage("Erasing flash")
        return fw.erase_uf2(image, drive, self._progress, self.cancel_event.is_set)

    def _erase(self, drive):
        if drive.method == "esptool":
            fw.erase_esptool(drive, self._stage, self.cancel_event.is_set)
            return drive
        return self._erase_uf2(drive)

    def _confirm_erase(self, drive):
        what = self._describe(drive)
        return ask_yes_no(self, "Erase the flash", "Erase all of the flash on the %s?\n\nThis removes its firmware and "
                          "all its files. They cannot be recovered without a backup." % what) and \
            ask_yes_no(self, "Erase the flash", "Really erase everything on the %s?" % what)


class FirmwareDialog(DeviceDialog):
    fail_title = "Install failed"
    cancel_text = "Cancelled. The flash may be part written: install again before using the board."

    def __init__(self, app):
        self.info = None
        self.boards, self.builds, self.shown_builds, self.list_chip, self.list_error = [], [], [], None, ""
        super().__init__(app, "Install firmware")
        self._source_changed()

    def build_body(self, box):
        grid = Gtk.Grid(column_spacing=10, row_spacing=6)
        self.use_site = Gtk.RadioButton(label="Download from the internet")
        self.use_file = Gtk.RadioButton(label="Use a file on this computer", group=self.use_site)
        self.use_site.connect("toggled", lambda _b: self._source_changed())
        grid.attach(self.use_site, 0, 0, 1, 1)
        self.site_rows = Gtk.Grid(column_spacing=10, row_spacing=6, hexpand=True)
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
        grid.attach(self.site_rows, 0, 1, 1, 1)
        grid.attach(self.use_file, 0, 2, 1, 1)
        self.chooser = Gtk.FileChooserButton(title="Choose a firmware file", action=Gtk.FileChooserAction.OPEN)
        firmware_filter(self.chooser)
        self.chooser.connect("file-set", lambda _c: self._check())
        grid.attach(self.chooser, 0, 3, 1, 1)
        box.pack_start(grid, False, False, 0)
        self.erase = Gtk.CheckButton(label="Erase the whole flash first. This removes all files. The usual advice the "
                                     "first time MicroPython goes on an ESP32 board.")
        self.erase.get_child().set_line_wrap(True)
        box.pack_start(self.erase, False, False, 0)

    def add_action_buttons(self):
        self.connect_btn = self.add_button("Connect", Gtk.ResponseType.APPLY)
        self.connect_btn.set_no_show_all(True)
        self.connect_btn.hide()                                     # add_button has already shown it
        self.install_btn = self.add_button("Install", Gtk.ResponseType.OK)

    def locked_widgets(self):
        return (self.install_btn, self.close_btn, self.board_box, self.chooser, self.site_rows, self.use_site,
                self.use_file, self.erase)

    def unlocked_widgets(self):
        return (self.board_box, self.use_site, self.use_file)

    def after_unlock(self):
        self._source_changed()

    def action(self, response):
        if response == Gtk.ResponseType.OK:
            self.install()
        elif response == Gtk.ResponseType.APPLY:
            self._connect_new_port()

    def device_changed(self, drive):
        chip = self._list_for(drive)
        if chip != self.list_chip:
            self.list_chip = chip
            self._load_boards(chip)

    def _list_for(self, drive):
        """(chip, the badges to include) to list boards for. On esptool, only once the chip has been read."""
        if not drive or not drive.chip:
            return None
        if drive.method != "esptool":
            return drive.chip, ()
        if not drive.esp_chip:
            return None
        badge = drive.esp_chip.badge
        return drive.chip, (badge,) if badge else ()

    # -- the site's lists ----------------------------------------------------
    def _load_boards(self, key):
        self.boards, self.builds, self.list_error = [], [], ""
        self.product_box.remove_all()
        self.build_box.remove_all()
        if key:
            self._in_background(lambda: self.catalog.boards(*key), self._boards_loaded)

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
        board, drive = self.product(), self.drive()
        if board and drive:
            self._in_background(lambda: self.catalog.builds(board.id, drive.chip), self._builds_loaded)
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
    def update_state(self, drive):
        """Install stays off until a board and a suitable image are both known."""
        self.info = None
        if drive and drive.method in ("repl", "badge"):
            self.note.set_text(fw.bootloader_hint(drive))
            self.install_btn.set_sensitive(False)
            self.erase.set_sensitive(False)
            return
        erasable = bool(drive and drive.chip)
        self.erase.set_sensitive(erasable)
        if not erasable:
            self.erase.set_active(False)
        ready = self._check_site(drive) if self.use_site.get_active() else self._check_file(drive)
        if ready and self.tool_problem:
            self.note.set_text(self.tool_problem)
            ready = False
        self.install_btn.set_sensitive(ready)

    def _check_site(self, drive):
        problem = self._chip_problem(drive)
        if not drive:
            text = "No board in its bootloader yet."
        elif problem:
            text = problem
        elif not drive.chip:
            text = "No list for this bootloader: use a file instead."
        elif not self.boards:
            text = self.list_error or "Reading the board list ..."
        elif not self.product():
            text = "%s %s" % (self._pick_board(drive), self.list_error)
        elif not self.build():
            text = self.list_error or ("Reading the versions ..." if not self.builds else "No release for this board: "
                                       "tick the box below Version to see other builds.")
        else:
            self.note.set_text(("Will install %s. %s" % (self.build().filename, self.list_error)).strip())
            return True
        self.note.set_text(text.strip())
        return False

    @staticmethod
    def _pick_board(drive):
        if drive.method == "uf2":
            return PICK_RP2
        if drive.esp_chip and not drive.esp_chip.badge:
            return "Choose your board. This chip does not match a badge, so none is listed (found %s)." \
                % drive.esp_chip.text
        return "Choose your board."

    def _check_file(self, drive):
        path = self.chooser.get_filename()
        problem = self._chip_problem(drive)
        if not drive:
            self.note.set_text("No board in its bootloader yet.")
        elif problem:
            self.note.set_text(problem)
        elif not path:
            self.note.set_text("Choose a firmware file.")
        else:
            try:
                self.info = fw.check_image(path, drive.chip, self._flash_bytes(drive))
                self.note.set_text("%s: %s firmware for the %s, %s\nSHA-256 %s" % (
                    os.path.basename(path), self.info.cpu, fw.CHIP_NAMES[drive.chip], human(self.info.size),
                    fw.sha256_of(path)))
                return True
            except (fw.FirmwareError, OSError) as e:
                self.note.set_text("Refused: %s" % e)
        return False

    # -- the install -------------------------------------------------------
    def install(self):
        drive = self.drive()
        site = self.use_site.get_active()
        build, path = (self.build(), None) if site else (None, self.chooser.get_filename())
        erase_first = self.erase.get_active()
        options = {}
        if drive.method == "esptool":
            options["erase"] = erase_first
            if site:
                options["expect_badge"] = self.product().id if self.product().id in fw.BADGES else ""
        what = "%s for the %s" % (build.label, self.product().label) if site else os.path.basename(path)
        also = "\n\nmpexplorer will disconnect from the current board first." if self.app.device else ""
        if drive.esp_chip:
            also = "\n\nThis chip reads as %s. The app cannot tell a badge from another board with the same chip, " \
                   "so check this is the right board.%s" % (drive.esp_chip.text, also)
        if not ask_yes_no(self, "Install firmware", "Install %s on the %s at %s?\n\nThis replaces the firmware on the "
                          "board in its bootloader. Files on the board usually stay, but copy off anything you "
                          "need first.%s" % (what, fw.CHIP_NAMES[drive.chip], drive.path, also)):
            return
        if erase_first and not ask_yes_no(self, "Erase the flash", "Erase everything on the board first? Its "
                                          "files cannot be recovered."):
            return

        def work():
            image = self._fetch(build, path, drive)
            target = drive
            if erase_first and drive.method == "uf2":
                target = self._erase_uf2(drive)
            return fw.install_and_verify(image, target, on_progress=self._progress, on_stage=self._stage,
                                         cancelled=self.cancel_event.is_set, **options), fw.sha256_of(image)
        self._start(work, self._installed)

    def _fetch(self, build, path, drive):
        if not build:
            return path
        self._stage("Getting %s" % build.filename)
        image = self.catalog.download(build, on_progress=self._progress, cancelled=self.cancel_event.is_set)
        try:
            fw.check_image(image, drive.chip, self._flash_bytes(drive))
        except fw.FirmwareError:
            self.catalog.forget(build)          # the next try downloads it again
            raise
        return image

    def _installed(self, res):
        self.result, sha = res
        if not self.result.port:
            return ("Written and checked. The app cannot follow this board as it starts: reset it yourself and Scan "
                    "for its port.\nSHA-256 %s" % sha)
        self.connect_btn.show()
        return "Installed: MicroPython %s on %s, now at %s.\nSHA-256 %s" % (
            self.result.release, self.result.machine, self.result.port, sha)

    def _connect_new_port(self):
        port = self.result.port
        app = self.app
        self.destroy()
        app.scan_ports()
        app.port_box.get_child().set_text(port)
        app.connect_device()


def firmware_filter(chooser):
    files = Gtk.FileFilter()
    files.set_name("Firmware (*.uf2, *.bin)")
    for pattern in ("*.uf2", "*.UF2", "*.bin"):
        files.add_pattern(pattern)
    chooser.add_filter(files)


class BackupDialog(DeviceDialog):
    """Save a board's whole flash to a file, and put such a file back."""
    cancel_text = "Cancelled. A restore can leave the flash part written: restore again before using the board."

    def __init__(self, app):
        super().__init__(app, "Back up or restore flash")

    def add_action_buttons(self):
        self.backup_btn = self.add_button("Back up...", Gtk.ResponseType.ACCEPT)
        self.restore_btn = self.add_button("Restore...", Gtk.ResponseType.OK)

    def locked_widgets(self):
        return (self.close_btn, self.board_box, self.backup_btn, self.restore_btn)

    def action(self, response):
        if response == Gtk.ResponseType.ACCEPT:
            self.backup()
        elif response == Gtk.ResponseType.OK:
            self.restore()

    def update_state(self, drive):
        problem = self._chip_problem(drive)
        usable = bool(drive) and not problem and drive.method != "badge"
        self.backup_btn.set_sensitive(usable and drive.method != "uf2")
        self.restore_btn.set_sensitive(usable and drive.method != "repl" and bool(drive.chip))
        self.backup_btn.set_tooltip_text("A board in BOOTSEL cannot be read: start it normally to back it up"
                                         if drive and drive.method == "uf2" else None)
        self.restore_btn.set_tooltip_text("Hold BOOTSEL while plugging it in to restore"
                                          if drive and drive.method == "repl" else None)
        if not drive:
            self.note.set_text("")
        elif drive.method == "badge":
            self.note.set_text(fw.bootloader_hint(drive))
        elif problem:
            self.note.set_text(problem)
        elif drive.method == "repl":
            self.note.set_text("This board can be backed up as it is. To restore: %s" % fw.bootloader_hint(drive))
        elif drive.method == "uf2":
            self.note.set_text("In BOOTSEL this board can be restored. Start it normally to back it up.")
        else:
            self.note.set_text("")

    def _ask_backup_path(self, drive):
        dlg = Gtk.FileChooserDialog(title="Save the flash backup as", transient_for=self,
                                    action=Gtk.FileChooserAction.SAVE)
        dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Save", Gtk.ResponseType.OK)
        dlg.set_do_overwrite_confirmation(True)
        dlg.set_current_name("flash-backup-%s.%s" % (time.strftime("%Y%m%d-%H%M"),
                                                     "uf2" if drive.method == "repl" else "bin"))
        path = dlg.get_filename() if dlg.run() == Gtk.ResponseType.OK else None
        dlg.destroy()
        return path

    def _ask_restore_path(self):
        dlg = Gtk.FileChooserDialog(title="Choose a flash backup", transient_for=self,
                                    action=Gtk.FileChooserAction.OPEN)
        dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Open", Gtk.ResponseType.OK)
        firmware_filter(dlg)
        path = dlg.get_filename() if dlg.run() == Gtk.ResponseType.OK else None
        dlg.destroy()
        return path

    def backup(self):
        drive = self.drive()
        path = self._ask_backup_path(drive)
        if not path:
            return

        def work():
            if drive.method == "repl":
                got = fw.backup_repl(drive.path, path, self._progress, self.cancel_event.is_set)
            else:
                got = fw.backup_esptool(drive, path, self._progress, self._stage, self.cancel_event.is_set)
            return got, fw.sha256_of(path)

        def done(res):
            got, sha = res
            return "Backed up %s (%s) to %s\nSHA-256 %s\nIt can hold Wi-Fi passwords, so only you can read it." % (
                human(got.size), got.source, got.path, sha)
        self._start(work, done)

    def restore(self):
        drive = self.drive()
        path = self._ask_restore_path()
        if not path:
            return
        try:
            fw.check_image(path, drive.chip, self._flash_bytes(drive))
        except fw.FirmwareError as e:
            error(self, "Cannot restore this file", str(e))
            return
        if not ask_yes_no(self, "Restore the flash", "Write %s to the %s?\n\nThis replaces what is on the board. The "
                          "app cannot tell whether this backup came from this board, so check it did." % (
                              os.path.basename(path), self._describe(drive))):
            return

        def work():
            return fw.install_and_verify(path, drive, on_progress=self._progress, on_stage=self._stage,
                                         cancelled=self.cancel_event.is_set), fw.sha256_of(path)

        def done(res):
            got, sha = res
            if not got.port:
                return "Written and checked. Reset the board yourself and Scan for its port.\nSHA-256 %s" % sha
            return "Restored: MicroPython %s on %s, now at %s.\nSHA-256 %s" % (got.release, got.machine, got.port, sha)
        self._start(work, done)


class EraseDialog(DeviceDialog):
    """Erase the whole flash, leaving a board in its bootloader."""

    def __init__(self, app):
        super().__init__(app, "Erase flash")

    def add_action_buttons(self):
        self.erase_btn = self.add_button("Erase...", Gtk.ResponseType.OK)

    def locked_widgets(self):
        return (self.close_btn, self.board_box, self.erase_btn)

    def action(self, response):
        if response == Gtk.ResponseType.OK:
            self.erase_flash()

    def update_state(self, drive):
        problem = self._chip_problem(drive)
        usable = bool(drive) and not problem and drive.method != "repl" and bool(drive.chip)
        self.erase_btn.set_sensitive(usable)
        if not drive:
            self.note.set_text("")
        elif drive.method in ("repl", "badge"):
            self.note.set_text(fw.bootloader_hint(drive))
        elif problem:
            self.note.set_text(problem)
        elif not drive.chip:
            self.note.set_text("The app has no way to erase this bootloader.")
        else:
            self.note.set_text("This removes the firmware and all files.")

    def erase_flash(self):
        drive = self.drive()
        if not self._confirm_erase(drive):
            return
        self._start(lambda: self._erase(drive),
                    lambda _res: "Erased. The board has no firmware now, and stays in its bootloader, ready for an "
                                 "install.")
