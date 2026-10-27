"""Filesystem backends: the host's disk and a MicroPython device over the raw REPL."""
import binascii
import json
import os
import posixpath
from dataclasses import dataclass

from .errors import BackendError

CHUNK = 2048
RP_EPOCH_OFFSET = 946684800     # seconds between 1970 and 2000 (MicroPython rp2 epoch)


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

    def listdir(self, p):
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

    def _json(self, code):
        return json.loads(self._run(code))

    def join(self, a, b):
        return posixpath.join(a, b)

    def parent(self, p):
        return posixpath.dirname(p.rstrip("/")) or "/"

    def basename(self, p):
        return posixpath.basename(p.rstrip("/"))

    def listdir(self, p):
        rows = self._json(
            "p=%r\nr=[]\nfor e in os.ilistdir(p):\n n=e[0]\n d=e[1]&0x4000\n s=t=0\n"
            " try:\n  st=os.stat(p.rstrip('/')+'/'+n)\n  s=st[6];t=st[8]\n"
            " except OSError:\n  pass\n r.append([n,1 if d else 0,0 if d else s,t])\n"
            "print(json.dumps(r))" % p)
        return [Entry(n, bool(d), s, t + self.epoch_offset) for n, d, s, t in rows]

    def stat(self, p):
        r = self._json(
            "try:\n st=os.stat(%r)\n print(json.dumps([1 if st[0]&0x4000 else 0,st[6],st[8]]))\n"
            "except OSError:\n print('null')" % p)
        if r is None:
            return None
        d, size, t = r
        return Entry(self.basename(p) or "/", bool(d), 0 if d else size, t + self.epoch_offset)

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

    def close(self):
        self.repl.close()


def human(n):
    n = float(n)
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return "%d %s" % (n, unit) if unit == "B" else "%.1f %s" % (n, unit)
        n /= 1024
    return "%.1f GB" % n


__all__ = ["Entry", "LocalBackend", "DeviceBackend", "BackendError", "CHUNK", "human"]
