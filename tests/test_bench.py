"""tools/bench.py runs end to end against the fake board (small sizes) and cleans up after itself."""
import importlib.util
from pathlib import Path

import pytest

from fakedevice import FakeSerial
from mpexplorer.errors import BackendError
from mpexplorer.transport import RawREPL

spec = importlib.util.spec_from_file_location("bench", Path(__file__).parent.parent / "tools" / "bench.py")
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


def test_benchmark_runs_both_modes_and_cleans_up(tmp_path):
    root = str(tmp_path / "bench")
    lines = []
    info, rows = bench.run_benchmark(lambda paste: RawREPL(FakeSerial(paste="on"), paste=paste), [4, 8], 12,
                                     root=root, log=lines.append, reset=False)
    modes = {r["mode"] for r in rows}
    assert modes == {"raw-paste", "plain"} and info["system"]
    tests = {r["test"] for r in rows}
    assert {"upload 4 KB", "download 8 KB", "list 12 files", "flash write (on board)"} <= tests
    assert all(r["seconds"] >= 0 for r in rows)
    assert not Path(root).exists()                                    # the board's bench folder is removed
    text = "\n".join(bench.verdict(rows, [4, 8]))
    assert "Decision:" in text and "| Mode |" in bench.markdown(info, rows)


def test_board_without_raw_paste_is_measured_once(tmp_path):
    info, rows = bench.run_benchmark(lambda paste: RawREPL(FakeSerial(paste="old"), paste=paste), [4], 5,
                                     root=str(tmp_path / "b"), log=lambda s: None, reset=False)
    assert {r["mode"] for r in rows} == {"plain"}


def test_refuses_to_touch_an_existing_folder(tmp_path):
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "precious.txt").write_text("keep")
    with pytest.raises(BackendError):
        bench.run_benchmark(lambda paste: RawREPL(FakeSerial(), paste=paste), [4], 5, root=str(tmp_path / "b"),
                            log=lambda s: None, reset=False)
    assert (tmp_path / "b" / "precious.txt").read_text() == "keep"


def test_verdict_flags_slow_numbers():
    rows = [{"mode": "plain", "test": "upload 256 KB", "seconds": 30.0, "kb_per_s": 8.5, "note": ""},
            {"mode": "plain", "test": "download 256 KB", "seconds": 2.0, "kb_per_s": 128.0, "note": ""},
            {"mode": "plain", "test": "list 200 files", "seconds": 0.5, "kb_per_s": None, "note": ""},
            {"mode": "plain", "test": "flash write (on board)", "seconds": 1.0, "kb_per_s": 250.0, "note": ""}]
    text = "\n".join(bench.verdict(rows, [256]))
    assert "SLOW" in text and "worth building" in text and "link is the bottleneck" in text
