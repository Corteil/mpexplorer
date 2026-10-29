"""A serial-like object that speaks the MicroPython raw REPL and runs snippets in CPython."""
import builtins
import codeop
import contextlib
import io
import os
import queue
import re
import struct
import threading
import traceback

if not hasattr(os, "ilistdir"):             # MicroPython-only API used by the snippets
    def _ilistdir(p):
        for n in os.listdir(p):
            d = os.path.isdir(os.path.join(p, n))
            full = os.path.join(p, n)       # like FAT: a 4th item with the size, no modified time
            yield (n, 0x4000 if d else 0x8000, 0, 0 if d else os.path.getsize(full))
    os.ilistdir = _ilistdir


BANNER = 'MicroPython fake-1.0 on 2026-01-01; FakeBoard with FakeCPU\r\nType "help()" for more information.\r\n'


class FriendlyREPL:
    """The normal REPL a terminal sees: prompt, echo, line editing, history, tab completion, paste mode."""

    def _init_friendly(self):
        self._line, self._pos = "", 0
        self._block, self._hist, self._hi = [], [], None
        self._esc = ""
        self._paste = None
        self._friendly_prog = False

    def _emit(self, text):
        self._out += text.encode()

    def _prompt(self):
        self._emit("... " if self._block else ">>> ")

    def _friendly(self, b):
        ch = b.decode("latin-1")
        if self._esc:
            self._esc += ch
            if len(self._esc) == 2:
                if ch not in "[O":
                    self._esc = ""
            elif ch.isalpha() or ch == "~":
                seq, self._esc = self._esc[2:], ""
                self._csi(seq)
            return
        if ch == "\x1b":
            self._esc = ch
            return
        if self._paste is not None:
            return self._paste_char(ch)
        line, pos = self._line, self._pos
        if ch in "\r\n":
            self._emit("\r\n")
            self._submit()
        elif ch in "\x7f\x08":
            if pos > 0:
                self._line, self._pos = line[:pos - 1] + line[pos:], pos - 1
                self._tail("\b")
        elif ch == "\x03":
            self._emit("\r\n")
            self._line, self._pos, self._block = "", 0, []
            self._prompt()
        elif ch == "\x04":
            if not line and not self._block:
                self.globals.clear()
                self.resets += 1
                self._emit("MPY: soft reboot\r\n" + BANNER)
                self._prompt()
            else:
                self._delete()
        elif ch == "\x05":
            self._emit("\r\npaste mode; Ctrl-C to cancel, Ctrl-D to finish\r\n=== ")
            self._paste = [""]
        elif ch == "\x02":
            self._emit("\r\n" + BANNER)
            self._prompt()
        elif ch == "\t":
            self._complete()
        elif ch >= " ":
            self._insert(ch)

    def _tail(self, lead):
        """Redraw after an edit: lead + erase + rest of line + cursor back (what MicroPython's readline emits)."""
        rest = self._line[self._pos:]
        self._emit(lead + "\x1b[K" + rest + ("\x1b[%dD" % len(rest) if rest else ""))

    def _insert(self, text):
        self._line = self._line[:self._pos] + text + self._line[self._pos:]
        self._pos += len(text)
        rest = self._line[self._pos:]
        self._emit(text + rest + ("\x1b[%dD" % len(rest) if rest else ""))

    def _delete(self):
        if self._pos < len(self._line):
            self._line = self._line[:self._pos] + self._line[self._pos + 1:]
            self._tail("")

    def _set_line(self, new):
        self._emit(("\x1b[%dD" % self._pos if self._pos else "") + "\x1b[K" + new)
        self._line, self._pos = new, len(new)

    def _csi(self, seq):
        if seq == "A" and self._hist:
            self._hi = len(self._hist) - 1 if self._hi is None else max(0, self._hi - 1)
            self._set_line(self._hist[self._hi])
        elif seq == "B" and self._hi is not None:
            self._hi += 1
            if self._hi >= len(self._hist):
                self._hi = None
            self._set_line("" if self._hi is None else self._hist[self._hi])
        elif seq == "C" and self._pos < len(self._line):
            self._pos += 1
            self._emit("\x1b[C")
        elif seq == "D" and self._pos > 0:
            self._pos -= 1
            self._emit("\x1b[D")
        elif seq in ("H", "1~") and self._pos:
            self._emit("\x1b[%dD" % self._pos)
            self._pos = 0
        elif seq in ("F", "4~") and self._pos < len(self._line):
            self._emit("\x1b[%dC" % (len(self._line) - self._pos))
            self._pos = len(self._line)
        elif seq == "3~":
            self._delete()

    def _complete(self):
        m = re.search(r"[A-Za-z_][A-Za-z_0-9]*$", self._line[:self._pos])
        if not m:
            return self._insert("    ")
        names = {n for n in dir(builtins) if not n.startswith("_")} | {n for n in self.globals if not n.startswith("_")}
        found = sorted(n for n in names if n.startswith(m.group()))
        if len(found) == 1:
            self._insert(found[0][len(m.group()):])
        elif found:
            self._emit("\r\n" + "  ".join(found) + "\r\n")
            self._prompt()
            self._emit(self._line)
            if self._pos < len(self._line):
                self._emit("\x1b[%dD" % (len(self._line) - self._pos))

    def _submit(self):
        line, self._line, self._pos, self._hi = self._line, "", 0, None
        if line.strip() and (not self._hist or self._hist[-1] != line):
            self._hist.append(line)
        self._block.append(line)
        src = "\n".join(self._block) + ("\n" if not line.strip() and len(self._block) > 1 else "")
        if not src.strip():
            self._block = []
            return self._prompt()
        try:
            code = codeop.compile_command(src, "<stdin>", "single")
        except (SyntaxError, ValueError, OverflowError):
            self._block = []
            self._emit('Traceback (most recent call last):\r\n  File "<stdin>", line 1\r\n'
                       'SyntaxError: invalid syntax\r\n')
            return self._prompt()
        if code is None:
            return self._prompt()               # incomplete block: "... " continuation
        self._block = []
        self._start_friendly(code)

    def _paste_char(self, ch):
        if ch == "\x04":
            src, self._paste = "\n".join(self._paste), None
            self._emit("\r\n")
            try:
                self._start_friendly(compile(src, "<stdin>", "exec"))
            except SyntaxError:
                self._emit('SyntaxError: invalid syntax\r\n')
                self._prompt()
        elif ch == "\x03":
            self._paste = None
            self._emit("\r\n")
            self._prompt()
        elif ch in "\r\n":
            self._paste.append("")
            self._emit("\r\n=== ")
        else:
            self._paste[-1] += ch
            self._emit(ch)

    def _start_friendly(self, code):
        self.running = True
        self._friendly_prog = True
        threading.Thread(target=self._friendly_run, args=(code,), daemon=True).start()

    def _friendly_run(self, code):
        out = io.StringIO()
        err = ""
        self.globals["input"] = self._input
        try:
            with contextlib.redirect_stdout(out):
                exec(code, self.globals)
        except BaseException as e:
            err = 'Traceback (most recent call last):\n  File "<stdin>", line 1, in <module>\n%s: %s\n' % (
                type(e).__name__, e)
        self.running = False
        self._friendly_prog = False
        while not self._stdin.empty():
            self._stdin.get()
        self._emit((out.getvalue() + err).replace("\n", "\r\n"))
        self._prompt()


class FakeSerial(FriendlyREPL):
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
        self.resets = 0
        self.running = False
        self._stdin = queue.Queue()
        self.waiting_input = threading.Event()
        self._init_friendly()

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
        if self.running:                        # a program is executing: bytes are its stdin
            if b != b"\x03":
                if self._friendly_prog:         # at the normal REPL the board echoes what is typed
                    self._out += b"\r\n" if b in (b"\r", b"\n") else b
                self._stdin.put(b)
            return
        if not self._raw:
            if b == b"\x01":
                self._raw = True
                self._banner()
            else:
                self._friendly(b)
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
            self._line, self._pos, self._block = "", 0, []
            self._emit("\r\n" + BANNER)
            self._prompt()
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
        self.running = True
        threading.Thread(target=self._run, args=(code, ok), daemon=True).start()

    def _input(self, prompt=""):
        print(prompt, end="")
        self.waiting_input.set()
        chars = []
        while True:
            try:
                c = self._stdin.get(timeout=5)
            except queue.Empty:
                raise EOFError("no input")
            if c in (b"\n", b"\r"):
                text = b"".join(chars).decode()
                if not self._friendly_prog:     # real MicroPython's input() echoes what it reads (readline does)
                    print(text)
                return text
            chars.append(c)

    def _run(self, code, ok):
        out = io.StringIO()
        err = ""
        self.globals["input"] = self._input
        try:
            with contextlib.redirect_stdout(out):
                exec(code, self.globals)
        except Exception:
            err = traceback.format_exc()
        self.running = False
        while not self._stdin.empty():          # unread stdin ends up in the REPL's input buffer
            self._buf += self._stdin.get()
        self._out += (b"OK" if ok else b"") + out.getvalue().encode() + b"\x04" + err.encode() + b"\x04>"
