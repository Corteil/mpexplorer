"""Open with...: pick the program for a file, and edit a device file in place with any program (docs/OPEN_WITH_PLAN.md).

GUI-free. A device file is downloaded to a private cache folder, the chosen program is launched on that copy, and a
`Watcher` decides when a save has settled and whether the device copy changed meanwhile (a conflict).
"""
import hashlib
import os
import posixpath
import re
import shlex
import subprocess
import time
from collections import namedtuple
from dataclasses import dataclass, field

from .backends import LocalBackend, human
from .ops import Progress, _copy_file

SETTLE = 1.0                    # seconds without another save before a change is uploaded
BUILTIN = ("editor", "run", "hex", "image")
PART_SUFFIX = ".mpx-part"


# -- choosing the program -------------------------------------------------
def expand_command(template, path):
    """'geany {file}' -> ['geany', path]: the argument list for subprocess (never a shell, so odd file names are safe).

    The template is split first and {file} replaced afterwards, so spaces and quotes in the path cannot split or break
    an argument. A template without {file} gets the path appended. ValueError for an empty template or bad quoting."""
    argv = shlex.split(template)
    if not argv:
        raise ValueError("the command is empty")
    if any("{file}" in a for a in argv):
        return [a.replace("{file}", path) for a in argv]
    return argv + [path]


def handler_for(settings, name, is_text=True, content_type=None):
    """The saved choice for this file, or the built-in editor for text, or None (ask the user).

    Lookup order: the file's extension, then its content type (both are keys of open_with.by_extension)."""
    table = settings.get("open_with.by_extension")
    for key in (posixpath.splitext(name)[1].lower(), content_type):
        if key and key in table:
            return table[key]
    return "editor" if is_text else None


def parse_handler(settings, handler):
    """'editor' | 'app:<desktop id>' | 'custom:<name>' -> ('builtin', name) | ('app', id) | ('command', template).

    ValueError for an unknown kind or a custom name that is not defined (a stale setting must not crash an open)."""
    if handler in BUILTIN:
        return "builtin", handler
    kind, _, value = handler.partition(":")
    if kind == "app" and value:
        return "app", value
    if kind == "custom" and value:
        template = settings.get("open_with.custom").get(value)
        if template is None:
            raise ValueError("no custom command called %r (Preferences > File types)" % value)
        return "command", template
    raise ValueError("unknown way of opening a file: %r" % handler)


# -- the private cache copy -----------------------------------------------
def cache_path(root, board, devpath):
    """<root>/<board>/<device path>, for a device file. ValueError if the path could climb out of the cache."""
    parts = [p for p in devpath.split("/") if p]
    if not parts or any(p in (".", "..") or "\0" in p for p in parts):
        raise ValueError("not a file path inside the board: %r" % devpath)
    return os.path.join(root, re.sub(r"[^A-Za-z0-9_:-]", "_", board or "") or "board", *parts)


def keep_both_name(remote):
    """'/lib/a.py' -> '/lib/a (device).py': where the device's own version goes when the user keeps both."""
    stem, ext = posixpath.splitext(remote)
    return "%s (device)%s" % (stem, ext)


# -- fingerprints: has the file changed? ----------------------------------
Fingerprint = namedtuple("Fingerprint", "size mtime digest")        # digest is None on a board without hashlib


def device_fingerprint(dev, path):
    st = dev.stat(path)
    return None if st is None else Fingerprint(st.size, st.mtime, dev.checksum(path))


def same_device_file(a, b):
    """Same content as far as we can tell: SHA-256 when both have one, else size and time."""
    if a is None or b is None:
        return a is b
    if a.digest and b.digest:
        return a.digest == b.digest
    return (a.size, a.mtime) == (b.size, b.mtime)


def file_digest(path):
    """SHA-256 of a local file, or None if it does not exist."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(65536), b""):
                h.update(block)
    except FileNotFoundError:
        return None
    return h.hexdigest()


# -- watching the copy ----------------------------------------------------
class Watcher:
    """Decides when a saved local copy should go back to the board. Call notice() on every file-system event (and on a
    timer: editors save by replacing the file, so an event may name a different inode); ask ready() whether the save has
    settled. The clock and the digest function are injectable so the timing is testable."""

    def __init__(self, local, remote, baseline, settle=SETTLE, clock=time.monotonic, digest=file_digest):
        self.local, self.remote, self.baseline = local, remote, baseline    # baseline: the device file as downloaded
        self.settle, self._clock, self._digest = settle, clock, digest
        self._sent = digest(local)          # the content the device is known to hold
        self._seen = self._sent             # the content at the last look: an unchanged poll must not restart the wait
        self._changed_at = None
        self.gone = False                   # the local copy was deleted
        self.paused = False                 # the user stopped auto-upload for this file

    def notice(self):
        d = self._digest(self.local)
        if d is None:
            self.gone, self._changed_at = True, None
            return
        self.gone = False
        if d == self._sent:
            self._changed_at = None         # a save that leaves the content as the device has it (or an undo)
        elif d != self._seen:
            self._changed_at = self._clock()    # new content: every further save restarts the wait
        self._seen = d

    @property
    def pending(self):
        return self._changed_at is not None

    def ready(self):
        """A change has been waiting for `settle` seconds with no further save, and auto-upload is on."""
        return self.pending and not self.paused and not self.gone and self._clock() - self._changed_at >= self.settle

    def snapshot(self):
        """The digest of what is about to be uploaded; pass it to uploaded() so a save made meanwhile is not lost."""
        return self._digest(self.local)

    def conflict(self, current):
        """True if the device file is no longer what was downloaded (someone or the board changed it)."""
        return not same_device_file(self.baseline, current)

    def uploaded(self, new_baseline, sent):
        self.baseline, self._sent, self._seen, self._changed_at = new_baseline, sent, sent, None
        self.notice()                       # picks up a save that arrived while uploading


# -- the transfers --------------------------------------------------------
def download_for_edit(dev, remote, local, prog=None, **watcher_args):
    """Copy a device file into the cache (private: 0700 folders, 0600 file) and return a Watcher for it.
    The fingerprint is taken first, so a change that lands during the download shows up as a conflict, not as a loss."""
    baseline = device_fingerprint(dev, remote)
    if baseline is None:
        raise FileNotFoundError(remote)
    os.makedirs(os.path.dirname(local), mode=0o700, exist_ok=True)
    _copy_file(dev, remote, LocalBackend(), local, prog or Progress())
    os.chmod(local, 0o600)
    return Watcher(local, remote, baseline, **watcher_args)


def upload_changes(dev, watcher, choice=None, prog=None):
    """Send the local copy back. Returns 'uploaded', 'conflict' (ask the user, then call again with a choice) or
    'skipped'.

    choice, after a conflict: 'overwrite' (replace the device file), 'keep_both' (the device's version is renamed to
    'name (device).ext' first, then ours is uploaded), or 'skip' (leave the device alone, stop asking about these
    edits).
    The file is written under a temporary name and renamed, so a cancel or a dropped cable never loses the old
    copy."""
    snap = watcher.snapshot()
    if snap is None:
        return "skipped"                    # the local copy is gone
    current = device_fingerprint(dev, watcher.remote)
    if watcher.conflict(current):
        if choice is None:
            return "conflict"
        if choice == "skip":
            watcher.uploaded(current, snap)
            return "skipped"
        if choice == "keep_both" and current is not None:
            dev.rename(watcher.remote, keep_both_name(watcher.remote))
        elif choice not in ("overwrite", "keep_both"):
            raise ValueError("unknown choice %r" % choice)
    tmp = watcher.remote + PART_SUFFIX
    _copy_file(LocalBackend(), watcher.local, dev, tmp, prog or Progress())
    if dev.stat(watcher.remote) is not None:
        dev.remove(watcher.remote)          # FAT cannot rename over an existing file
    dev.rename(tmp, watcher.remote)
    watcher.uploaded(device_fingerprint(dev, watcher.remote), snap)
    return "uploaded"



# -- editing in place: the sessions ---------------------------------------
def launch_program(argv):
    """Start an external program detached from us and from the terminal; it may outlive mpexplorer."""
    return subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)


@dataclass
class Session:
    dev: object
    board: str
    remote: str
    watcher: Watcher
    proc: object = None                 # the launched program (it may exit at once, handing over to a running window)
    busy: bool = False                  # a download or upload job is queued or running
    key: tuple = None                   # (mtime_ns, size) of the local copy at the last look
    retry_after: float = 0.0            # after a failed upload, do not try again before this time
    last_active: float = 0.0
    notes: list = field(default_factory=list)


class EditManager:
    """Open a device file in an external program, and send each saved change back to the board.

    GUI-free: the GUI calls edit_device_file() and then tick() a few times a second; slow work goes through the job
    runner like every other transfer. say(text) reports in the status bar; ask_conflict(name) returns 'overwrite',
    'keep_both' or 'skip' (None counts as skip); refresh() is called after an upload so the panes show the new size."""

    def __init__(self, runner, cache_root, say, ask_conflict, refresh=None, launch=launch_program,
                 clock=time.monotonic, interval=0.5, idle=600.0):
        self.runner, self.root, self.say, self.ask_conflict = runner, cache_root, say, ask_conflict
        self.refresh, self.launch, self._clock = refresh or (lambda: None), launch, clock
        self.interval, self.idle = interval, idle
        self.sessions = []
        self._last_tick = float("-inf")

    # -- starting ------------------------------------------------------
    def edit_device_file(self, dev, board, remote, template):
        """Download remote into the cache, then launch `template` ({file} = the copy) on it. GUI thread."""
        name = posixpath.basename(remote)
        try:
            local = cache_path(self.root, board, remote)
            expand_command(template, local)                     # a bad template fails now, before any download
        except ValueError as e:
            self.say("Cannot open %s: %s" % (name, e))
            return
        for s in self.sessions:
            if s.board == board and s.remote == remote:
                self._start(s, template)                        # already open: just bring the program up again
                return
        placeholder = Session(dev, board, remote, None)
        placeholder.busy = True

        def work(prog):
            return download_for_edit(dev, remote, local, prog, clock=self._clock)

        def done(watcher, exc):
            if exc:
                self.say("Cannot open %s: %s" % (name, exc))
                return
            placeholder.watcher, placeholder.busy = watcher, False
            placeholder.last_active = self._clock()
            self.sessions.append(placeholder)
            self._start(placeholder, template)
        self.runner.submit(work, done, "Downloading " + remote, status="Downloading " + name)

    def _start(self, s, template):
        argv = expand_command(template, s.watcher.local)
        try:
            s.proc = self.launch(argv)
        except OSError as e:
            self.say("Cannot start %s: %s" % (argv[0], e))
            self._drop(s)
            return
        self.say("Editing %s in %s: saves upload to the board" % (posixpath.basename(s.remote), argv[0]))

    def open_host_file(self, path, template):
        """A file on this computer: just launch the program. Returns an error text, or None."""
        try:
            self.launch(expand_command(template, path))
        except (OSError, ValueError) as e:
            return str(e)
        return None

    # -- watching ------------------------------------------------------
    def tick(self):
        """Look at every open copy and upload settled saves. GUI thread, cheap: a stat per copy unless one changed."""
        now = self._clock()
        if now - self._last_tick < self.interval:
            return
        self._last_tick = now
        for s in list(self.sessions):
            if s.busy:
                continue
            try:
                st = os.stat(s.watcher.local)
                key = (st.st_mtime_ns, st.st_size)
            except OSError:
                key = None
            if key != s.key or s.watcher.pending:               # hash only when something changed or is waiting
                if key != s.key:
                    s.last_active = now
                s.key = key
                s.watcher.notice()
            if s.watcher.ready() and now >= s.retry_after:
                self._upload(s)
            elif (not s.watcher.pending and s.proc is not None and s.proc.poll() is not None
                  and now - s.last_active > self.idle):
                self._drop(s)                                   # the program has gone and nothing changed for a while

    def _upload(self, s, choice=None):
        name = posixpath.basename(s.remote)
        s.busy = True

        def work(prog):
            return upload_changes(s.dev, s.watcher, choice, prog), os.path.getsize(s.watcher.local)

        def done(res, exc):
            s.busy = False
            if exc:
                s.retry_after = self._clock() + 10
                self.say("Upload of %s failed: %s" % (name, exc))
                return
            result, size = res
            if result == "conflict":
                self._upload(s, self.ask_conflict(name) or "skip")
            elif result == "uploaded":
                self.say("Uploaded %s (%s)" % (name, human(size)))
                self.refresh()
            else:
                self.say("Not uploaded: %s" % name)
        self.runner.submit(work, done, "Uploading " + s.remote, status="Uploading " + name)

    # -- ending --------------------------------------------------------
    def flush(self, dev, board):
        """Worker thread, before the board is closed: send every pending save. Conflicts are never overwritten silently:
        they are left alone and their names returned (the cache copy is kept)."""
        left = []
        for s in [x for x in self.sessions if x.board == board]:
            s.watcher.notice()
            if s.watcher.pending and not s.watcher.gone:
                try:
                    if upload_changes(dev, s.watcher) != "uploaded":
                        left.append(posixpath.basename(s.remote))
                except Exception:
                    left.append(posixpath.basename(s.remote))
        return left

    def close_board(self, board):
        """The board is gone: forget its sessions and delete the cache copies, except ones with unsent changes
        (those stay in the cache so nothing the user typed is lost). Returns the names of the kept ones."""
        kept = []
        for s in [x for x in self.sessions if x.board == board]:
            s.watcher.notice()
            if s.watcher.pending:
                kept.append(posixpath.basename(s.remote))
                self.sessions.remove(s)
            else:
                self._drop(s)
        return kept

    def _drop(self, s):
        if s in self.sessions:
            self.sessions.remove(s)
        try:
            os.remove(s.watcher.local)
            folder = os.path.dirname(s.watcher.local)
            while folder.startswith(self.root + os.sep):
                os.rmdir(folder)                                # only empty folders go; stops at the first that is not
                folder = os.path.dirname(folder)
        except OSError:
            pass
