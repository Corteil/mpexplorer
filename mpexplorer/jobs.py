"""Single worker thread that owns all slow work (serial access is not concurrent)."""
import queue
import threading

from .ops import Progress


class Job:
    def __init__(self, fn, on_done, label, quiet):
        self.fn, self.on_done, self.label, self.quiet = fn, on_done, label, quiet
        self.prog = Progress()
        self.prog.label = label


class JobRunner(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.q = queue.Queue()
        self.finished = queue.Queue()
        self.current = None
        self.start()

    def submit(self, fn, on_done=None, label="", quiet=False):
        job = Job(fn, on_done, label, quiet)
        self.q.put(job)
        return job

    def run(self):
        while True:
            job = self.q.get()
            self.current = job
            try:
                res, exc = job.fn(job.prog), None
            except BaseException as e:      # reported to the GUI thread, never lost
                res, exc = None, e
            self.current = None
            self.finished.put((job, res, exc))

    def drain(self):
        out = []
        while True:
            try:
                out.append(self.finished.get_nowait())
            except queue.Empty:
                return out

    @property
    def busy(self):
        return self.current is not None or not self.q.empty()

    def cancel_all(self):
        while True:
            try:
                self.q.get_nowait()
            except queue.Empty:
                break
        if self.current:
            self.current.prog.cancel()
