"""Start or stop the virtual mouse/keyboard daemon (tools/vmouse.py) as root.

    python3 tools/vmouse_ctl.py start <python-with-evdev> <fifo>
    python3 tools/vmouse_ctl.py stop

Finds the daemon by scanning /proc. Never use `pkill -f vmouse`: it matches (and kills) the shell that runs it.
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = b"vmouse" + b".py"


def daemons():
    out = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            argv = open("/proc/%s/cmdline" % pid, "rb").read().split(b"\0")
        except OSError:
            continue
        if int(pid) != os.getpid() and argv and not argv[0].endswith(b"bash") and any(a.endswith(TARGET) for a in argv):
            out.append(pid)
    return out


def stop():
    for pid in daemons():
        subprocess.run(["sudo", "-n", "kill", pid])
        print("stopped", pid)


def start(python, fifo):
    stop()
    time.sleep(1)
    if os.path.exists(fifo):
        subprocess.run(["sudo", "-n", "rm", "-f", fifo])
    log = open(fifo + ".log", "w")
    subprocess.Popen(["sudo", "-n", python, os.path.join(HERE, "vmouse.py"), fifo], stdout=log,
                     stderr=subprocess.STDOUT, start_new_session=True)
    time.sleep(3)
    print(open(fifo + ".log").read().strip() or "no output yet")


if __name__ == "__main__":
    if sys.argv[1:2] == ["start"] and len(sys.argv) == 4:
        start(sys.argv[2], sys.argv[3])
    elif sys.argv[1:2] == ["stop"]:
        stop()
    else:
        sys.exit(__doc__)
