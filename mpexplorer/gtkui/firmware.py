"""Tools > Install firmware: put MicroPython on a board in its bootloader. The logic is in ..firmware."""
import os
import threading

from .. import firmware as fw
from ..backends import human
from ..errors import Cancelled
from .common import GLib, Gtk, ask_yes_no, error

STEPS = ("Hold the BOOTSEL button while plugging the Pico or Pico 2 in. It appears here as a USB drive, "
         "and any serial connection to it ends.")
SAME_CHIP = ("The bootloader names the chip, not the board: a Pico and a Pico W read the same but need "
             "different firmware. Choose the file made for your board.")


class FirmwareDialog(Gtk.Dialog):
    def __init__(self, app):
        super().__init__(title="Install firmware", transient_for=app, modal=True)
        self.app, self.drives, self.info, self.running, self.result = app, [], None, False, None
        self.cancel_event = threading.Event()
        self.set_default_size(560, -1)
        box = self.get_content_area()
        box.set_spacing(8)
        box.set_border_width(12)
        box.pack_start(Gtk.Label(label=STEPS, xalign=0, wrap=True), False, False, 0)

        grid = Gtk.Grid(column_spacing=10, row_spacing=6)
        grid.attach(Gtk.Label(label="Board", xalign=1), 0, 0, 1, 1)
        self.board_box = Gtk.ComboBoxText(hexpand=True)
        self.board_box.connect("changed", lambda _b: self._check())
        grid.attach(self.board_box, 1, 0, 1, 1)
        grid.attach(Gtk.Label(label="Firmware", xalign=1), 0, 1, 1, 1)
        self.chooser = Gtk.FileChooserButton(title="Choose a firmware file", action=Gtk.FileChooserAction.OPEN)
        uf2 = Gtk.FileFilter()
        uf2.set_name("UF2 firmware (*.uf2)")
        uf2.add_pattern("*.uf2")
        uf2.add_pattern("*.UF2")
        self.chooser.add_filter(uf2)
        self.chooser.connect("file-set", lambda _c: self._check())
        grid.attach(self.chooser, 1, 1, 1, 1)
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
        self.scan()
        self._check()
        self.timer = GLib.timeout_add_seconds(1, self._tick)
        self.connect("destroy", lambda _d: GLib.source_remove(self.timer))
        self.show_all()

    def scan(self):
        """Keep the list current, so a board shows up as soon as it is plugged in."""
        found = fw.detect()
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
        self._check()

    def _tick(self):
        if not self.running:
            self.scan()
        return GLib.SOURCE_CONTINUE

    def drive(self):
        i = self.board_box.get_active()
        return self.drives[i] if 0 <= i < len(self.drives) else None

    def _check(self):
        """Check the image against the board as soon as both are known: Install stays off until it passes."""
        drive, path, self.info = self.drive(), self.chooser.get_filename(), None
        if self.result:
            return
        if not drive:
            self.note.set_text("No board in its bootloader yet.")
        elif not path:
            self.note.set_text("Choose a firmware file.")
        else:
            try:
                self.info = fw.check_uf2(path, drive.chip)
                self.note.set_text("%s: %s firmware for the %s, %s\nSHA-256 %s" % (
                    os.path.basename(path), self.info.cpu, fw.CHIP_NAMES[drive.chip], human(self.info.size),
                    fw.sha256_of(path)))
            except (fw.FirmwareError, OSError) as e:
                self.note.set_text("Refused: %s" % e)
        self.install_btn.set_sensitive(bool(self.info))

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

    def install(self):
        drive, path = self.drive(), self.chooser.get_filename()
        also = "\n\nmpexplorer will disconnect from the current board first." if self.app.device else ""
        if not ask_yes_no(self, "Install firmware", "Install %s on the %s at %s?\n\nThis replaces the firmware on the "
                          "board in its bootloader. Files on the board usually stay, but copy off anything you "
                          "need first.%s" % (os.path.basename(path), fw.CHIP_NAMES[drive.chip], drive.path, also)):
            return
        self.app.disconnect()
        self.running = True
        self.cancel_event.clear()
        self.cancel_btn.set_sensitive(True)
        self.cancel_btn.show()
        self.bar.set_fraction(0)
        self.bar.show()
        for w in (self.install_btn, self.close_btn, self.board_box, self.chooser):
            w.set_sensitive(False)

        def work():
            try:
                res, exc = fw.install_and_verify(
                    path, drive,
                    on_progress=lambda a, b: GLib.idle_add(self.bar.set_fraction, a / b),
                    on_stage=lambda text: GLib.idle_add(self.note.set_text, text + " ..."),
                    cancelled=self.cancel_event.is_set), None
            except Exception as e:
                res, exc = None, e
            GLib.idle_add(self._finished, res, exc)
        threading.Thread(target=work, daemon=True).start()

    def _finished(self, res, exc):
        self.running = False
        self.cancel_btn.hide()
        self.bar.hide()
        self.close_btn.set_sensitive(True)
        if res:
            self.result = res
            self.note.set_text("Installed: MicroPython %s on %s, now at %s." % (res.release, res.machine, res.port))
            self.connect_btn.show()
            return
        self.board_box.set_sensitive(True)
        self.chooser.set_sensitive(True)
        self.drives = []        # read them again: the board may have restarted or still be there
        self.board_box.remove_all()
        self.scan()
        self._check()
        if isinstance(exc, Cancelled):
            self.note.set_text("Cancelled. The flash may be part written: install again before using the board.")
        else:
            self.note.set_text("Failed.")
            error(self, "Install failed", str(exc))

    def _connect_new_port(self):
        port = self.result.port
        app = self.app
        self.destroy()
        app.scan_ports()
        app.port_box.get_child().set_text(port)
        app.connect_device()
