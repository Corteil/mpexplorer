"""Persistent settings (GUI-free): one JSON file, validated, written atomically, never fatal.

    s = Settings.load()                    # ~/.config/mpexplorer/settings.json, or $MPEXPLORER_CONFIG
    s.get("window.width")                  # 1180 unless the user resized it
    s.set("general.last_port", "/dev/ttyACM0")
    s.save()                               # no-op when nothing changed; returns False (never raises) on I/O errors

Design rules
- A missing, empty, corrupt or partly invalid file never stops the app: bad values fall back to their defaults and are
  listed in `settings.problems`; an unreadable file is renamed to `settings.json.bad` and the defaults are used.
- Keys are dotted paths and must be declared in SPEC (a typo raises KeyError). Dict-valued keys hold user data
  (file-type associations, folders per board). Unknown keys already in the file are preserved on save, so an older
  build does not erase what a newer one wrote.
- A file written by a newer version (`version` higher than ours) is used read-only: never overwritten.
- Writes are atomic (temp file + fsync + replace) with mode 0600 in a 0700 directory.
"""
import copy
import json
import os
import tempfile
import threading

VERSION = 1
_MISSING = object()


# -- validators: each returns the cleaned value or raises ValueError -------------------------------------------
def boolean(v):
    if isinstance(v, bool):
        return v
    raise ValueError("expected true or false")


def string(v):
    if isinstance(v, str):
        return v
    raise ValueError("expected text")


def integer(lo, hi):
    def check(v):
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            raise ValueError("expected a whole number from %d to %d" % (lo, hi))
        return v
    return check


def optional(check):
    return lambda v: None if v is None else check(v)


def str_dict(v):
    if isinstance(v, dict) and all(isinstance(k, str) and isinstance(x, str) for k, x in v.items()):
        return dict(v)
    raise ValueError("expected a table of text values")


def sort_order(v):
    if isinstance(v, list) and len(v) == 2 and v[0] in ("name", "size", "modified") and isinstance(v[1], bool):
        return list(v)
    raise ValueError('expected ["name"|"size"|"modified", reverse]')


# key -> (default, validator). Everything the program remembers is declared here.
SPEC = {
    "general.last_port": ("", string),
    "general.show_hidden": (False, boolean),
    "general.remember_window": (True, boolean),     # Preferences: restore window size/position at start
    "general.remember_folders": (True, boolean),    # ... the last host folder and the last folder per board
    "general.remember_port": (True, boolean),       # ... the last serial port
    "general.set_board_clock": (False, boolean),    # set the board's clock to the host's time at every connect
    "general.device_mtimes": (False, boolean),      # device pane: fetch modified times (one slow stat per file)
    "general.sort.host": (["name", False], sort_order),
    "general.sort.device": (["name", False], sort_order),
    "window.width": (1180, integer(300, 10000)),
    "window.height": (680, integer(200, 10000)),
    "window.x": (None, optional(integer(-10000, 20000))),
    "window.y": (None, optional(integer(-10000, 20000))),
    "window.maximized": (False, boolean),
    "folders.host": ("", string),
    "folders.boards": ({}, str_dict),               # board id -> last device folder
    "open_with.auto_upload": (True, boolean),       # upload saves made in an external program (docs/OPEN_WITH_PLAN.md)
    "open_with.by_extension": ({}, str_dict),       # ".py" -> "editor" | "app:<desktop id>" | "custom:<name>"
    "open_with.custom": ({}, str_dict),             # name -> command with {file}
    "connection.fast_mode": (False, boolean),       # opt-in RAM-only agent (docs/AGENT_PLAN.md)
}


def config_path():
    """$MPEXPLORER_CONFIG, else $XDG_CONFIG_HOME/mpexplorer/settings.json, else ~/.config/mpexplorer/settings.json."""
    explicit = os.environ.get("MPEXPLORER_CONFIG")
    if explicit:
        return explicit
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "mpexplorer", "settings.json")


def segment(text):
    """Make arbitrary text (a serial number, a path) safe to use as one dict key."""
    return str(text).replace(".", "_")


class Settings:
    def __init__(self, path=None):
        """path=None keeps everything in memory only (tests, or when the config directory is unusable)."""
        self.path = path
        self.problems = []
        self.read_only = False
        self._data = {}                 # nested dict exactly as stored (known and unknown keys)
        self._lock = threading.RLock()
        self._dirty = False
        self._listeners = []

    @classmethod
    def load(cls, path=None):
        s = cls(path or config_path())
        s._read()
        return s

    # -- reading -----------------------------------------------------------
    def _read(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                raw = f.read()
        except FileNotFoundError:
            return
        except (OSError, UnicodeDecodeError) as e:
            self.problems.append("could not read %s: %s" % (self.path, e))
            return
        if not raw.strip():
            return
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("top level is not an object")
        except ValueError as e:
            self.problems.append("%s is damaged (%s); using defaults, the old file is kept as .bad" % (self.path, e))
            self._quarantine()
            return
        version = data.get("version", 1)
        if not isinstance(version, int) or isinstance(version, bool):
            version = 1
        if version > VERSION:
            self.read_only = True
            self.problems.append("%s was written by a newer version (%s); it will not be overwritten" % (
                self.path, version))
        data.pop("version", None)
        self._data = data
        for key, (default, check) in SPEC.items():          # repair bad values now, remember what was wrong
            value = self._lookup(key)
            if value is _MISSING:
                continue
            try:
                check(value)
            except ValueError as e:
                self.problems.append("setting %s ignored (%s)" % (key, e))
                self._remove(key)

    def _quarantine(self):
        try:
            os.replace(self.path, self.path + ".bad")
        except OSError:
            pass

    def _lookup(self, key):
        node = self._data
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return _MISSING
            node = node[part]
        return node

    def _remove(self, key):
        parts = key.split(".")
        node = self._data
        for part in parts[:-1]:
            node = node.get(part)
            if not isinstance(node, dict):
                return
        node.pop(parts[-1], None)

    # -- public API --------------------------------------------------------
    def get(self, key):
        """The stored value, or the default. Containers are copies: changing them does not change the settings."""
        if key not in SPEC:
            raise KeyError("unknown setting %r" % key)
        with self._lock:
            value = self._lookup(key)
            return copy.deepcopy(SPEC[key][0] if value is _MISSING else value)

    def set(self, key, value):
        """Store a value (ValueError if it is invalid for that key). Returns True if it changed."""
        if key not in SPEC:
            raise KeyError("unknown setting %r" % key)
        value = copy.deepcopy(SPEC[key][1](value))
        with self._lock:
            if value == self.get(key):
                return False
            node = self._data
            for part in key.split(".")[:-1]:
                nxt = node.get(part)
                if not isinstance(nxt, dict):
                    nxt = node[part] = {}
                node = nxt
            node[key.split(".")[-1]] = value
            self._dirty = True
            listeners = list(self._listeners)
        for cb in listeners:
            try:
                cb(key, value)
            except Exception:
                pass                    # a broken listener must not break saving settings
        return True

    def reset(self, key):
        if key not in SPEC:
            raise KeyError("unknown setting %r" % key)
        with self._lock:
            if self._lookup(key) is not _MISSING:
                self._remove(key)
                self._dirty = True

    def reset_all(self):
        """Back to the defaults for every declared setting (unknown keys from newer versions are kept).
        Listeners hear about each setting that changed. Returns the list of those keys."""
        changed = []
        with self._lock:
            for key, (default, _check) in SPEC.items():
                if self._lookup(key) is not _MISSING:
                    if self.get(key) != default:
                        changed.append(key)
                    self._remove(key)
                    self._dirty = True
            listeners = list(self._listeners)
        for key in changed:
            for cb in listeners:
                try:
                    cb(key, copy.deepcopy(SPEC[key][0]))
                except Exception:
                    pass
        return changed

    def subscribe(self, callback):
        """callback(key, value) after each change that alters a setting."""
        self._listeners.append(callback)

    def unsubscribe(self, callback):
        try:
            self._listeners.remove(callback)
        except ValueError:
            pass

    @property
    def dirty(self):
        return self._dirty

    # -- writing -----------------------------------------------------------
    def save(self):
        """Write the file if anything changed. Returns True on success (or nothing to do); never raises."""
        with self._lock:
            if not self._dirty:
                return True
            if self.path is None:
                self._dirty = False
                return True
            if self.read_only:
                return False
            try:
                folder = os.path.dirname(self.path)
                os.makedirs(folder, mode=0o700, exist_ok=True)
                payload = json.dumps(dict(self._data, version=VERSION), indent=2, sort_keys=True) + "\n"
                fd, tmp = tempfile.mkstemp(prefix=".settings-", suffix=".tmp", dir=folder)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        f.write(payload)
                        f.flush()
                        os.fsync(f.fileno())
                    os.chmod(tmp, 0o600)
                    os.replace(tmp, self.path)
                except BaseException:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
                    raise
            except OSError as e:
                self.problems.append("could not save settings: %s" % e)
                return False
            self._dirty = False
            return True
