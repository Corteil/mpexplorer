"""Catches a frozen GUI: if the main loop stops beating for a while, write every thread's stack to a file.

The GUI calls beat() from its 100 ms poll. A daemon thread looks once a second; when the last beat is older than
`limit` it dumps the stacks (once per stall), so a freeze can be diagnosed afterwards without anyone being there."""
import faulthandler
import os
import threading
import time


def default_path():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "mpexplorer", "stalls.txt")


class Watchdog:
    def __init__(self, path=None, limit=5.0, clock=time.monotonic, dump=None):
        self.path, self.limit, self._clock = path or default_path(), limit, clock
        self._dump = dump or self._dump_stacks
        self._last = clock()
        self._reported = False
        self._stop = threading.Event()

    def beat(self):
        self._last, self._reported = self._clock(), False

    def check(self):
        """True if this call reported a new stall."""
        stalled = self._clock() - self._last
        if stalled < self.limit or self._reported:
            return False
        self._reported = True
        try:
            self._dump(stalled)
        except OSError:
            pass                        # a diagnostic must never be the thing that breaks the app
        return True

    def _dump_stacks(self, stalled):
        os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
        with open(self.path, "a") as f:
            f.write("\n=== main loop stalled for %.1f s at %s ===\n" % (stalled, time.strftime("%Y-%m-%d %H:%M:%S")))
            f.flush()
            faulthandler.dump_traceback(file=f, all_threads=True)

    def start(self):
        threading.Thread(target=self._run, daemon=True, name="watchdog").start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.wait(1.0):
            self.check()
