"""The same behaviours against a real MicroPython (unix port) instead of the fake board.

Run locally with:  MICROPYTHON=/path/to/micropython pytest tests/test_real_micropython.py -rsP
Without a binary these are skipped. They exist to catch places where the fake board and real firmware disagree.

Two fixtures are used (see conftest.py and rawrepl_shim.py): the unix port has no raw REPL, so file/console/sync tests
run real MicroPython behind a small raw-REPL shim, while terminal tests use the unix port's own friendly REPL.
"""
import hashlib
import os
import threading
import time

import pytest

from mpexplorer.backends import CHUNK, DeviceBackend, LocalBackend
from mpexplorer.console import ConsoleSession
from mpexplorer.ops import ConflictPolicy, Progress, copy_tree, delete_tree, move_item
from mpexplorer.sync import apply_plan, build_plan
from mpexplorer.terminal import paste_bytes
from mpexplorer.transport import DeviceError, RawREPL
from test_terminal import DOWN, LEFT, UP, Term


@pytest.fixture
def repl(mpy_shim_serial):
    r = RawREPL(mpy_shim_serial)
    r.enter_raw()
    return r


@pytest.fixture
def dev(repl, tmp_path):
    return DeviceBackend(repl), str(tmp_path)


def wait_for(cond, secs=5.0):
    end = time.monotonic() + secs
    while not cond():
        assert time.monotonic() < end, "timed out"
        time.sleep(0.01)


def in_thread(fn):
    t = threading.Thread(target=fn, daemon=True)
    t.start()
    return t


# ----------------------------------------------------------------- transport
def test_exec_state_and_errors(repl):
    assert repl.exec("x = 6*7\nprint(x)").strip() == b"42"
    assert repl.exec("print(x + 1)").strip() == b"43"          # globals persist between executions
    with pytest.raises(DeviceError) as e:
        repl.exec("1/0")
    assert "ZeroDivisionError" in str(e.value)
    assert repl.exec("print('alive')").strip() == b"alive"


def test_falls_back_when_raw_paste_is_unavailable(repl):
    assert repl.exec("print(1)").strip() == b"1"
    assert repl.paste is False                                  # the shim answers "not supported"
    assert repl.exec("print(2)").strip() == b"2"


def test_large_code_survives_flow_control(repl):
    blob = "x = %r\nprint(len(x))" % ("a" * 20000)
    assert repl.exec(blob, timeout=20).strip() == b"20000"


def test_stdout_streams_while_running(repl):
    chunks = []
    repl.exec("import time\nprint('one')\ntime.sleep(0.3)\nprint('two')", 10, chunks.append)
    assert b"".join(chunks).replace(b"\r", b"") == b"one\ntwo\n" and len(chunks) >= 2


# ------------------------------------------------------------------ backends
def test_listdir_stat_and_errors(dev, tmp_path):
    d, root = dev
    (tmp_path / "a.txt").write_bytes(b"hello")
    (tmp_path / "sub").mkdir()
    entries = {e.name: e for e in d.listdir(root)}
    assert set(entries) == {"a.txt", "sub"}
    assert entries["a.txt"].size == 5 and entries["sub"].is_dir and entries["a.txt"].mtime > 1_000_000_000
    assert d.stat(root + "/nope") is None and d.stat(root + "/sub").is_dir
    with pytest.raises(DeviceError):
        d.listdir(root + "/missing")
    assert d.stat(root + "/a.txt").size == 5                   # still usable after an error


def test_binary_roundtrip(dev, tmp_path):
    d, root = dev
    local = LocalBackend()
    for size in (0, 1, CHUNK - 1, CHUNK, CHUNK * 3 + 7):
        data = os.urandom(size)
        src = tmp_path / "src.bin"
        src.write_bytes(data)
        remote = d.join(root, "r%d.bin" % size)
        copy_tree(local, str(src), d, remote, Progress(), ConflictPolicy())
        back = tmp_path / ("back%d.bin" % size)
        copy_tree(d, remote, local, str(back), Progress(), ConflictPolicy())
        assert back.read_bytes() == data


def test_tree_ops_on_device(dev, tmp_path):
    d, root = dev
    local = LocalBackend()
    tree = tmp_path / "t"
    (tree / "x" / "y").mkdir(parents=True)
    (tree / "x" / "y" / "f.py").write_text("print(1)")
    (tree / "top.txt").write_text("top")
    copy_tree(local, str(tree), d, d.join(root, "copied"), Progress(), ConflictPolicy())
    copy_tree(d, d.join(root, "copied"), d, d.join(root, "dup"), Progress(), ConflictPolicy())   # on-device copy
    assert (tmp_path / "dup" / "x" / "y" / "f.py").read_text() == "print(1)"
    move_item(d, d.join(root, "dup"), d, d.join(root, "moved"), Progress(), ConflictPolicy())
    assert (tmp_path / "moved").exists() and not (tmp_path / "dup").exists()
    delete_tree(d, d.join(root, "moved"), Progress())
    assert not (tmp_path / "moved").exists()
    d.rename(d.join(root, "copied"), d.join(root, "renamed"))
    assert (tmp_path / "renamed").exists()


def test_checksum_free_space_and_run(dev, tmp_path):
    d, root = dev
    (tmp_path / "x").write_bytes(b"hello" * 1000)
    (tmp_path / "s.py").write_text("print('hi')")
    assert d.run(root + "/s.py").replace("\r", "").strip() == "hi"
    digest = d.checksum(root + "/x")
    if digest is None:
        pytest.skip("this MicroPython build has no hashlib")
    assert digest == hashlib.sha256(b"hello" * 1000).hexdigest()
    try:
        assert d.free_space(root) > 0
    except DeviceError:
        pytest.skip("this MicroPython build has no os.statvfs")


def test_sync_end_to_end(dev, tmp_path):
    d, _root = dev
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    local = LocalBackend()
    (src / "a.py").write_text("print(1)")
    (src / "lib").mkdir()
    (src / "lib" / "m.py").write_text("x=1")
    plan = build_plan(local, str(src), d, str(dst))
    assert sorted((a.kind, a.rel) for a in plan.actions) == [("copy", "a.py"), ("copy", "lib/m.py"), ("mkdir", "lib")]
    apply_plan(plan, Progress())
    assert (dst / "lib" / "m.py").read_text() == "x=1"
    again = build_plan(local, str(src), d, str(dst))
    assert again.actions == []
    (src / "a.py").write_text("print(2)")                      # same size, different content
    plan = build_plan(local, str(src), d, str(dst))
    if plan.notes:
        pytest.skip("no hashlib on this build, so same-size changes cannot be detected")
    assert [a.rel for a in plan.of("copy")] == ["a.py"]


# ------------------------------------------------------------------- console
def run_line(session, line):
    out = []
    ok = session.run(line, lambda t, e: out.append((t, e)))
    return ok, "".join(t for t, _e in out), any(e for _t, e in out)


def test_console_matches_the_real_repl(repl):
    s = ConsoleSession(repl)
    assert run_line(s, "1+2") == (True, "3\n", False)
    assert run_line(s, "x = 40")[:2] == (True, "")
    assert run_line(s, "x + 2")[1] == "42\n"
    assert run_line(s, "_ + 1")[1] == "43\n"                    # _ holds the last result
    assert run_line(s, "for i in range(2):\n    print(i)")[1] == "0\n1\n"
    ok, text, is_err = run_line(s, "nope")
    assert not ok and is_err and "NameError" in text and "<stdin>" not in text
    assert run_line(s, "2*3")[1] == "6\n"


def test_console_input_with_real_stdin(repl):
    s = ConsoleSession(repl)
    out = []
    done = threading.Event()

    def work():
        s.run("n = input('Name? ')\nprint('Hi', n)", lambda t, e: out.append(t))
        done.set()
    in_thread(work)
    wait_for(lambda: "Name? " in "".join(out))                  # the prompt streams out before the answer is sent
    s.send_input("Bob\x03\x04")                                 # control characters must not reach the program
    assert done.wait(5)
    text = "".join(out).replace("\r", "")
    print("observed input() output:", repr(text))               # shows whether the board echoes typed text
    assert text.startswith("Name? ") and text.endswith("Hi Bob\n")
    assert run_line(s, "1+1")[1] == "2\n"                       # nothing leaked into the next run


def test_console_interrupt_stops_a_busy_loop(repl):
    s = ConsoleSession(repl)
    result = []
    out = []
    # a loop that calls into the runtime: very old builds only look for Ctrl-C at calls, not in a bare `while: pass`
    code = "import time\nwhile True:\n    time.sleep(0.01)"
    t = in_thread(lambda: result.append(s.run(code, lambda tx, e: out.append(tx))))
    time.sleep(0.5)
    s.interrupt()
    t.join(10)
    assert not t.is_alive() and result == [False] and "KeyboardInterrupt" in "".join(out)
    assert run_line(s, "1+1")[1] == "2\n"


# ------------------------------------------------------------------ terminal
@pytest.fixture
def term(mpy_serial):
    t = Term(mpy_serial)
    t.session.start()
    t.wait_end(">>> ")
    yield t
    t.session.stop()


def test_terminal_basic_use(term):
    assert b"MicroPython" in term.ser.started
    term.type("1+1\r")
    term.wait("2\n>>> ")
    term.type("print('hi')\r")
    term.wait("hi\n>>> ")


def test_terminal_mid_line_edit_redraw_matches_real_escape_codes(term):
    term.type("print(13)", LEFT, LEFT, b"\x7f", "2")
    term.wait(">>> print(23)")
    assert term.screen.lines[term.screen.row] == ">>> print(23)" and term.screen.col == len(">>> print(2")
    term.type("\r")
    term.wait("23\n>>> ")


def test_terminal_history_and_ctrl_c(term):
    term.type("a = 5\r")
    term.wait_end(">>> ")
    term.type(UP)
    term.wait(">>> a = 5")
    term.type(b"\x03")                                           # Ctrl-C abandons the recalled line
    term.wait_end(">>> ")
    term.type(UP, DOWN)                                          # up then down returns to an empty line
    term.type("a\r")
    term.wait("5\n>>> ")


def test_terminal_paste_mode(term):
    term.type(paste_bytes("def f(x):\n    return x * 2\nprint(f(21))\n"))
    term.wait("42\n>>> ")


def test_benchmark_script_runs_on_real_micropython(tmp_path):
    """tools/bench.py end to end (small sizes) on the real interpreter: ticks_ms, os.uname, gc.mem_free, file I/O."""
    import importlib.util
    from pathlib import Path

    from conftest import SHIM, micropython_binary
    from ptyserial import PtySerial

    spec = importlib.util.spec_from_file_location("bench", Path(__file__).parent.parent / "tools" / "bench.py")
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)
    exe, sers = micropython_binary(), []

    def make(paste):
        ser = PtySerial([exe, SHIM], cwd=str(tmp_path), banner=b"READY\r\n")
        sers.append(ser)
        return RawREPL(ser, paste=paste)
    try:
        info, rows = bench.run_benchmark(make, [4, 8], 10, root=str(tmp_path / "bench"), log=lambda s: None,
                                         reset=False)
    finally:
        for ser in sers:
            ser.close()
    assert info["system"] and {r["mode"] for r in rows} == {"plain"}      # the shim has no raw-paste
    assert {"upload 4 KB", "download 8 KB", "list 10 files"} <= {r["test"] for r in rows}
    assert not (tmp_path / "bench").exists()
