"""Where each file chooser opens (docs/FILE_CHOOSER_PLAN.md). GUI-free.

Each kind of choice remembers the folder it last chose from (setting `general.last_dirs`), so a second firmware file is
looked for where the first one was. The first time: Downloads for files that come from the web, the programs folder
for a program. A remembered folder that has gone is ignored.
"""
import os
import sys

from . import paths

KINDS = ("firmware", "backup", "libraries", "program")


def _downloads(home):
    d = os.path.join(home, "Downloads")
    return d if os.path.isdir(d) else home


def default_folder(kind, platform=None, env=None, home=None):
    """The first-time folder for a kind of choice."""
    env = os.environ if env is None else env
    home = home or os.path.expanduser("~")
    if kind == "program":
        if paths.is_windows(platform):
            return env.get("ProgramFiles") or "C:\\Program Files"
        return "/usr/bin"
    return _downloads(home)


def start_folder(settings, kind, platform=None, env=None, home=None, isdir=os.path.isdir):
    """The folder to open a chooser of this kind in: where the last one chose from, if it is still there."""
    last = settings.get("general.last_dirs").get(kind, "")
    if last and isdir(last):
        return last
    return default_folder(kind, platform, env, home)


def remember(settings, kind, chosen):
    """Note where a choice was made: the folder of a chosen file, or a chosen folder's parent (so the next look
    starts beside it). Saved at once, like the other settings."""
    if not chosen:
        return
    path = chosen[0] if isinstance(chosen, (list, tuple)) else chosen
    folder = os.path.dirname(os.path.normpath(path))
    if not folder:
        return
    dirs = settings.get("general.last_dirs")
    if dirs.get(kind) != folder:
        dirs[kind] = folder
        settings.set("general.last_dirs", dirs)
        settings.save()


def example_path(name, platform=None):
    """A path to show as an example in a text box: C:\\Users\\you\\name on Windows, /home/pi/name elsewhere."""
    if paths.is_windows(platform or sys.platform):
        return "C:\\Users\\you\\" + name
    return "/home/pi/" + name
