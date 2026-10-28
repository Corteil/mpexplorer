"""Tk: open an editor (or console with WIN=console) with the pointer parked on the main window, click inside it with the
virtual mouse and report which window got the click. Usage: tk_click_probe.py <repo> <fifo>  (env PX/PY, VARIANT)."""
"""Open an editor over the main window with the pointer parked on the main window, click inside the editor, report
which window got the click."""
import sys, time; sys.path.insert(0, sys.argv[1])
from mpexplorer.gui.app import App
from mpexplorer.gui.widgets import Editor
import mpexplorer.gui.widgets as W
from mpexplorer.gui import wm
import os
VAR = os.environ.get("VARIANT", "")
def _nudge(self):
    self.update_idletasks()
    if VAR == "remap":
        self.withdraw(); self.update(); self.deiconify()
    elif VAR == "topmost":
        self.attributes("-topmost", True); self.after(150, lambda: self.attributes("-topmost", False))
    elif VAR == "lowerlift":
        self.lower(); self.update(); self.lift()
    elif VAR == "activate":
        wm.activate(self.winfo_id())
    elif VAR == "iconify":
        self.iconify(); self.update(); self.after(300, self.deiconify)
    elif VAR == "activate_main_then":
        wm.activate(self.master.master.winfo_id()); self.after(200, lambda: wm.activate(self.winfo_id()))
    self.after(50, self._take_focus)
def _appear(self):
    if VAR == "fast":                       # map, unmap, map without letting the event loop run in between
        self.deiconify(); self.withdraw(); self.deiconify()
    elif VAR == "fast_update":              # same but flush requests to the server between
        self.deiconify(); self.update_idletasks(); self.withdraw(); self.update_idletasks(); self.deiconify()
    elif VAR == "early":                    # remap while still 1x1 and unpainted, then full size
        self.geometry("1x1"); self.deiconify(); self.update(); self.withdraw(); self.update(); self.geometry("720x520"); self.deiconify()
    else:
        self.deiconify()
    self.after(100, self._nudge)
if VAR in ("fast", "fast_update", "early"):
    W.AppWindow._appear = _appear
if VAR and VAR not in ("fast", "fast_update", "early") and hasattr(W, "AppWindow"):
    W.AppWindow._nudge = _nudge
fifo = sys.argv[2]
class B: label = "Device"
def vm(cmd):
    with open(fifo, "w") as f: f.write(cmd + "\n")
def pump(ms):
    end = time.time() + ms / 1000
    while time.time() < end: a.update(); time.sleep(0.01)
a = App(); a.geometry("1180x680+100+60"); a.update(); pump(500)
hits = []; ev_log = []
a.bind_all("<Enter>", lambda ev: ev_log.append("enter " + str(ev.widget)[-30:]))
a.bind_all("<ButtonRelease-1>", lambda ev: ev_log.append("release " + str(ev.widget)[-30:]))
a.bind_all("<ButtonPress-1>", lambda ev: hits.append(str(ev.widget)))
vm("move %s %s" % (os.environ.get("PX", "300"), os.environ.get("PY", "300"))); pump(900)
tmap = []; t0 = time.time()
W.AppWindow.__init__.__defaults__
class Sess: history = []; repl = type("R", (), {"trace": None})()
from mpexplorer.gui.console_window import ConsoleWindow
ed = (ConsoleWindow(a, Sess()) if os.environ.get("WIN") == "console" else Editor(a, B(), "/one.py", "hello\n" * 20)); ed.bind("<Map>", lambda e: tmap.append(("map", round(time.time() - t0, 3))) if e.widget is ed else None); ed.bind("<Unmap>", lambda e: tmap.append(("unmap", round(time.time() - t0, 3))) if e.widget is ed else None); pump(1500); print("pointer:", a.winfo_pointerxy(), flush=True)
t = ed.out if os.environ.get('WIN') == 'console' else ed.text; cx, cy = t.winfo_rootx() + 150, t.winfo_rooty() + 150
print("editor text centre:", cx, cy, "| editor geometry:", ed.wm_geometry(), flush=True)
vm("click %d %d" % (cx, cy)); pump(1200); n1 = len(hits); vm("click %d %d" % (cx + 20, cy + 20)); pump(1200); print("pointer after click:", a.winfo_pointerxy(), "| events:", ev_log[-4:], flush=True)
print("map/unmap timeline:", tmap, flush=True)
print("1st click:", hits[:n1] or "nothing", "| 2nd click:", hits[n1:] or "nothing", flush=True)
print("RESULT:", "OK" if hits[:1] and hits[0].startswith(str(ed)) else "BUG" if hits[:1] else "no first click seen", flush=True)
a.destroy()
