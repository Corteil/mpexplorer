"""A serial-port look-alike around a real program on a pseudo-terminal (used to drive MicroPython's unix port).

The child gets its own session with the pty as controlling terminal, so Ctrl-C (0x03) is turned into SIGINT by the
tty exactly as when a person types it, which is how the unix port implements KeyboardInterrupt.
"""
import errno
import fcntl
import os
import pty
import select
import signal
import struct
import subprocess
import termios
import time


class PtySerial:
    def __init__(self, argv, cwd=None, banner=b">>> ", banner_timeout=10.0):
        master, slave = pty.openpty()
        attrs = termios.tcgetattr(slave)            # like a serial line: bytes arrive at once, no echo, CR stays CR
        attrs[0] &= ~(termios.ICRNL | termios.INLCR | termios.IGNCR | termios.IXON)
        attrs[3] &= ~(termios.ECHO | termios.ICANON | termios.IEXTEN)   # ISIG stays on: Ctrl-C becomes SIGINT
        attrs[6][termios.VMIN], attrs[6][termios.VTIME] = 1, 0
        termios.tcsetattr(slave, termios.TCSANOW, attrs)
        self.proc = subprocess.Popen(
            argv, stdin=slave, stdout=slave, stderr=slave, cwd=cwd, close_fds=True, start_new_session=True,
            env={"PATH": os.environ.get("PATH", ""), "HOME": cwd or "/tmp", "TERM": "dumb"},
            preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0))
        os.close(slave)
        self.fd = master
        self.closed = False
        self.started = self._wait_for(banner, banner_timeout) if banner else b""

    def _wait_for(self, text, timeout):
        """Read until text appears (the REPL banner); returns everything read."""
        buf, end = b"", time.monotonic() + timeout
        while text not in buf:
            if time.monotonic() > end:
                raise TimeoutError("no %r from the program, got %r" % (text, buf[-200:]))
            buf += self.read(256)
        return buf

    @property
    def in_waiting(self):
        try:
            return struct.unpack("i", fcntl.ioctl(self.fd, termios.FIONREAD, b"\0\0\0\0"))[0]
        except OSError:
            return 0

    def read(self, n=1):
        try:
            if not select.select([self.fd], [], [], 0.05)[0]:
                return b""
            return os.read(self.fd, n)
        except OSError as e:
            if e.errno == errno.EIO:        # the program exited and the pty hung up
                return b""
            raise

    def write(self, data):
        view = memoryview(bytes(data))
        while view:
            view = view[os.write(self.fd, view):]
        return len(data)

    def alive(self):
        return self.proc.poll() is None

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except OSError:
                self.proc.kill()
        self.proc.wait()
        os.close(self.fd)
