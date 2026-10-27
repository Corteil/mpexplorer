"""A serial-like object that speaks the MicroPython raw REPL and runs snippets in CPython."""
import contextlib
import io
import os
import traceback

if not hasattr(os, "ilistdir"):             # MicroPython-only API used by the snippets
    def _ilistdir(p):
        for n in os.listdir(p):
            d = os.path.isdir(os.path.join(p, n))
            yield (n, 0x4000 if d else 0x8000, 0)
    os.ilistdir = _ilistdir


class FakeSerial:
    def __init__(self, epoch_year=1970):
        self._out = bytearray()
        self._buf = bytearray()
        self._raw = False
        self.globals = {}
        self.closed = False
        self.execs = []

    @property
    def in_waiting(self):
        return len(self._out)

    def read(self, n=1):
        data = bytes(self._out[:n])
        del self._out[:n]
        return data

    def write(self, data):
        for byte in data:
            self._byte(bytes([byte]))
        return len(data)

    def close(self):
        self.closed = True

    def _byte(self, b):
        if not self._raw:
            if b == b"\x01":
                self._raw = True
                self._out += b"raw REPL; CTRL-B to exit\r\n>"
            return
        if b == b"\x02":
            self._raw = False
        elif b == b"\x03":
            self._buf.clear()
        elif b == b"\x04":
            if self._buf:
                self._execute(self._buf.decode())
                self._buf.clear()
        else:
            self._buf += b

    def _execute(self, code):
        self.execs.append(code)
        out = io.StringIO()
        err = ""
        try:
            with contextlib.redirect_stdout(out):
                exec(code, self.globals)
        except Exception:
            err = traceback.format_exc()
        self._out += b"OK" + out.getvalue().encode() + b"\x04" + err.encode() + b"\x04>"
