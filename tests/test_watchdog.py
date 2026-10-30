from mpexplorer.watchdog import Watchdog


class Clock:
    t = 100.0

    def __call__(self):
        return self.t


def test_reports_a_stall_once_and_rearms_after_a_beat(tmp_path):
    clock, dumps = Clock(), []
    w = Watchdog(str(tmp_path / "s.txt"), limit=5, clock=clock, dump=dumps.append)
    clock.t += 4
    assert not w.check() and not dumps                      # not yet
    clock.t += 2
    assert w.check() and len(dumps) == 1 and dumps[0] == 6  # 6 s without a beat
    clock.t += 30
    assert not w.check() and len(dumps) == 1                # still the same stall: not repeated
    w.beat()
    clock.t += 6
    assert w.check() and len(dumps) == 2                    # a new stall after the loop recovered


def test_a_healthy_loop_never_dumps(tmp_path):
    clock, dumps = Clock(), []
    w = Watchdog(str(tmp_path / "s.txt"), limit=5, clock=clock, dump=dumps.append)
    for _ in range(100):
        clock.t += 0.1
        w.beat()
        assert not w.check()
    assert dumps == []


def test_the_dump_file_names_the_stall_and_the_threads(tmp_path):
    clock = Clock()
    path = tmp_path / "cache" / "stalls.txt"
    w = Watchdog(str(path), limit=1, clock=clock)
    clock.t += 3
    assert w.check()
    text = path.read_text()
    assert "main loop stalled for 3.0 s" in text and "Thread 0x" in text or "Current thread" in text


def test_a_broken_dump_does_not_raise(tmp_path):
    clock = Clock()

    def broken(_stalled):
        raise OSError("disk full")
    w = Watchdog(str(tmp_path / "s.txt"), limit=1, clock=clock, dump=broken)
    clock.t += 3
    assert w.check()
