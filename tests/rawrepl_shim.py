# Run with a real MicroPython (unix port):  micropython tests/rawrepl_shim.py
#
# The unix port has no raw REPL of its own (Ctrl-A is ignored), so this ~50 line script speaks the raw-REPL framing
# in front of a real MicroPython interpreter. What it lets the tests check on real MicroPython is everything that runs
# *inside* the snippets: os.ilistdir/stat/rename, file I/O, memoryview/readinto, binascii, hashlib, json, exec
# semantics, tracebacks, input() and KeyboardInterrupt. The framing itself (OK / 0x04 / raw-paste) is only as real as
# this script, so it is exercised for real only on hardware.
import sys

try:
    import io
except ImportError:
    import uio as io

inp = sys.stdin.buffer
out = sys.stdout.buffer
# bare "\n": the pty (like a serial console driver) turns it into "\r\n", as on a board
BANNER = b"raw REPL; CTRL-B to exit\n>"


def write(data):
    out.write(data)
    if hasattr(out, "flush"):
        out.flush()


def run(src):
    write(b"OK")
    err = ""
    try:
        exec(src.decode(), g)
    except BaseException as e:             # includes KeyboardInterrupt from Ctrl-C
        sio = io.StringIO()
        sys.print_exception(e, sio)
        err = sio.getvalue()
    write(b"\x04" + err.encode() + b"\x04>")


g = {"__name__": "__main__"}
raw = False
buf = bytearray()
ready = False
while True:
    try:
        if not ready:                       # inside the try: a Ctrl-C right after this must not kill us
            ready = True
            write(b"READY\n")
        c = inp.read(1)
        if not c:
            break
        if c == b"\x01":                    # Ctrl-A: (re-)enter raw mode; discards any half-entered code
            raw = True
            buf = bytearray()
            write(BANNER)
        elif not raw:
            pass                            # no friendly REPL here
        elif c == b"\x02":
            raw = False
        elif c == b"\x03":                  # Ctrl-C while entering code abandons it
            buf = bytearray()
        elif c == b"\x05":                  # raw-paste request: answer "understood but not supported"
            inp.read(2)
            write(b"R\x00")
        elif c == b"\x04":
            if buf:
                run(bytes(buf))
                buf = bytearray()
        else:
            buf.extend(c)
    except KeyboardInterrupt:               # SIGINT from the tty (Ctrl-C) while idle
        buf = bytearray()
