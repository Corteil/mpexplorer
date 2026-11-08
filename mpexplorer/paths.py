"""Where the program keeps its settings and caches, on Linux and on Windows (docs/WINDOWS_PLAN.md).

Every function takes the platform, environment, home folder and exe folder as arguments, so Linux tests can exercise the
Windows branches. Called with no arguments they use this machine.

Linux: $XDG_CONFIG_HOME or ~/.config, $XDG_CACHE_HOME or ~/.cache, and $XDG_DATA_HOME or ~/.local/share (kept data:
the app store), each with an `mpexplorer` folder below.
Windows: %APPDATA%\\mpexplorer for settings and %LOCALAPPDATA%\\mpexplorer for caches and kept data.
Portable: if a file named portable.txt sits next to the frozen exe, both live in a `data` folder beside it
(settings in data, caches in data\\cache); if that folder cannot be written the usual places are used and
`portable_problem()` says why.
"""
import os
import sys

APP = "mpexplorer"
PORTABLE_MARKER = "portable.txt"
PORTABLE_DATA = "data"

_problem = ""


def is_windows(platform=None):
    return (platform or sys.platform).startswith("win")


def exe_dir():
    """The folder of the frozen program, or None when running from source (there is no portable mode then)."""
    return os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False) else None


def portable_problem():
    """Why portable.txt was ignored (a read-only folder), else an empty string."""
    return _problem


def portable_dir(exe_folder="default"):
    """The `data` folder beside the exe when portable.txt is there and the folder can be written, else None."""
    global _problem
    folder = exe_dir() if exe_folder == "default" else exe_folder
    if not folder or not os.path.isfile(os.path.join(folder, PORTABLE_MARKER)):
        return None
    data = os.path.join(folder, PORTABLE_DATA)
    try:
        os.makedirs(data, exist_ok=True)
        probe = os.path.join(data, ".write-test")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("x")
        os.remove(probe)
    except OSError as e:
        _problem = ("portable.txt is next to the program but %s cannot be written (%s): using the usual folders."
                    % (data, e))
        return None
    _problem = ""
    return data


def _home(home):
    return home or os.path.expanduser("~")


def config_dir(platform=None, env=None, home=None, exe_folder="default"):
    """The folder holding settings.json."""
    env = os.environ if env is None else env
    data = portable_dir(exe_folder)
    if data:
        return data
    if is_windows(platform):
        base = env.get("APPDATA") or os.path.join(_home(home), "AppData", "Roaming")
    else:
        base = env.get("XDG_CONFIG_HOME") or os.path.join(_home(home), ".config")
    return os.path.join(base, APP)


def cache_dir(platform=None, env=None, home=None, exe_folder="default"):
    """The folder under which the caches (open, copied, libs, firmware, stalls.txt, esptool log) live."""
    env = os.environ if env is None else env
    data = portable_dir(exe_folder)
    if data:
        return os.path.join(data, "cache")
    if is_windows(platform):
        base = env.get("LOCALAPPDATA") or os.path.join(_home(home), "AppData", "Local")
    else:
        base = env.get("XDG_CACHE_HOME") or os.path.join(_home(home), ".cache")
    return os.path.join(base, APP)


def data_dir(platform=None, env=None, home=None, exe_folder="default"):
    """The folder for data kept on purpose (the badge app store's local copy), not a cache that may be cleared:
    $XDG_DATA_HOME or ~/.local/share on Linux, %LOCALAPPDATA% on Windows, each with an `mpexplorer` folder below;
    portable mode keeps it in the `data` folder itself."""
    env = os.environ if env is None else env
    data = portable_dir(exe_folder)
    if data:
        return data
    if is_windows(platform):
        base = env.get("LOCALAPPDATA") or os.path.join(_home(home), "AppData", "Local")
    else:
        base = env.get("XDG_DATA_HOME") or os.path.join(_home(home), ".local", "share")
    return os.path.join(base, APP)


def settings_file(platform=None, env=None, home=None, exe_folder="default"):
    """$MPEXPLORER_CONFIG if set (in every mode), else settings.json in config_dir()."""
    env = os.environ if env is None else env
    explicit = env.get("MPEXPLORER_CONFIG")
    if explicit:
        return explicit
    return os.path.join(config_dir(platform, env, home, exe_folder), "settings.json")


CONSOLE_EXE = "mpexplorer-console.exe"


def helper_executable():
    """What runs our own helpers (--miniterm, --esptool): this Python from source; in the frozen Windows build the
    console twin beside the exe, because the windowed exe has no stdout and cannot own a console window."""
    if not getattr(sys, "frozen", False):
        return sys.executable
    twin = os.path.join(exe_dir(), CONSOLE_EXE)
    return twin if os.path.isfile(twin) else sys.executable
