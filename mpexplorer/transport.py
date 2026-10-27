"""MicroPython raw-REPL transport.

Nothing is installed on the device: every operation is a snippet of Python sent
to the stock REPL (Ctrl-A raw mode) and executed there, as `mpremote` does.
"""
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
    def __init__(self, ser):
        self.ser = ser
        self.raw = False
        self._rx = bytearray()
        self.lock = threading.RLock()

    @classmethod
    def open(cls, port, baud=115200):
        import serial
        return cls(serial.serial_for_url(port, baudrate=baud, timeout=0.1))

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
                return
            raise TransportError("could not enter the raw REPL (wrong port, or the port is busy?)")

    def exec(self, code, timeout=10.0):
        """Run code on the device and return its stdout as bytes."""
        with self.lock:
            if not self.raw:
                self.enter_raw()
            data = code.encode()
            try:
                for i in range(0, len(data), WRITE_BLOCK):
                    self.ser.write(data[i:i + WRITE_BLOCK])
                    time.sleep(WRITE_PAUSE)
                self.ser.write(b"\x04")
                self._read_until(b"OK", timeout)
                out = self._read_until(b"\x04", timeout)[:-1]
                err = self._read_until(b"\x04", timeout)[:-1]
                try:
                    self._read_until(b">", 1)
                except TransportError:
                    pass
            except TransportError:
                self.raw = False                # resync on next call
                self.ser.write(b"\x03\x03")
                raise
            if err:
                raise DeviceError(err.decode(errors="replace"))
            return out

    def soft_reset(self):
        with self.lock:
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
