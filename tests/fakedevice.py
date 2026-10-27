"""A serial-like object that speaks the MicroPython raw REPL and runs snippets in CPython."""
import contextlib
import io
import os
import struct
import traceback

if not hasattr(os, "ilistdir"):             # MicroPython-only API used by the snippets
    def _ilistdir(p):
        for n in os.listdir(p):
            d = os.path.isdir(os.path.join(p, n))
            yield (n, 0x4000 if d else 0x8000, 0)
    os.ilistdir = _ilistdir


class FakeSerial:
    """paste: "on" = firmware with raw-paste, "off" = understands it but disabled (R\\x00),
    "old" = pre-1.14 firmware that knows nothing about it."""

    def __init__(self, paste="on", window=128):
        self._out = bytearray()
        self._buf = bytearray()
        self._raw = False
        self._state = "code"        # code | paste_cmd | paste_data
        self._cmd = b""
        self.mode, self.window = paste, window
        self._granted = window
        self._since_grant = 0
        self._received = 0
        self.globals = {}
        self.closed = False
        self.execs = []
        self.paste_used = 0
        self.flow_violations = 0

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

    def _banner(self):
        self._out += b"raw REPL; CTRL-B to exit\r\n>"

    def _byte(self, b):
        if not self._raw:
            if b == b"\x01":
                self._raw = True
                self._banner()
            return
        if self._state == "paste_data":
            return self._paste_byte(b)
        if self._state == "paste_cmd":
            self._cmd += b
            if b == b"\x01":
                self._state, cmd, self._cmd = "code", self._cmd, b""
                self._start_paste(cmd)
            return
        if b == b"\x05" and self.mode != "old":
            self._state = "paste_cmd"
        elif b == b"\x01":
            self._buf.clear()
            self._banner()
        elif b == b"\x02":
            self._raw = False
        elif b == b"\x03":
            self._buf.clear()
        elif b == b"\x04":
            if self._buf:
                self._execute(self._buf.decode(), ok=True)
                self._buf.clear()
        else:
            self._buf += b

    def _start_paste(self, cmd):
        if self.mode == "on":
            self._out += b"R\x01" + struct.pack("<H", self.window)
            self._state = "paste_data"
            self._granted, self._since_grant, self._received = self.window, 0, 0
        else:
            self._out += b"R\x00"

    def _paste_byte(self, b):
        if b == b"\x04":
            self._state = "code"
            self.paste_used += 1
            self._out += b"\x04"                      # ack of end of input
            self._execute(self._buf.decode(), ok=False)
            self._buf.clear()
            return
        self._received += 1
        if self._received > self._granted:
            self.flow_violations += 1
        self._buf += b
        self._since_grant += 1
        if self._since_grant == self.window:
            self._since_grant = 0
            self._granted += self.window
            self._out += b"\x01"

    def _execute(self, code, ok):
        self.execs.append(code)
        out = io.StringIO()
        err = ""
        try:
            with contextlib.redirect_stdout(out):
                exec(code, self.globals)
        except Exception:
            err = traceback.format_exc()
        self._out += (b"OK" if ok else b"") + out.getvalue().encode() + b"\x04" + err.encode() + b"\x04>"
