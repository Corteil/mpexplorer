import pytest

from fakedevice import FakeSerial
from mpexplorer.backends import DeviceBackend, LocalBackend
from mpexplorer.console import ConsoleSession, build_code, clean_traceback
from mpexplorer.errors import BackendError, Cancelled
from mpexplorer.ops import Progress
from mpexplorer.sync import apply_plan, build_plan
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


def run_sync(plan, delete=False):
    apply_plan(plan, Progress(), include_delete=delete)


def test_sync_new_changed_extra_and_idempotent(env):
    local, dev, src, dst = env
    (src / "a.py").write_text("print(1)")
    (src / "lib").mkdir()
    (src / "lib" / "m.py").write_text("x=1")
    (src / "__pycache__").mkdir()
    (src / "__pycache__" / "junk.pyc").write_text("zz")
    plan = build_plan(local, str(src), dev, str(dst))
    assert sorted((a.kind, a.rel) for a in plan.actions) == [
        ("copy", "a.py"), ("copy", "lib/m.py"), ("mkdir", "lib")]         # excluded names are skipped
    run_sync(plan)
    assert (dst / "lib" / "m.py").read_text() == "x=1" and not (dst / "__pycache__").exists()
    assert not list(dst.rglob("*.mpx-part"))

    again = build_plan(local, str(src), dev, str(dst))
    assert again.actions == [] and again.unchanged == 2

    (src / "a.py").write_text("print(2)")                                 # same size, different content
    (dst / "old.txt").write_text("stale")
    (dst / "olddir").mkdir()
    (dst / "olddir" / "f").write_text("q")
    plan = build_plan(local, str(src), dev, str(dst))
    assert [(a.kind, a.rel, a.reason) for a in plan.actions if a.kind == "copy"] == [("copy", "a.py", "changed")]
    run_sync(plan)                                                        # no delete: extras survive
    assert (dst / "a.py").read_text() == "print(2)" and (dst / "old.txt").exists()
    run_sync(plan, delete=True)
    assert not (dst / "old.txt").exists() and not (dst / "olddir").exists()


def test_sync_device_to_local_and_size_only(env):
    local, dev, src, dst = env
    (src / "f.bin").write_bytes(b"abcd")
    (dst / "f.bin").write_bytes(b"abce")
    assert build_plan(dev, str(src), local, str(dst), use_checksum=False).actions == []
    plan = build_plan(dev, str(src), local, str(dst))
    assert [a.rel for a in plan.of("copy")] == ["f.bin"]
    run_sync(plan)
    assert (dst / "f.bin").read_bytes() == b"abcd"


def test_sync_creates_missing_destination_and_detects_mismatch(env, tmp_path):
    local, dev, src, dst = env
    (src / "a").write_text("1")
    newdst = tmp_path / "fresh"
    plan = build_plan(local, str(src), dev, str(newdst))
    assert plan.actions[0].kind == "mkdir" and plan.actions[0].rel == ""
    run_sync(plan)
    assert (newdst / "a").read_text() == "1"
    (src / "thing").write_text("file")
    (dst / "thing").mkdir()
    with pytest.raises(BackendError):
        build_plan(local, str(src), dev, str(dst))


def test_sync_cancel_keeps_old_file(env):
    local, dev, src, dst = env
    (src / "a").write_bytes(b"new!")
    (dst / "a").write_bytes(b"old")
    plan = build_plan(local, str(src), dev, str(dst))
    prog = Progress()
    prog.cancel()
    with pytest.raises(Cancelled):
        apply_plan(plan, prog)
    assert (dst / "a").read_bytes() == b"old"


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
