import os

import pytest

from fakedevice import FakeSerial
from mpexplorer.backends import CHUNK, DeviceBackend, LocalBackend
from mpexplorer.errors import BackendError, Cancelled
from mpexplorer.jobs import JobRunner
from mpexplorer.ops import (ConflictPolicy, Progress, copy_tree, delete_tree, is_within,
                            move_item, total_size, unique_name)
from mpexplorer.transport import DeviceError, RawREPL


@pytest.fixture(params=["on", "off", "old"])
def dev(request, tmp_path):
    return DeviceBackend(RawREPL(FakeSerial(paste=request.param))), str(tmp_path)


@pytest.fixture
def local():
    return LocalBackend()


def test_listdir_and_stat(dev, tmp_path):
    d, root = dev
    (tmp_path / "a.txt").write_bytes(b"hello")
    (tmp_path / "sub").mkdir()
    entries = {e.name: e for e in d.listdir(root)}
    assert entries["a.txt"].size == 5 and not entries["a.txt"].is_dir
    assert entries["sub"].is_dir
    assert d.stat(root + "/nope") is None
    assert d.stat(root + "/sub").is_dir


def test_listdir_mtimes_only_when_asked(dev, tmp_path):
    d, root = dev
    (tmp_path / "a.txt").write_bytes(b"hello")
    (tmp_path / "sub").mkdir()
    fast = {e.name: e for e in d.listdir(root)}
    assert fast["a.txt"].size == 5 and fast["a.txt"].mtime == 0 and fast["sub"].is_dir
    full = {e.name: e for e in d.listdir(root, mtimes=True)}
    assert full["a.txt"].size == 5 and full["a.txt"].mtime > 1_000_000_000 and full["sub"].mtime > 1_000_000_000


def test_roundtrip_odd_sizes(dev, local, tmp_path):
    d, root = dev
    for size in (0, 1, CHUNK - 1, CHUNK, CHUNK * 3 + 7):
        data = os.urandom(size)
        src = tmp_path / "src.bin"
        src.write_bytes(data)
        remote = d.join(root, "r%d.bin" % size)
        copy_tree(local, str(src), d, remote, Progress(), ConflictPolicy())
        back = tmp_path / ("back%d.bin" % size)
        copy_tree(d, remote, local, str(back), Progress(), ConflictPolicy())
        assert back.read_bytes() == data


def test_tree_copy_move_delete(dev, local, tmp_path):
    d, root = dev
    tree = tmp_path / "t"
    (tree / "x" / "y").mkdir(parents=True)
    (tree / "x" / "y" / "f.py").write_text("print(1)")
    (tree / "top.txt").write_text("top")
    dst = d.join(root, "copied")
    p = Progress()
    p.total = total_size(local, str(tree))
    copy_tree(local, str(tree), d, dst, p, ConflictPolicy())
    assert p.done == p.total == 11
    assert (tmp_path / "copied" / "x" / "y" / "f.py").read_text() == "print(1)"
    # same-device copy uses the on-device fast path, move uses rename
    copy_tree(d, dst, d, d.join(root, "dup"), Progress(), ConflictPolicy())
    assert (tmp_path / "dup" / "top.txt").read_text() == "top"
    move_item(d, d.join(root, "dup"), d, d.join(root, "moved"), Progress(), ConflictPolicy())
    assert not (tmp_path / "dup").exists() and (tmp_path / "moved").exists()
    delete_tree(d, d.join(root, "moved"), Progress())
    assert not (tmp_path / "moved").exists()


def test_conflicts(dev, local, tmp_path):
    d, root = dev
    (tmp_path / "f.txt").write_text("old")
    src = tmp_path / "s"
    src.mkdir()
    (src / "f.txt").write_text("new")
    target = d.join(root, "f.txt")
    p = ConflictPolicy()                                   # default: skip
    copy_tree(local, str(src / "f.txt"), d, target, Progress(), p)
    assert (tmp_path / "f.txt").read_text() == "old" and p.skipped == 1
    copy_tree(local, str(src / "f.txt"), d, target, Progress(), ConflictPolicy(lambda n: ("overwrite", False)))
    assert (tmp_path / "f.txt").read_text() == "new"
    copy_tree(local, str(src / "f.txt"), d, target, Progress(), ConflictPolicy(lambda n: ("rename", True)))
    assert (tmp_path / "f (1).txt").exists()
    with pytest.raises(Cancelled):
        copy_tree(local, str(src / "f.txt"), d, target, Progress(), ConflictPolicy(lambda n: ("cancel", False)))


def test_move_keeps_source_when_skipped(dev, local, tmp_path):
    d, root = dev
    (tmp_path / "f.txt").write_text("keep")
    src = tmp_path / "s"
    src.mkdir()
    (src / "f.txt").write_text("other")
    move_item(local, str(src), d, root, Progress(), ConflictPolicy())
    assert (src / "f.txt").exists()                        # skipped, so not deleted


def test_cancel_removes_partial_file(dev, local, tmp_path):
    d, root = dev
    (tmp_path / "big.bin").write_bytes(os.urandom(CHUNK * 4))
    p = Progress()
    p.cancel()
    with pytest.raises(Cancelled):
        copy_tree(local, str(tmp_path / "big.bin"), d, d.join(root, "out.bin"), p, ConflictPolicy())
    assert not (tmp_path / "out.bin").exists()


def test_device_errors(dev):
    d, root = dev
    with pytest.raises(DeviceError) as e:
        d.listdir(root + "/missing")
    assert "FileNotFoundError" in str(e.value) or "OSError" in str(e.value)
    d.mkdir(root + "/ok")                                  # transport survives an error
    assert d.stat(root + "/ok").is_dir


def test_rename_run_and_epoch(dev, tmp_path):
    d, root = dev
    (tmp_path / "a").write_text("x")
    d.rename(root + "/a", root + "/b")
    assert (tmp_path / "b").exists()
    (tmp_path / "s.py").write_text("print('hi')")
    assert d.run(root + "/s.py").strip() == "hi"
    assert d.epoch_offset == 0
    assert d.free_space(root) > 0


def test_helpers(local, tmp_path):
    (tmp_path / "a.tar.gz").write_text("")
    assert unique_name(local, str(tmp_path / "a.tar.gz")).endswith("a.tar (1).gz")
    assert is_within(local, "/a/b/c", "/a/b") and not is_within(local, "/a/bc", "/a/b")
    with pytest.raises(BackendError):
        copy_tree(local, str(tmp_path / "gone"), local, str(tmp_path / "x"), Progress(), ConflictPolicy())


def test_job_runner():
    r = JobRunner()
    r.submit(lambda prog: 42)
    r.submit(lambda prog: 1 / 0)
    got = []
    import time
    for _ in range(100):
        got += r.drain()
        if len(got) == 2:
            break
        time.sleep(0.02)
    assert got[0][1] == 42 and isinstance(got[1][2], ZeroDivisionError)


@pytest.mark.parametrize("mode,expected", [("on", True), ("off", False), ("old", False)])
def test_paste_negotiation(mode, expected):
    ser = FakeSerial(paste=mode)
    repl = RawREPL(ser)
    assert repl.exec("print(1+1)").strip() == b"2"
    assert repl.paste is expected
    assert repl.exec("print(3)").strip() == b"3"           # still works after the probe
    assert (ser.paste_used == 2) is expected


def test_paste_flow_control_and_large_code():
    ser = FakeSerial(paste="on", window=32)
    repl = RawREPL(ser)
    blob = "x=%r\nprint(len(x))" % ("a" * 5000)
    assert repl.exec(blob).strip() == b"5000"
    assert ser.flow_violations == 0


def test_paste_can_be_disabled():
    ser = FakeSerial(paste="on")
    repl = RawREPL(ser, paste=False)
    assert repl.exec("print(7)").strip() == b"7"
    assert ser.paste_used == 0


def test_paste_reports_device_errors():
    repl = RawREPL(FakeSerial(paste="on"))
    with pytest.raises(DeviceError):
        repl.exec("1/0")
    assert repl.exec("print('ok')").strip() == b"ok"
