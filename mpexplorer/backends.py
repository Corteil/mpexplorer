"""Filesystem backends: the host's disk and a MicroPython device over the raw REPL."""
import binascii
import hashlib
import json
import os
import posixpath
import time
from dataclasses import dataclass

from .errors import BackendError

CHUNK = 2048
RP_EPOCH_OFFSET = 946684800     # seconds between 1970 and 2000 (MicroPython rp2 epoch)
MIN_VALID_MTIME = 978307200     # 2001-01-01: an earlier time means the board's clock was never set


@dataclass
class Entry:
    name: str
    is_dir: bool
    size: int = 0
    mtime: float = 0.0


class LocalBackend:
    key = "local"
    label = "Host"

    def __init__(self):
        self.root = os.path.expanduser("~")

    def join(self, a, b):
        return os.path.join(a, b)

    def parent(self, p):
        return os.path.dirname(p.rstrip(os.sep)) or os.sep

    def basename(self, p):
        return os.path.basename(p.rstrip(os.sep))

    def listdir(self, p, mtimes=True):
        out = []
        with os.scandir(p) as it:
            for e in it:
                try:
                    st = e.stat()
                    d = e.is_dir()
                    out.append(Entry(e.name, d, 0 if d else st.st_size, st.st_mtime))
                except OSError:
                    out.append(Entry(e.name, False))
        return out

    def set_mtime(self, p, t):
        os.utime(p, (t, t))

    def stat(self, p):
        try:
            st = os.stat(p)
        except OSError:
            return None
        d = os.path.isdir(p)
        return Entry(self.basename(p), d, 0 if d else st.st_size, st.st_mtime)

    def mkdir(self, p):
        os.mkdir(p)

    def remove(self, p):
        os.remove(p)

    def rmdir(self, p):
        os.rmdir(p)

    def rename(self, a, b):
        os.rename(a, b)

    def open_read(self, p):
        return open(p, "rb")

    def open_write(self, p):
        return open(p, "wb")

    def free_space(self, p):
        st = os.statvfs(p)
        return st.f_bavail * st.f_frsize

    def checksum(self, p):
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for block in iter(lambda: f.read(65536), b""):
                h.update(block)
        return h.hexdigest()


class _DeviceReader:
    def __init__(self, dev, path):
        self.dev = dev
        dev._run("_f=open(%r,'rb')" % path)
        self.open = True

    def read(self, n=CHUNK):
        out = self.dev._run("print(b.b2a_base64(_f.read(%d)).decode().strip())" % n)
        return binascii.a2b_base64(out.strip())

    def close(self):
        if self.open:
            self.open = False
            self.dev._run("_f.close();del _f")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class _DeviceWriter:
    def __init__(self, dev, path):
        self.dev = dev
        dev._run("_f=open(%r,'wb')" % path)
        self.open = True

    def write(self, data):
        for i in range(0, len(data), CHUNK):
            enc = binascii.b2a_base64(data[i:i + CHUNK], newline=False).decode()
            self.dev._run("_f.write(b.a2b_base64(%r))" % enc)

    def close(self):
        if self.open:
            self.open = False
            self.dev._run("_f.close();del _f;gc.collect()")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class DeviceBackend:
    key = "device"
    label = "Device"
    root = "/"
    HEADER = ("import os,json,gc\n"
              "try:\n import binascii as b\nexcept ImportError:\n import ubinascii as b\n")

    def __init__(self, repl):
        self.repl = repl
        self.epoch_offset = 0
        year = int(self._run("import time;print(time.gmtime(0)[0])").strip())
        if year == 2000:
            self.epoch_offset = RP_EPOCH_OFFSET

    def _run(self, code, timeout=10.0):
        return self.repl.exec(self.HEADER + code, timeout)

    def _host_time(self, t):
        """A file time from the board as a host timestamp. Badge clocks hold UTC (the GPS clock and NTP both write
        UTC to the RTC and apps add their own offset), so no time zone conversion is done here."""
        return t + self.epoch_offset if t else 0        # 0 = not fetched (listdir without mtimes)

    def clock(self):
        """The board's clock as (year, month, day, hour, minute, second)."""
        return tuple(self._json("import time\nprint(json.dumps(list(time.localtime())[:6]))"))

    def set_clock(self, now=None):
        """Set the board's RTC to the host's UTC time (or the timestamp `now`); returns the time read back."""
        t = time.gmtime(now)
        self._run("import machine\nmachine.RTC().datetime((%d,%d,%d,%d,%d,%d,%d,0))" % (
            t.tm_year, t.tm_mon, t.tm_mday, t.tm_wday, t.tm_hour, t.tm_min, t.tm_sec))
        return self.clock()

    def _json(self, code):
        return json.loads(self._run(code))

    def join(self, a, b):
        return posixpath.join(a, b)

    def parent(self, p):
        return posixpath.dirname(p.rstrip("/")) or "/"

    def basename(self, p):
        return posixpath.basename(p.rstrip("/"))

    def listdir(self, p, mtimes=False):
        """List a folder. os.stat costs tens of ms per file on some boards (FAT on a Tildagon), so unless
        mtimes is set the size comes from ilistdir's own tuple and the modified time is left 0."""
        rows = self._json(
            "p=%r\nm=%r\nr=[]\nfor e in os.ilistdir(p):\n n=e[0]\n d=e[1]&0x4000\n s=t=0\n"
            " if not m and len(e)>3 and e[3]>=0:\n  s=e[3]\n else:\n  try:\n   st=os.stat(p.rstrip('/')+'/'+n)\n"
            "   s=st[6];t=st[8]\n  except OSError:\n   pass\n r.append([n,1 if d else 0,0 if d else s,t])\n"
            "print(json.dumps(r))" % (p, bool(mtimes)))
        return [Entry(n, bool(d), s, self._host_time(t)) for n, d, s, t in rows]

    def stat(self, p):
        r = self._json(
            "try:\n st=os.stat(%r)\n print(json.dumps([1 if st[0]&0x4000 else 0,st[6],st[8]]))\n"
            "except OSError:\n print('null')" % p)
        if r is None:
            return None
        d, size, t = r
        return Entry(self.basename(p) or "/", bool(d), 0 if d else size, self._host_time(t))

    def mkdir(self, p):
        self._run("os.mkdir(%r)" % p)

    def remove(self, p):
        self._run("os.remove(%r)" % p)

    def rmdir(self, p):
        self._run("os.rmdir(%r)" % p)

    def rename(self, a, b):
        self._run("os.rename(%r,%r)" % (a, b))

    def open_read(self, p):
        return _DeviceReader(self, p)

    def open_write(self, p):
        return _DeviceWriter(self, p)

    def free_space(self, p):
        return int(self._run("st=os.statvfs(%r);print(st[1]*st[4])" % p).strip())

    def checksum(self, p):
        """SHA-256 computed on the device (no data crosses the link); None if it has no hashlib."""
        out = self._run(
            "try:\n import hashlib\nexcept ImportError:\n print('')\nelse:\n"
            " h=hashlib.sha256();buf=bytearray(512)\n with open(%r,'rb') as f:\n  while True:\n"
            "   n=f.readinto(buf)\n   if not n:\n    break\n   h.update(memoryview(buf)[:n])\n"
            " print(b.hexlify(h.digest()).decode())" % p, timeout=30 + self.stat(p).size / 5000)
        return out.strip().decode() or None

    def copy_file(self, src, dst, on_bytes=None):
        """Copy on the device itself so the data never crosses the serial link."""
        size = self.stat(src).size
        self._run(
            "with open(%r,'rb') as s, open(%r,'wb') as d:\n buf=bytearray(1024)\n while True:\n"
            "  n=s.readinto(buf)\n  if not n:\n   break\n  d.write(memoryview(buf)[:n])\n"
            "gc.collect()" % (src, dst), timeout=30 + size / 10000)
        if on_bytes:
            on_bytes(size)

    def run(self, path, timeout=60.0):
        """Execute a script on the device; returns its output (raises DeviceError on a traceback)."""
        return self.repl.exec("exec(open(%r).read(),{})" % path, timeout).decode(errors="replace")

    def soft_reset(self):
        self.repl.soft_reset()

    def close(self, reset=False):
        self.repl.close(reset)


def human_duration(seconds):
    """'45 s', '3 min 20 s', '1 h 05 min': a short time span for the transfer line."""
    s = int(round(seconds))
    if s < 60:
        return "%d s" % s
    if s < 3600:
        return "%d min %02d s" % (s // 60, s % 60)
    return "%d h %02d min" % (s // 3600, s % 3600 // 60)


def human(n):
    n = float(n)
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return "%d %s" % (n, unit) if unit == "B" else "%.1f %s" % (n, unit)
        n /= 1024
    return "%.1f GB" % n


__all__ = ["Entry", "LocalBackend", "DeviceBackend", "BackendError", "CHUNK", "human", "human_duration"]
