import os, sys, time; ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT); sys.path.insert(0, ROOT + "/tests")
from mpexplorer.gtkui.common import Gtk
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
app.right.set_location(dev, "/tmp"); pump(800)
app.open_console(); pump(500); c = app.console
c.term_radio.set_active(True); pump(1200)
print("terminal widget:", type(c.term).__name__, "| title:", c.get_title(), "| device pane paused:", app.right.paused)
text = lambda: (c.term.term.get_text(None)[0] if c.term else "")
print("screen after start:", repr(text().strip()[-60:]))
c.term.term.emit("commit", "print(6*7)\r", 11); pump(1500)
print("after typing:", repr(text().strip()[-80:]))
c.emu_radio.set_active(True); pump(800)
print("back to emulated:", c.term is None, "| title:", c.get_title(), "| paused:", app.right.paused)
c.inp.get_buffer().set_text("print(1+1)"); c.run(); pump(2000)
b = c.out.get_buffer(); print("emulated output:", repr(b.get_text(b.get_start_iter(), b.get_end_iter(), True)))
print("OK"); os._exit(0)
