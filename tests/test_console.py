import pytest

from fakedevice import FakeSerial
from mpexplorer.backends import DeviceBackend, LocalBackend
from mpexplorer.console import ConsoleSession, build_code, clean_traceback
from mpexplorer.transport import RawREPL


@pytest.fixture(params=["on", "old"])
def repl(request):
    return RawREPL(FakeSerial(paste=request.param))


@pytest.fixture
def env(repl, tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    return LocalBackend(), DeviceBackend(repl), src, dst


def test_checksum_matches_both_sides(env):
    local, dev, src, _ = env
    (src / "x").write_bytes(b"hello" * 1000)
    assert local.checksum(str(src / "x")) == dev.checksum(str(src / "x"))


def test_console_expressions_statements_state_and_errors(repl):
    out = []
    s = ConsoleSession(repl)

    def run(line):
        out.clear()
        ok = s.run(line, lambda t, err: out.append((t, err)))
        return ok, "".join(t for t, _e in out), any(e for _t, e in out)
    assert run("1+2") == (True, "3\n", False)
    assert run("x = 40") == (True, "", False)
    assert run("x + 2") == (True, "42\n", False)                 # state persists between lines
    assert run("print('hi')") == (True, "hi\n", False)
    assert run("for i in range(2):\n    print(i)") == (True, "0\n1\n", False)
    ok, text, is_err = run("nope")
    assert not ok and is_err and "NameError" in text and "<stdin>" not in text
    assert run("2*3")[1] == "6\n"                                # the session survives an error
    assert s.history[:3] == ["1+2", "x = 40", "x + 2"]


def test_console_helpers_and_interrupt(repl):
    assert build_code("1+1").startswith("_v=eval(")
    assert build_code("x=1") == "x=1\n"
    tb = 'Traceback (most recent call last):\n  File "<stdin>", line 1, in <module>\nNameError: bad name\n'
    assert clean_traceback(tb) == "NameError: bad name\n"
    ConsoleSession(repl).interrupt()
    assert repl.ser._buf == bytearray()                          # Ctrl-C reached the device and cleared input


def test_exec_streams_output_and_allows_no_timeout(repl):
    chunks = []
    assert repl.exec("print('abc')", None, chunks.append).strip() == b"abc"
    assert b"".join(chunks).strip() == b"abc"


def _run_in_thread(fn):
    import threading
    t = threading.Thread(target=fn, daemon=True)
    t.start()
    return t


def _wait(cond, secs=3.0):
    import time
    end = time.monotonic() + secs
    while not cond():
        assert time.monotonic() < end, "timed out"
        time.sleep(0.005)


def test_console_underscore_holds_last_result(repl):
    s = ConsoleSession(repl)
    out = []
    s.run("6*7", lambda t, e: out.append(t))
    s.run("_ + 1", lambda t, e: out.append(t))
    assert "".join(out) == "42\n43\n"


def test_console_soft_reset_clears_state(repl):
    s = ConsoleSession(repl)
    out = []
    emit = lambda t, e: out.append((t, e))          # noqa: E731
    s.run("x = 5", emit)
    s.soft_reset(emit)
    assert out[-1][0].startswith("MPY: soft reboot") and out[-1][1] is False and repl.ser.resets == 1
    assert not out[-1][0].rstrip().endswith(">>>")      # the prompt it ends on is left out
    assert s.run("x", emit) is False                  # variables are gone, console still works
    assert s.run("1+1", emit) is True


def test_console_input_reaches_running_program(repl):
    ser = repl.ser
    s = ConsoleSession(repl)
    out = []
    t = _run_in_thread(lambda: s.run("n = input('Name? ')\nprint('Hi', n)", lambda tx, e: out.append(tx)))
    _wait(ser.waiting_input.is_set)
    s.send_input("Bob\x04\x03")                       # control characters are stripped
    t.join(3)
    assert "".join(out) == "Name? Bob\nHi Bob\n"            # the board echoes what input() reads


def test_unread_input_does_not_leak_into_next_run(repl):
    ser = repl.ser
    s = ConsoleSession(repl)
    out = []
    emit = lambda t, e: out.append((t, e))          # noqa: E731
    t = _run_in_thread(lambda: s.run("import time\ntime.sleep(0.3)", emit))
    _wait(lambda: ser.running)
    s.send_input("junk")                              # the program never reads it
    t.join(3)
    out.clear()
    assert s.run("1+1", emit) is True and out == [("2\n", False)]


def test_trace_reports_everything_sent_and_received():
    repl = RawREPL(FakeSerial(paste="on"))
    events = []
    repl.trace = lambda kind, text: events.append((kind, text))
    repl.exec("print('hi')")
    with pytest.raises(Exception):
        repl.exec("1/0")
    kinds = [k for k, _t in events]
    assert ("send", "print('hi')") in events and ("recv", "hi\n") in [(k, t.replace("\r", "")) for k, t in events]
    assert "error" in kinds and "info" in kinds
    repl.trace = lambda k, t: 1 / 0                   # a broken observer is ignored
    assert repl.exec("print(1)").strip() == b"1"


class BootSerial:
    """Just enough of a port to watch a soft reset: after Ctrl-D it prints what a CircuitPython board does."""

    def __init__(self, reply):
        self.reply, self.out, self.written, self.in_waiting = reply, bytearray(), [], 0

    def write(self, data):
        self.written.append(bytes(data))
        if data == b"\x04":
            self.out += self.reply
            self.in_waiting = len(self.out)

    def read(self, n=1):
        chunk, self.out = bytes(self.out[:n]), self.out[n:]
        self.in_waiting = len(self.out)
        return chunk

    def flush(self):
        pass


def test_soft_reset_shows_what_the_board_prints_while_starting():
    from mpexplorer.transport import RawREPL
    ser = BootSerial(b"soft reboot\r\n\r\nAuto-reload is on.\r\nHello World!\r\n\r\nCode done running.\r\n\r\n"
                     b"Press any key to enter the REPL. Use CTRL-D to reload.\r\n")
    out = []
    ConsoleSession(RawREPL(ser)).soft_reset(lambda t, e: out.append(t))
    text = "".join(out)
    assert "Hello World!" in text and "Code done running." in text and "\r" not in text
    assert ser.written[0] == b"\x02" and ser.written[-1] == b"\x04"


def test_soft_reset_of_a_silent_board_still_says_so():
    from mpexplorer.transport import RawREPL
    out = []
    ConsoleSession(RawREPL(BootSerial(b""))).soft_reset(lambda t, e: out.append(t))
    assert out == ["MPY: soft reboot\n"]


def test_terminal_codes_are_removed_from_console_output_even_when_split():
    from mpexplorer.console import CodeStripper
    boot = ("\x1b]0;\U0001f40dcode.py | 10.3.1\x1b\\Hello \x1b[31mWorld\x1b[0m!\n\x1b[0K\x07\x00ok\t"
            "\x1b]0;\U0001f40dDone | 10.3.1\x1b\\")
    assert CodeStripper().feed(boot) == "Hello World!\nok\t"
    for cut in range(1, len(boot)):                      # every way of splitting it in two
        s = CodeStripper()
        assert s.feed(boot[:cut]) + s.feed(boot[cut:]) == "Hello World!\nok\t", cut
    s = CodeStripper()
    assert s.feed("text \x1b") + s.feed("[1;32mgreen") == "text green"


def test_soft_reset_output_has_no_terminal_codes():
    from mpexplorer.transport import RawREPL
    ser = BootSerial(b"\x1b]0;\xf0\x9f\x90\x8dcode.py | 10.3.1\x1b\\soft reboot\r\nHello World!\r\n"
                     b"\x1b]0;\xf0\x9f\x90\x8dDone | 10.3.1\x1b\\Press any key to enter the REPL.\r\n")
    out = []
    ConsoleSession(RawREPL(ser)).soft_reset(lambda t, e: out.append(t))
    text = "".join(out)
    assert "Hello World!" in text and "\x1b" not in text and "10.3.1" not in text
