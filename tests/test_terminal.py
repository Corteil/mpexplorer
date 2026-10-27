import threading
import time

import pytest

from fakedevice import FakeSerial
from mpexplorer.console import ConsoleSession
from mpexplorer.screen import AnsiScreen
from mpexplorer.terminal import TerminalSession, key_bytes, paste_bytes
from mpexplorer.transport import RawREPL, TransportError

UP, DOWN, LEFT, RIGHT = b"\x1b[A", b"\x1b[B", b"\x1b[D", b"\x1b[C"


# ---------------------------------------------------------------- AnsiScreen
def test_screen_basic_text_and_crlf():
    s = AnsiScreen()
    s.feed(b"hello\r\nworld")
    assert s.lines == ["hello", "world"] and (s.row, s.col) == (1, 5)


def test_screen_backspace_erase_and_overwrite():
    s = AnsiScreen()
    s.feed(b">>> abc")
    s.feed(b"\b\x1b[K")                          # what the REPL sends for Backspace at end of line
    assert s.text() == ">>> ab"
    s.feed(b"\r>>> X")                            # carriage return then overwrite
    assert s.text() == ">>> Xb"


def test_screen_cursor_moves_and_midline_edit():
    s = AnsiScreen()
    s.feed(b">>> abcd\x1b[2D")                    # cursor left twice
    assert s.col == 6
    s.feed(b"X\x1b[Kcd\x1b[2D")                   # insert redraw: char + erase + tail + back
    assert s.text() == ">>> abXcd" and s.col == 7


def test_screen_history_redraw():
    s = AnsiScreen()
    s.feed(b">>> old line")
    s.feed(b"\x1b[8D\x1b[Knew")                   # up-arrow: left by len, erase, new text (len 8 is the old text)
    assert s.text() == ">>> new"


def test_screen_erase_variants_and_delete_chars():
    s = AnsiScreen()
    s.feed(b"abcdef\x1b[3D\x1b[1K")               # erase from start to cursor
    assert s.text() == "    ef"
    s.feed(b"\x1b[2K")
    assert s.text() == ""
    s.feed(b"\rabcdef\r\x1b[2P")                  # delete two chars at cursor
    assert s.text() == "cdef"
    s.feed(b"\r\none\r\ntwo\x1b[A\x1b[J")           # cursor up one line, erase to end of screen
    assert s.lines == ["cdef", "one"] and s.row == 1 and s.col == 3   # "two" is gone


def test_screen_ignores_colours_title_and_private_modes():
    s = AnsiScreen()
    s.feed(b"\x1b[31mred\x1b[0m \x1b]0;title\x07ok\x1b[?25l\x1b[?2004h")
    assert s.text() == "red ok"


def test_screen_utf8_split_across_feeds_and_scrollback_limit():
    s = AnsiScreen(max_lines=3)
    s.feed(b"caf\xc3")
    s.feed(b"\xa9\r\n")
    assert s.lines[0] == "caf\u00e9"
    for i in range(10):
        s.feed(b"l%d\r\n" % i)
    assert len(s.lines) == 3 and s.lines[-2] == "l9" and s.row == len(s.lines) - 1


def test_screen_clear_all():
    s = AnsiScreen()
    s.feed(b"a\r\nb\x1b[2J")
    assert s.lines == [""] and (s.row, s.col) == (0, 0)


# ------------------------------------------------------------------ key map
def test_key_bytes():
    assert key_bytes("Return") == b"\r" and key_bytes("BackSpace") == b"\x7f"
    assert key_bytes("Up") == UP and key_bytes("Delete") == b"\x1b[3~"
    assert key_bytes("a", "a") == b"a" and key_bytes("eacute", "\u00e9") == "\u00e9".encode()
    assert key_bytes("c", "\x03", ctrl=True) == b"\x03" and key_bytes("D", "", ctrl=True) == b"\x04"
    assert key_bytes("Shift_L", "") is None and key_bytes("F5", "") is None


def test_paste_bytes_single_and_multiline():
    assert paste_bytes("x = 1\n") == b"x = 1"
    assert paste_bytes("for i in r:\n    print(i)\nz=1\n") == b"\x05for i in r:\r    print(i)\rz=1\x04"


# --------------------------------------------------- terminal over the fake
class Term:
    """A terminal session wired to a screen, like the Tk widget does."""

    def __init__(self, ser=None):
        self.ser = ser or FakeSerial()
        self.repl = RawREPL(self.ser)
        self.screen = AnsiScreen()
        self.lock = threading.Lock()
        self.errors = []
        self.session = TerminalSession(self.repl, self._data, self.errors.append)

    def _data(self, chunk):
        with self.lock:
            self.screen.feed(chunk)

    def text(self):
        with self.lock:
            return self.screen.text()

    def type(self, *chunks):
        for c in chunks:
            self.session.send(c if isinstance(c, bytes) else c.encode())

    def wait(self, needle, secs=3.0):
        end = time.monotonic() + secs
        while needle not in self.text():
            assert time.monotonic() < end, "timed out waiting for %r in:\n%s" % (needle, self.text())
            time.sleep(0.005)

    def wait_end(self, suffix, secs=3.0):
        end = time.monotonic() + secs
        while not self.text().endswith(suffix):
            assert time.monotonic() < end, "timed out waiting for %r at the end of:\n%s" % (suffix, self.text())
            time.sleep(0.005)


@pytest.fixture
def term():
    t = Term()
    t.session.start()
    yield t
    t.session.stop()


def test_terminal_shows_banner_and_runs_code(term):
    term.type("\r")
    term.wait(">>> ")
    term.type("1+1\r")
    term.wait("2\n>>> ")
    term.type("print('hi')\r")
    term.wait("hi\n>>> ")


def test_terminal_line_editing(term):
    term.type("\r")
    term.wait_end(">>> ")
    term.type("print(13)", LEFT, LEFT, b"\x7f", "2")      # mid-line delete and insert redraw the tail correctly
    term.wait(">>> print(23)")
    assert term.screen.col == len(">>> print(2") and term.screen.lines[term.screen.row] == ">>> print(23)"
    term.type("\r")
    term.wait("23\n>>> ")


def test_terminal_history_up_down(term):
    term.type("\r")
    term.wait(">>> ")
    term.type("a = 5\r")
    term.wait("a = 5\n>>> ")
    term.type("a + 1\r")
    term.wait("6\n>>> ")
    term.type(UP)
    term.wait(">>> a + 1")
    term.type(UP)
    term.wait(">>> a = 5")
    term.type(DOWN)
    term.wait(">>> a + 1")
    term.type("\r")
    term.wait("6\n>>> a + 1")


def test_terminal_tab_completion(term):
    term.type("\r")
    term.wait(">>> ")
    term.type("long_variable_name = 3\r")
    term.wait("\n>>> ")
    term.type("long_v", "\t")
    term.wait(">>> long_variable_name")
    term.type(b"\x03")                                   # Ctrl-C abandons the line
    term.wait("\n>>> ")


def test_terminal_multiline_block_and_paste_mode(term):
    term.type("\r")
    term.wait(">>> ")
    term.type("for i in range(2):\r", "    print(i)\r", "\r")
    term.wait("0\n1\n>>> ")
    term.type(paste_bytes("def f(x):\n    return x * 2\nprint(f(21))\n"))
    term.wait("print(f(21))")
    term.type(b"")                                       # the paste bytes already end with Ctrl-D
    term.wait("42\n>>> ")


def test_terminal_ctrl_d_soft_resets(term):
    term.type("\r")
    term.wait(">>> ")
    term.type("v = 1\r")
    term.wait("\n>>> ")
    term.type(b"\x04")
    term.wait("MPY: soft reboot")
    assert term.ser.resets == 1 and "v" not in term.ser.globals


def test_terminal_input_reaches_program(term):
    term.type("\r")
    term.wait(">>> ")
    term.type("n = input('Name? ')\r")
    end = time.monotonic() + 3
    while not term.ser.waiting_input.is_set():
        assert time.monotonic() < end
        time.sleep(0.005)
    term.type("Bob\r")
    term.wait("Bob\n")                                   # the board echoes what is typed
    term.wait_end(">>> ")
    assert term.ser.globals["n"] == "Bob"


# ------------------------------------------------------ switching between modes
def test_exec_is_refused_while_terminal_is_active(term):
    with pytest.raises(TransportError):
        term.repl.exec("print(1)")


def test_state_survives_switching_between_emulated_and_terminal():
    ser = FakeSerial()
    repl = RawREPL(ser)
    con = ConsoleSession(repl)
    out = []
    con.run("x = 5", lambda t, e: out.append(t))
    t = Term(ser)
    t.repl = repl                                         # same connection, as in the app
    t.session = TerminalSession(repl, t._data, t.errors.append)
    t.session.start()
    t.type("\r")
    t.wait(">>> ")
    t.type("x\r")
    t.wait("5\n>>> ")
    t.type("y = x + 1\r")
    t.wait("\n>>> ")
    t.session.stop()                                      # back to emulated mode
    out.clear()
    assert con.run("y", lambda tx, e: out.append(tx)) is True and "".join(out) == "6\n"
    assert repl.exec("print(x)").strip() == b"5"


def test_terminal_reader_reports_a_dead_port():
    class Dead(FakeSerial):
        def read(self, n=1):
            raise OSError("device disconnected")
    t = Term(Dead())
    t.session.start()
    end = time.monotonic() + 3
    while not t.errors:
        assert time.monotonic() < end
        time.sleep(0.005)
    t.session.stop()
    assert "disconnected" in str(t.errors[0])
