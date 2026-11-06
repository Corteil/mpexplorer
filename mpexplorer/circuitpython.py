"""CircuitPython boards: telling them from MicroPython, and using their CIRCUITPY drive for files.

Seen on a real CircuitPython 10.3.1 Pico 2 (docs/CIRCUITPYTHON_PLAN.md): the serial REPL works, but writes
over it fail with `OSError: [Errno 30] Read-only filesystem` while the host has the drive mounted, and it has no
`os.ilistdir` or `time.gmtime`. So files go through the mounted drive and everything else (run, console,
terminal) through the serial port.
"""
import os
import re
import shutil
import time

from .backends import LocalBackend
from .errors import BackendError
from .transport import DeviceError


class DriveNotFound(BackendError):
    pass


def detect(repl):
    """('circuitpython', '10.3.1'), ('micropython', '1.28.0') or (None, '') when the board does not say."""
    try:
        out = repl.exec("import sys\nprint(sys.implementation.name)\nprint('.'.join(str(n) for n in "
                        "sys.implementation.version[:3]))", timeout=5).decode().split()
    except (BackendError, OSError):
        return None, ""
    return (out[0].lower(), out[1]) if len(out) >= 2 else (None, "")


def mounts(text=None):
    """Mount points of FAT drives named CIRCUITPY (udisks adds a number when there are two: CIRCUITPY1)."""
    if text is None:
        try:
            with open("/proc/mounts", encoding="utf-8") as f:
                text = f.read()
        except OSError:
            return []
    found = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[2] in ("vfat", "msdos", "fuseblk", "exfat"):
            path = parts[1].replace("\\040", " ")
            if re.fullmatch(r"CIRCUITPY\d*", os.path.basename(path)):
                found.append(path)
    return found


def read_boot_out(mount):
    """{'version', 'board', 'board_id', 'uid'} from the drive's boot_out.txt; {} when it cannot be read."""
    try:
        with open(os.path.join(mount, "boot_out.txt"), encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return {}
    info = {}
    m = re.match(r"Adafruit CircuitPython (\S+) on [^;]*; (.*)", lines[0]) if lines else None
    if m:
        info["version"], info["board"] = m.group(1), m.group(2).strip()
    for line in lines[1:]:
        key, _, value = line.partition(":")
        if key.strip().lower() == "board id":
            info["board_id"] = value.strip()
        elif key.strip().lower() == "uid":
            info["uid"] = value.strip()
    return info


def find_drive(serial, mount_list=None):
    """The mount of the board whose USB serial number (its UID) is `serial`. With no serial, the only drive if there
    is exactly one. Raises DriveNotFound with what to do about it."""
    mount_list = mounts() if mount_list is None else mount_list
    if serial:
        for path in mount_list:
            if read_boot_out(path).get("uid", "").lower() == serial.lower():
                return path
    elif len(mount_list) == 1:
        return mount_list[0]
    raise DriveNotFound("This is a CircuitPython board, but its CIRCUITPY drive is not mounted. Files are read and "
                        "written through that drive (the board refuses writes over serial while the computer has it), "
                        "so mount it (unplug and replug, or open it in the file manager) and connect again.")


class _SyncedWriter:
    """FAT can lose a write if the board is unplugged soon after: flush it to the drive when the file is closed."""

    def __init__(self, path):
        self.f = open(path, "wb")

    def write(self, data):
        return self.f.write(data)

    def close(self):
        if not self.f.closed:
            self.f.flush()
            os.fsync(self.f.fileno())
            self.f.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class DriveBackend(LocalBackend):
    """The CIRCUITPY drive as the board's file system, plus the serial REPL (`repl`) for running things."""
    key = "device"          # it is the board as far as the window is concerned: Run, Upload / Download wording, ...
    label = "Device"

    def __init__(self, mount, repl):
        super().__init__()
        self.mount, self.root, self.repl = mount, mount, repl

    def parent(self, p):
        p = p.rstrip(os.sep)
        return self.mount if p == self.mount.rstrip(os.sep) else (os.path.dirname(p) or self.mount)

    def open_write(self, p):
        return _SyncedWriter(p)

    def copy_file(self, src, dst, on_bytes=None):
        shutil.copyfile(src, dst)
        with open(dst, "r+b") as f:         # fsync needs a writable handle on Windows
            os.fsync(f.fileno())
        if on_bytes:
            on_bytes(os.path.getsize(dst))

    def _run(self, code, timeout=10.0):
        """Code run on the board (the libraries tools ask what firmware it is, for one)."""
        return self.repl.exec(code, timeout)

    def memory(self):
        """Flash, file system and RAM sizes as the board reports them (meminfo.read); read-only."""
        from . import meminfo
        return meminfo.read(lambda code: self._run(code, 15.0))

    def board_path(self, p):
        """'/media/pi/CIRCUITPY/lib/x.py' -> '/lib/x.py', the path the board itself uses."""
        rel = os.path.relpath(p, self.mount)
        return "/" if rel == "." else "/" + rel.replace(os.sep, "/")

    def run(self, path, timeout=60.0):
        """Run a script on the board; returns its output (raises DeviceError on a traceback)."""
        return self.repl.exec("exec(open(%r).read(),{})" % self.board_path(path), timeout).decode(errors="replace")

    def clock(self):
        """The board's clock as (year, month, day, hour, minute, second). CircuitPython has no time.gmtime, so this
        reads localtime(), which on a board is the RTC as it was set (we set it to UTC, as for MicroPython boards)."""
        out = self.repl.exec("import time\nt=time.localtime()\nprint(t[0],t[1],t[2],t[3],t[4],t[5])", 5).decode()
        return tuple(int(n) for n in out.split())

    def set_clock(self, now=None):
        """Set the board's RTC to the host's UTC time (or the timestamp `now`); returns the time read back.
        Needs the board's `rtc` module: some builds (a Pico W was seen) do not have one."""
        t = time.gmtime(now)
        code = ("import rtc,time\nrtc.RTC().datetime=time.struct_time((%d,%d,%d,%d,%d,%d,%d,-1,-1))"
                % (t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec, t.tm_wday))
        try:
            self.repl.exec(code, 5)
        except DeviceError as e:
            if "rtc" in str(e):
                raise BackendError("This CircuitPython build has no rtc module, so its clock cannot be set.")
            raise
        return self.clock()

    def soft_reset(self):
        self.repl.soft_reset()

    def close(self, reset=False):
        self.repl.close(reset)
