"""Backend-independent file operations: copy, move, delete, with progress and cancel."""
import threading
import time

from .backends import CHUNK, MIN_VALID_MTIME, human, human_duration
from .errors import BackendError, Cancelled


class Progress:
    def __init__(self):
        self.total = 0
        self.done = 0
        self.label = ""
        self.started = time.monotonic()
        self._cancel = threading.Event()

    def add(self, n):
        self.done += n

    def cancel(self):
        self._cancel.set()

    def check(self):
        if self._cancel.is_set():
            raise Cancelled()

    def rate(self):
        return self.done / max(time.monotonic() - self.started, 1e-3)

    def fraction(self):
        return min(1.0, self.done / self.total) if self.total else 0.0

    def eta(self):
        """Seconds left at the current rate, or None when the total or the rate is not known yet."""
        rate = self.rate()
        return (self.total - self.done) / rate if self.total > self.done and self.done and rate > 0 else None

    def describe(self):
        """The transfer line: file, bytes done of total, percent, speed, time left."""
        parts = [self.label] if self.label else []
        if self.total:
            parts.append("%s of %s (%d%%)" % (human(self.done), human(self.total), self.fraction() * 100))
        else:
            parts.append(human(self.done))
        parts.append("%s/s" % human(self.rate()))
        eta = self.eta()
        if eta is not None:
            parts.append("%s left" % human_duration(eta))
        return "   ".join(parts)


class ConflictPolicy:
    """Decides what to do when a destination file exists.

    ask(name) -> (action, apply_to_all) with action in overwrite/skip/rename/cancel.
    Without ask, existing files are skipped.
    """

    def __init__(self, ask=None):
        self.ask = ask
        self.sticky = None
        self.skipped = 0

    def decide(self, be, dst):
        """Return the path to write, or None to skip."""
        if be.stat(dst) is None:
            return dst
        action = self.sticky
        if action is None:
            action, everywhere = self.ask(be.basename(dst)) if self.ask else ("skip", False)
            if everywhere:
                self.sticky = action
        if action == "cancel":
            raise Cancelled()
        if action == "overwrite":
            return dst
        if action == "rename":
            return unique_name(be, dst)
        self.skipped += 1
        return None


def unique_name(be, path):
    """name.ext -> name (1).ext, first one that is free."""
    parent, name = be.parent(path), be.basename(path)
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem:
        stem, ext = name, ""
    else:
        ext = "." + ext
    i = 1
    while True:
        cand = be.join(parent, "%s (%d)%s" % (stem, i, ext))
        if be.stat(cand) is None:
            return cand
        i += 1


def is_within(be, path, folder):
    path, folder = path.rstrip(be_sep(be)), folder.rstrip(be_sep(be))
    return path == folder or path.startswith(folder + be_sep(be))


def be_sep(be):
    return "/" if be.key == "device" else __import__("os").sep


def total_size(be, path):
    st = be.stat(path)
    if st is None:
        return 0
    if not st.is_dir:
        return st.size
    return sum(total_size(be, be.join(path, e.name)) if e.is_dir else e.size
               for e in be.listdir(path))


def _copy_file(src_be, src, dst_be, dst, prog, mtime=None):
    prog.label = src_be.basename(src)
    if src_be is dst_be and hasattr(src_be, "copy_file"):
        src_be.copy_file(src, dst, prog.add)
        return
    ok = False
    try:
        with src_be.open_read(src) as r, dst_be.open_write(dst) as w:
            while True:
                prog.check()
                data = r.read(CHUNK)
                if not data:
                    break
                w.write(data)
                prog.add(len(data))
        ok = True
    finally:
        if not ok:
            try:
                dst_be.remove(dst)      # never leave a truncated file behind
            except Exception:
                pass
    if ok and mtime and hasattr(dst_be, "set_mtime"):
        try:
            dst_be.set_mtime(dst, mtime)      # a file off a board keeps the time it was written
        except OSError:
            pass


def copy_tree(src_be, src, dst_be, dst, prog, policy):
    prog.check()
    st = src_be.stat(src)
    if st is None:
        raise BackendError("%s no longer exists" % src)
    if st.is_dir:
        existing = dst_be.stat(dst)
        if existing is None:
            dst_be.mkdir(dst)
        elif not existing.is_dir:
            raise BackendError("%s exists and is not a folder" % dst)
        for e in sorted(src_be.listdir(src), key=lambda e: e.name):
            copy_tree(src_be, src_be.join(src, e.name), dst_be, dst_be.join(dst, e.name), prog, policy)
        return
    target = policy.decide(dst_be, dst)
    if target is None:
        prog.add(st.size)
        return
    keep = st.mtime if getattr(src_be, "key", None) == "device" and st.mtime >= MIN_VALID_MTIME else None
    _copy_file(src_be, src, dst_be, target, prog, keep)


def delete_tree(be, path, prog):
    prog.check()
    st = be.stat(path)
    if st is None:
        return
    if st.is_dir:
        for e in be.listdir(path):
            delete_tree(be, be.join(path, e.name), prog)
        be.rmdir(path)
    else:
        be.remove(path)


def move_item(src_be, src, dst_be, dst, prog, policy):
    """Rename when possible, otherwise copy then delete (only if nothing was skipped)."""
    if src_be is dst_be and dst_be.stat(dst) is None:
        src_be.rename(src, dst)
        return
    skipped = policy.skipped
    copy_tree(src_be, src, dst_be, dst, prog, policy)
    if policy.skipped == skipped:
        delete_tree(src_be, src, prog)
