"""Terminal mode: pass keystrokes straight to the board's own REPL (GUI-free)."""
import threading
import time

from .screen import AnsiScreen  # noqa: F401  (re-exported for callers)

_SPECIAL = {
    "Return": b"\r", "KP_Enter": b"\r", "BackSpace": b"\x7f", "Tab": b"\t", "Escape": b"\x1b",
    "Up": b"\x1b[A", "Down": b"\x1b[B", "Right": b"\x1b[C", "Left": b"\x1b[D",
    "Home": b"\x1b[H", "End": b"\x1b[F", "Delete": b"\x1b[3~",
}


def key_bytes(keysym, char="", ctrl=False):
    """Map a Tk key event to the bytes a terminal would send, or None to ignore the key."""
    if keysym in _SPECIAL:
        return _SPECIAL[keysym]
    if ctrl:
        if len(keysym) == 1 and keysym.isalpha():
            return bytes([ord(keysym.lower()) - 96])        # Ctrl-A..Z -> 0x01..0x1a
        if char and 0 < ord(char[0]) < 32:
            return char[0].encode()
        return None
    if char and char.isprintable():
        return char.encode("utf-8")
    return None


def paste_bytes(text):
    """One line is typed as is; several lines use the REPL's paste mode (Ctrl-E ... Ctrl-D) so indentation survives."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
    if "\n" not in text:
        return text.encode("utf-8")
    return b"\x05" + text.replace("\n", "\r").encode("utf-8") + b"\x04"


class TerminalSession:
    """Owns the serial line while terminal mode is on. Received bytes go to on_data(bytes) from a reader thread."""

    def __init__(self, repl, on_data, on_error=None):
        self.repl, self.on_data, self.on_error = repl, on_data, on_error
        self._stop = threading.Event()
        self._thread = None

    @property
    def active(self):
        return self._thread is not None

    def start(self):
        self.repl.begin_terminal()
        self._stop.clear()
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    def _read(self):
        ser = self.repl.ser
        while not self._stop.is_set():
            try:
                data = ser.read(ser.in_waiting or 1)
            except Exception as e:                          # unplugged, port closed...
                self._fail(e)
                return
            if data:
                self.on_data(data)
            else:
                time.sleep(0.002)

    def _fail(self, exc):
        if self.on_error and not self._stop.is_set():
            self.on_error(exc)

    def send(self, data):
        try:
            self.repl.ser.write(data)
        except Exception as e:
            self._fail(e)

    def stop(self):
        """Leave terminal mode. The next file operation re-enters the raw REPL; board state is untouched."""
        self._stop.set()
        if self._thread:
            self._thread.join(1.0)
            self._thread = None
        self.repl.end_terminal()
