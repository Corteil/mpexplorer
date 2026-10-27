"""MicroPython raw-REPL transport.

Nothing is installed on the device: every operation is a snippet of Python sent
to the stock REPL (Ctrl-A raw mode) and executed there, as `mpremote` does.
"""
import struct
import threading
import time

from .errors import BackendError

WRITE_BLOCK = 256       # bytes per serial write; the REPL has a small input buffer
WRITE_PAUSE = 0.005     # seconds between blocks


class TransportError(BackendError):
    pass


class DeviceError(TransportError):
    """The device raised an exception; .traceback holds its full text."""

    def __init__(self, tb):
        lines = tb.strip().splitlines()
        super().__init__(lines[-1] if lines else "device error")
        self.traceback = tb


class RawREPL:
    def __init__(self, ser, paste=None):
        """paste: None = use raw-paste if the firmware supports it, False = never, True = same as None."""
        self.ser = ser
        self.raw = False
        self.paste = None if paste in (None, True) else False     # None = not yet probed
        self._rx = bytearray()
        self.terminal = False   # True while a terminal owns the line; exec() is refused
        self.trace = None       # optional callable(kind, text): "send" | "recv" | "error" | "info"
        self.lock = threading.RLock()

    @classmethod
    def open(cls, port, baud=115200):
        import serial
        ser = serial.serial_for_url(port, baudrate=baud, timeout=0.1, do_not_open=True)
        ser.dtr = ser.rts = False       # opening with DTR/RTS asserted resets ESP32-S3 boards
        ser.open()
        return cls(ser)

    def _trace(self, kind, text):
        if self.trace:
            try:
                self.trace(kind, text)
            except Exception:
                pass            # a broken observer must never break the connection

    def _read_until(self, ending, timeout):
        """Consume input up to and including `ending`; anything after it stays buffered."""
        end = time.monotonic() + timeout
        while True:
            i = self._rx.find(ending)
            if i >= 0:
                out = bytes(self._rx[:i + len(ending)])
                del self._rx[:i + len(ending)]
                return out
            if time.monotonic() >= end:
                raise TransportError("timeout waiting for %r (got %r)" % (ending, bytes(self._rx[-80:])))
            chunk = self.ser.read(self.ser.in_waiting or 1)
            if chunk:
                self._rx += chunk
            else:
                time.sleep(0.001)

    def _drain(self):
        self._rx.clear()
        while self.ser.in_waiting:
            self.ser.read(self.ser.in_waiting)

    def enter_raw(self):
        with self.lock:
            for _ in range(3):
                self.ser.write(b"\r\x03\x03")   # interrupt any running program
                time.sleep(0.1)
                self._drain()
                self.ser.write(b"\r\x01")       # Ctrl-A: raw REPL
                try:
                    self._read_until(b"raw REPL; CTRL-B to exit\r\n>", 2)
                except TransportError:
                    continue
                self.raw = True
                self._trace("info", "entered raw REPL")
                return
            raise TransportError("could not enter the raw REPL (wrong port, or the port is busy?)")

    def interrupt(self):
        """Send Ctrl-C to stop running code. Safe to call from another thread while exec() waits."""
        self._trace("info", "Ctrl-C")
        self.ser.write(b"\x03")

    def send_input(self, text):
        """Type a line into the running program's stdin (what input() reads).

        Control characters are dropped so pasted text can never act as Ctrl-C/Ctrl-D.
        Safe to call from another thread while exec() waits.
        """
        text = "".join(ch for ch in text if ch == "\t" or ch >= " ")
        self._trace("info", "stdin: %s" % text)
        self.ser.write(text.encode() + b"\n")

    def begin_terminal(self):
        """Hand the line to a terminal: leave raw mode so the board shows its normal REPL prompt."""
        with self.lock:
            self.ser.write(b"\x02" if self.raw else b"\r")
            self.raw = False
            self.terminal = True
            self._rx.clear()
            self._trace("info", "terminal mode on")

    def end_terminal(self):
        """Take the line back. Raw mode is re-entered (with Ctrl-C) by the next exec(); board state is untouched."""
        with self.lock:
            self.terminal = False
            self.raw = False
            self._trace("info", "terminal mode off")

    def mark_dirty(self):
        """Force a clean re-entry of raw mode before the next exec (discards stray stdin bytes)."""
        self.raw = False

    def exec(self, code, timeout=10.0, on_output=None):
        """Run code on the device and return its stdout as bytes.

        on_output(chunk) is called with stdout as it arrives. timeout=None waits indefinitely.
        """
        with self.lock:
            if self.terminal:
                raise TransportError("terminal mode is active; switch the console back to emulated mode first")
            if not self.raw:
                self.enter_raw()
            data = code.encode()
            self._trace("send", code)
            ctl = 10.0 if timeout is None else timeout      # handshake steps always have a time limit
            try:
                if not (self.paste is not False and self._send_paste(data, ctl)):
                    for i in range(0, len(data), WRITE_BLOCK):
                        self.ser.write(data[i:i + WRITE_BLOCK])
                        time.sleep(WRITE_PAUSE)
                    self.ser.write(b"\x04")
                    self._read_until(b"OK", ctl)
                out = self._read_stream(timeout, on_output)
                err = self._read_until(b"\x04", ctl)[:-1]
                try:
                    self._read_until(b">", 1)
                except TransportError:
                    pass
            except TransportError as e:
                self._trace("error", str(e))
                self.raw = False                # resync on next call
                self.ser.write(b"\x03\x03")
                raise
            if out:
                self._trace("recv", out.decode(errors="replace"))
            if err:
                self._trace("error", err.decode(errors="replace"))
                raise DeviceError(err.decode(errors="replace"))
            return out

    def _read_stream(self, timeout, on_output):
        """Read stdout up to the 0x04 terminator, handing chunks to on_output as they arrive."""
        out = bytearray()
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            i = self._rx.find(b"\x04")
            n = i if i >= 0 else len(self._rx)
            if n:
                chunk = bytes(self._rx[:n])
                del self._rx[:n]
                out += chunk
                if on_output:
                    on_output(chunk)
            if i >= 0:
                del self._rx[:1]
                return bytes(out)
            if deadline is not None and time.monotonic() >= deadline:
                raise TransportError("timeout waiting for program output (got %r)" % bytes(out[-200:]))
            data = self.ser.read(self.ser.in_waiting or 1)
            if data:
                self._rx += data
            else:
                time.sleep(0.001)

    def _read_n(self, n, timeout):
        end = time.monotonic() + timeout
        while len(self._rx) < n:
            if time.monotonic() >= end:
                raise TransportError("timeout reading %d byte(s) (got %r)" % (n, bytes(self._rx)))
            chunk = self.ser.read(self.ser.in_waiting or 1)
            if chunk:
                self._rx += chunk
            else:
                time.sleep(0.001)
        out = bytes(self._rx[:n])
        del self._rx[:n]
        return out

    def _send_paste(self, data, timeout):
        """Send code with raw-paste flow control (MicroPython >= 1.14).

        Returns False, with the REPL still ready for a plain raw-mode upload, if the firmware
        lacks raw-paste. Unlike plain raw mode there are no per-block pauses: the device tells
        us how many more bytes it can take.
        """
        self.ser.write(b"\x05A\x01")
        head = self._read_n(2, timeout)
        if head == b"R\x01":
            self.paste = True
            window = struct.unpack("<H", self._read_n(2, timeout))[0]
            self._trace("info", "raw-paste, window %d" % window)
            remain, i = window, 0
            while i < len(data):
                while remain == 0 or self.ser.in_waiting or self._rx:
                    b = self._read_n(1, timeout)
                    if b == b"\x01":
                        remain += window
                    elif b == b"\x04":
                        self.ser.write(b"\x04")
                        raise TransportError("device aborted the raw-paste upload")
                    else:
                        raise TransportError("unexpected byte %r during raw-paste" % b)
                n = min(remain, len(data) - i)
                self.ser.write(data[i:i + n])
                i += n
                remain -= n
            self.ser.write(b"\x04")
            self._read_until(b"\x04", timeout)         # device acknowledges end of input
            return True
        self.paste = False
        self._trace("info", "raw-paste not available, using plain raw REPL")
        if head != b"R\x00":       # old firmware: it echoed the start of the raw REPL banner instead
            self._read_until(b"w REPL; CTRL-B to exit\r\n>", timeout)
        return False

    def soft_reset(self):
        with self.lock:
            self._trace("info", "soft reset")
            self.ser.write(b"\x02\x04")         # leave raw mode, Ctrl-D
            self.raw = False
            time.sleep(0.5)

    def close(self):
        with self.lock:
            try:
                self.ser.write(b"\x02")
            except Exception:
                pass
            self.ser.close()
