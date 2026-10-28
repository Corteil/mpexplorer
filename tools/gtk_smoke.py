"""Run the GTK app against the fake board in-process; exercise list/open/save/copy/console."""
import os, sys, tempfile, time; ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT); sys.path.insert(0, ROOT + "/tests")
import gi; gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib
from fakedevice import FakeSerial
from mpexplorer.transport import RawREPL
from mpexplorer.backends import DeviceBackend
from mpexplorer.console import ConsoleSession
from mpexplorer.gtkui.app import App
app = App()
def pump(ms):
    end = time.time() + ms / 1000
    while time.time() < end:
        while Gtk.events_pending(): Gtk.main_iteration()
        time.sleep(0.01)
repl = RawREPL(FakeSerial(paste="on")); repl.enter_raw()
dev = DeviceBackend(repl); app.device = dev; app.session = ConsoleSession(repl)
app.right.set_location(dev, dev.root)
tmp = tempfile.mkdtemp(); open(tmp + "/hello.txt", "w").write("hi there\n"); os.mkdir(tmp + "/sub")
devdir = tempfile.mkdtemp(); app.right.go(devdir); app.left.go(tmp); pump(1500)
print("host rows:", [r[1] for r in app.left.store], "| device rows:", [r[1] for r in app.right.store])
# select hello.txt and upload
app.left.tree.get_selection().select_path(Gtk.TreePath.new_from_string([i for i, r in enumerate(app.left.store) if r[1] == "hello.txt"][0].__str__()))
app.transfer(app.left, app.right); pump(2500)
print("device rows after upload:", [r[1] for r in app.right.store], "|", app.status.get_text())
# open it in the editor on the device
app.right.tree.get_selection().select_path(Gtk.TreePath.new_from_string([i for i, r in enumerate(app.right.store) if r[1] == "hello.txt"][0].__str__()))
app.right._open(); pump(2000)
print("windows:", [w.get_title() for w in app.windows])
ed = app.windows[-1]; ed.buf.insert(ed.buf.get_end_iter(), "more\n"); ed.save(); pump(2000)
print("editor status:", ed.status.get_text(), "| on device:", repr(open(devdir + "/hello.txt").read()))
app.open_console(); pump(800)
c = app.console; c.inp.get_buffer().set_text("print(6*7)"); c.run(); pump(2000)
b = c.out.get_buffer(); print("console output:", repr(b.get_text(b.get_start_iter(), b.get_end_iter(), True)))
app.destroy() if False else None
print("OK")
os._exit(0)
