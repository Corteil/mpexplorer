"""Single worker thread that owns all slow work (serial access is not concurrent)."""
import queue
import threading

from .ops import Progress


class Job:
    def __init__(self, fn, on_done, label, quiet, status=None, inline=False, on_cancel=None):
        self.fn, self.on_done, self.label, self.quiet = fn, on_done, label, quiet
        self.on_cancel = on_cancel  # called by the cancelling thread to stop work that never checks prog (Ctrl-C)
        self.inline = inline        # shows its own progress (a dialog), so the main status bar leaves it alone
        self.status = status or label.rstrip(" .")      # short text for the badge's screen
        self.prog = Progress()
        self.prog.label = label


class JobRunner(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.q = queue.Queue()
        self.finished = queue.Queue()
        self.current = None
        self.hook = None        # optional hook(job, "start"|"end", exc), called on the worker thread around each job
        self.start()

    def submit(self, fn, on_done=None, label="", quiet=False, status=None, inline=False, on_cancel=None):
        job = Job(fn, on_done, label, quiet, status, inline, on_cancel)
        self.q.put(job)
        return job

    def run(self):
        while True:
            job = self.q.get()
            self.current = job
            self._hook(job, "start", None)
            try:
                res, exc = job.fn(job.prog), None
            except BaseException as e:      # reported to the GUI thread, never lost
                res, exc = None, e
            self._hook(job, "end", exc)
            self.current = None
            self.finished.put((job, res, exc))

    def _hook(self, job, phase, exc):
        if self.hook:
            try:
                self.hook(job, phase, exc)
            except Exception:
                pass            # a broken observer must never break the worker

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
        job = self.current
        if job:
            job.prog.cancel()
            if job.on_cancel:
                try:
                    job.on_cancel()
                except Exception:
                    pass            # the job may have just ended; a failed interrupt must not break Cancel or Quit
