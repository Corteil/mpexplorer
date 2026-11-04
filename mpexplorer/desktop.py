"""Desktop launcher: the menu entry, its icon and a desktop icon, for the current user (no root needed).

`mpexplorer --install-desktop` writes them from the files shipped in the package, so a pipx install needs no checkout.
The window's app id is `mpexplorer` (`GLib.set_prgname`), which is how the taskbar matches the launcher and its icon.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

NAME = "mpexplorer"
HERE = Path(__file__).resolve().parent


def data_home():
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def desktop_dir():
    """The user's desktop folder (`xdg-user-dir DESKTOP`, else ~/Desktop), or None if it does not exist."""
    path = None
    try:
        out = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            path = Path(out.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    path = path or Path.home() / "Desktop"
    # xdg-user-dir answers $HOME when no desktop folder is set: never put the launcher there.
    return path if path.is_dir() and path != Path.home() else None


def exec_line():
    """(command, working folder or None) for the launcher: the installed script by its full path (a desktop session's
    PATH may not have ~/.local/bin, where pipx puts it), else this Python with -m, run from the folder holding the
    package so a plain checkout works too."""
    script = shutil.which(NAME)
    argv = [script] if script else [sys.executable, "-m", NAME]
    cmd = " ".join('"%s"' % a if " " in a else a for a in argv)     # the desktop entry spec quotes with "
    return cmd, (None if script else str(HERE.parent))


def entry_text(exec_cmd, workdir=None):
    out = []
    for line in (HERE / "mpexplorer.desktop").read_text().splitlines():
        if line.startswith("Exec="):
            line = "Exec=" + exec_cmd
            if workdir:
                line += "\nPath=" + workdir
        out.append(line)
    return "\n".join(out) + "\n"


def targets(desktop=None):
    """(menu entry, icon, desktop icon or None) paths."""
    apps = data_home() / "applications" / (NAME + ".desktop")
    icon = data_home() / "icons" / "hicolor" / "256x256" / "apps" / (NAME + ".png")
    return apps, icon, (desktop / (NAME + ".desktop") if desktop else None)


def install(desktop=None, exec_cmd=None, workdir=None):
    """Write the launcher files; returns the paths written."""
    apps, icon, on_desktop = targets(desktop)
    if exec_cmd is None:
        exec_cmd, workdir = exec_line()
    text = entry_text(exec_cmd, workdir)
    written = []
    for path in (apps, on_desktop):
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            path.chmod(0o755)        # file managers only run a desktop file that is executable
            written.append(path)
    icon.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(HERE / "icon.png", icon)
    written.append(icon)
    if on_desktop:
        # GNOME's file manager also wants the file marked trusted; pcmanfm ignores this. Best effort.
        try:
            subprocess.run(["gio", "set", str(on_desktop), "metadata::trusted", "true"],
                           capture_output=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass
    return written


def uninstall(desktop=None):
    """Remove the launcher files; returns the paths removed."""
    removed = []
    for path in targets(desktop):
        if path and path.exists():
            path.unlink()
            removed.append(path)
    return removed


def main(argv):
    if "--uninstall-desktop" in argv:
        removed = uninstall(desktop_dir())
        print("\n".join("Removed %s" % p for p in removed) or "No launcher files found.")
        return
    for p in install(desktop_dir()):
        print("Installed %s" % p)
    print("MicroPython Explorer is in the Programming menu. If double-clicking the desktop icon asks to Execute,\n"
          "choose Execute (pcmanfm: set quick_exec=1 in ~/.config/libfm/libfm.conf to stop the question).")
