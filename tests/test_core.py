import os
import sys
import time
import types

import pytest

import fakedisplay
from fakedevice import FakeSerial
from mpexplorer.backends import CHUNK, DeviceBackend, LocalBackend, human_duration
from mpexplorer.badgescreen import BadgeScreen, fit
from mpexplorer.errors import BackendError, Cancelled
from mpexplorer.jobs import JobRunner
from mpexplorer.ops import (ConflictPolicy, Progress, copy_tree, delete_tree, is_within,
                            move_item, total_size, unique_name)
from mpexplorer.transport import DeviceError, RawREPL, TransportError


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


def _big(n):
    return "x=%r\nprint(len(x))" % ("a" * n)


def test_adaptive_sending_probes_both_modes_and_keeps_data_intact():
    ser = FakeSerial(paste="on")
    repl = RawREPL(ser)
    repl.exec("print(1)")                                   # negotiates raw-paste
    for i in range(10):
        assert repl.exec(_big(1500 + i)).strip() == str(1500 + i).encode()
    assert repl.prefer_plain is not None                    # decided after PROBE_SAMPLES timed sends per mode
    assert 4 <= ser.paste_used < 11                         # plain was tried too, so not every send used raw-paste


def test_adaptive_choice_needs_a_clear_win():
    def decided(plain, paste):
        repl = RawREPL(FakeSerial(paste="on"))
        repl.exec("print(1)")
        for _ in range(2):
            repl._note_rate(False, 2000, 2000 / plain)
            repl._note_rate(True, 2000, 2000 / paste)
        return repl.prefer_plain
    assert decided(plain=1500, paste=1000) is True           # 1.5x: plain
    assert decided(plain=1100, paste=1000) is False          # 1.1x is not worth the lost flow control
    assert decided(plain=500, paste=1000) is False


def test_adaptive_sending_can_be_switched_off():
    ser = FakeSerial(paste="on")
    repl = RawREPL(ser, adaptive=False)
    for _ in range(6):
        repl.exec(_big(2000))
    assert ser.paste_used == 6 and repl.prefer_plain is None


def test_failed_plain_send_goes_back_to_raw_paste(monkeypatch):
    repl = RawREPL(FakeSerial(paste="on"))
    repl.exec("print(1)")
    repl.prefer_plain = True
    real = repl._read_until

    def broken(ending, timeout):
        if ending == b"OK":
            raise TransportError("no OK")
        return real(ending, timeout)
    monkeypatch.setattr(repl, "_read_until", broken)
    with pytest.raises(TransportError):
        repl.exec(_big(2000))
    monkeypatch.undo()
    assert repl.prefer_plain is False
    assert repl.exec(_big(2000)).strip() == b"2000"


def test_paste_reports_device_errors():
    repl = RawREPL(FakeSerial(paste="on"))
    with pytest.raises(DeviceError):
        repl.exec("1/0")
    assert repl.exec("print('ok')").strip() == b"ok"


def _download(d, root, local, tmp_path, name):
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    policy = ConflictPolicy(lambda n: ("overwrite", True))
    copy_tree(d, root + "/" + name, local, str(out / name), Progress(), policy)
    return os.stat(out / name).st_mtime


def test_download_keeps_the_board_file_time(dev, local, tmp_path):
    d, root = dev
    f = tmp_path / "a.txt"
    f.write_bytes(b"hello")
    os.utime(f, (1_700_000_000, 1_700_000_000))
    assert _download(d, root, local, tmp_path, "a.txt") == pytest.approx(1_700_000_000, abs=2)   # board times are UTC


def test_download_ignores_a_clock_that_was_never_set(dev, local, tmp_path):
    d, root = dev
    f = tmp_path / "old.txt"
    f.write_bytes(b"hello")
    os.utime(f, (946_684_800 + 3600, 946_684_800 + 3600))           # 2000-01-01 01:00: the badge default
    assert _download(d, root, local, tmp_path, "old.txt") > time.time() - 60


@pytest.fixture
def fake_rtc(monkeypatch):
    calls = []
    machine = types.ModuleType("machine")
    machine.RTC = lambda: types.SimpleNamespace(datetime=lambda *a: calls.append(a[0]))
    monkeypatch.setitem(sys.modules, "machine", machine)
    return calls


def test_set_clock_writes_utc(dev, fake_rtc):
    d, _root = dev
    before = time.gmtime()
    back = d.set_clock()
    (y, mo, day, wd, h, mi, s, sub), = fake_rtc
    assert (y, mo, day) == before[:3] and wd == before.tm_wday and sub == 0
    assert abs((h * 3600 + mi * 60 + s) - (before.tm_hour * 3600 + before.tm_min * 60 + before.tm_sec)) < 5
    assert len(back) == 6 and back[0] == time.gmtime().tm_year        # the read-back


@pytest.mark.parametrize("reset,resets", [(False, 0), (True, 1)])
def test_close_resets_the_board_only_when_asked(reset, resets):
    ser = FakeSerial(paste="on")
    repl = RawREPL(ser)
    repl.exec("print(1)")
    repl.close(reset)
    assert ser.closed and ser.resets == resets


def test_fit_shortens_text_and_keeps_the_end_of_a_path():
    assert fit("short") == "short" and fit("a" * 30) == "a" * 18 + ".."
    assert fit("/apps/Corteil_tildagon_gps_clock", tail=True).startswith("..")
    assert fit("/apps/Corteil_tildagon_gps_clock", tail=True).endswith("gps_clock") and len(fit("x" * 99, 10)) == 10
    assert fit("tab\there\n") == "tabhere"                      # control characters never reach the board


def test_badge_screen_draws_the_status_page_and_skips_repeats(monkeypatch, tmp_path):
    rec = fakedisplay.install(monkeypatch)
    screen = BadgeScreen(RawREPL(FakeSerial(paste="on")), host="raspberrypi")
    assert screen.show("Ready", folder=str(tmp_path)) is True
    text = " ".join(rec.last)
    assert all(w in text for w in ("mpexplorer", "CONNECTED", "raspberrypi", "Ready", "MB free", "since"))
    assert screen.show() is False and len(rec.frames) == 1                 # nothing changed: no second exec
    assert screen.show("Uploading main.py", kind="busy") is True and "Uploading main.py" in " ".join(rec.last)
    assert screen.show("press reset button", kind="gone") is True
    assert "DISCONNECTED" in rec.last and "MB free" not in " ".join(rec.last)


def test_badge_screen_leaves_a_board_without_a_display_alone(monkeypatch):
    monkeypatch.setitem(sys.modules, "display", None)                       # `import display` raises ImportError
    repl = RawREPL(FakeSerial(paste="on"))
    calls = []
    real = repl.exec
    repl.exec = lambda *a, **k: (calls.append(1), real(*a, **k))[1]
    screen = BadgeScreen(repl)
    assert screen.show("Ready") is False and screen.available is False and len(calls) == 1
    assert screen.show("Done") is False and len(calls) == 1               # not tried again


def test_job_runner_hook_sees_start_and_end_and_cannot_break_the_worker():
    seen = []
    runner = JobRunner()
    runner.hook = lambda job, phase, exc: (seen.append((job.status, phase, type(exc).__name__ if exc else None)),
                                           1 / 0)[0]                        # the hook itself fails every time
    runner.submit(lambda prog: 1, label="Copying ...")
    runner.submit(lambda prog: 1 / 0, label="Deleting ...", status="Deleting x")
    deadline = time.time() + 5
    done = []
    while len(done) < 2 and time.time() < deadline:
        done += runner.drain()
        time.sleep(0.01)
    assert [d[2] for d in done] == [None, done[1][2]] and isinstance(done[1][2], ZeroDivisionError)
    assert seen == [("Copying", "start", None), ("Copying", "end", None),
                    ("Deleting x", "start", None), ("Deleting x", "end", "ZeroDivisionError")]


def test_human_duration():
    got = [human_duration(x) for x in (0, 44.6, 60, 200, 3900)]
    assert got == ["0 s", "45 s", "1 min 00 s", "3 min 20 s", "1 h 05 min"]


def test_progress_describes_the_transfer(monkeypatch):
    p = Progress()
    p.label = "main.py"
    assert p.describe() == "main.py   0 B   0 B/s" and p.eta() is None and p.fraction() == 0.0     # nothing known yet
    clock = {"t": p.started}
    monkeypatch.setattr(time, "monotonic", lambda: clock["t"])
    p.total, p.done = 4096, 1024
    clock["t"] = p.started + 2.0                                                              # 512 B/s
    assert p.fraction() == 0.25 and p.eta() == pytest.approx(6.0)
    assert p.describe() == "main.py   1.0 KB of 4.0 KB (25%)   512 B/s   6 s left"
    assert p.describe(with_label=False) == "1.0 KB of 4.0 KB (25%)   512 B/s   6 s left"
    p.total = 0
    assert p.describe() == "main.py   1.0 KB   512 B/s" and p.eta() is None
