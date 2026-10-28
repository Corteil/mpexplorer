"""Virtual absolute mouse and keyboard through /dev/uinput, so a test can click and type like a person.

Run it as root through tools/vmouse_ctl.py (needs `pip install evdev` in the venv that runs it). Commands are written,
one per line, to a FIFO: move X Y | click X Y | dclick X Y | type letters | key enter | quit. Coordinates are screen
pixels (1920x1080 assumed). Close every real GUI first: the rig clicks whatever is on screen.
"""
import os, sys, time
from evdev import AbsInfo, UInput, ecodes as e
W, H = 1920, 1080
fifo = sys.argv[1]
cap = {e.EV_KEY: [e.BTN_LEFT], e.EV_ABS: [(e.ABS_X, AbsInfo(0, 0, W - 1, 0, 0, 0)), (e.ABS_Y, AbsInfo(0, 0, H - 1, 0, 0, 0))]}
ui = UInput(cap, name="claude-vmouse")
KEYS = {c: getattr(e, "KEY_" + c.upper()) for c in "abcdefghijklmnopqrstuvwxyz"}
kb = UInput({e.EV_KEY: list(KEYS.values()) + [e.KEY_ENTER]}, name="claude-vkbd")
if not os.path.exists(fifo):
    os.mkfifo(fifo); os.chmod(fifo, 0o666)
def move(x, y):
    # a few intermediate steps so the compositor sees real motion
    ui.write(e.EV_ABS, e.ABS_X, x); ui.write(e.EV_ABS, e.ABS_Y, y); ui.syn()
print("ready", flush=True)
time.sleep(1.0)
while True:
    with open(fifo) as f:
        for line in f:
            print("cmd", line.strip(), round(time.time() % 1000, 2), flush=True)
            p = line.split()
            if not p: continue
            if p[0] == "quit": ui.close(); sys.exit(0)
            if p[0] == "dclick":
                x, y = int(p[1]), int(p[2]); move(x, y); time.sleep(0.6)
                for _ in range(2):
                    ui.write(e.EV_KEY, e.BTN_LEFT, 1); ui.syn(); time.sleep(0.05)
                    ui.write(e.EV_KEY, e.BTN_LEFT, 0); ui.syn(); time.sleep(0.07)
                continue
            if p[0] == "key":
                kb.write(e.EV_KEY, e.KEY_ENTER, 1); kb.syn(); time.sleep(0.05)
                kb.write(e.EV_KEY, e.KEY_ENTER, 0); kb.syn(); time.sleep(0.1)
                continue
            if p[0] == "type":
                for ch in p[1]:
                    kb.write(e.EV_KEY, KEYS[ch], 1); kb.syn(); time.sleep(0.03)
                    kb.write(e.EV_KEY, KEYS[ch], 0); kb.syn(); time.sleep(0.05)
                continue
            x, y = int(p[1]), int(p[2])
            move(x, y); time.sleep(0.6)
            if p[0] == "click":
                ui.write(e.EV_KEY, e.BTN_LEFT, 1); ui.syn(); time.sleep(0.08)
                ui.write(e.EV_KEY, e.BTN_LEFT, 0); ui.syn(); time.sleep(0.1)
