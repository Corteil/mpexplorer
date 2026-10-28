"""What the GUIs restore at start and record as the user works (GUI-free, shared by GTK and Tk)."""
import os

from .ports import device_of
from .settings import segment


def host_start(settings, fallback):
    """The last host folder if remembering is on and it still exists, else the fallback."""
    if settings.get("general.remember_folders"):
        path = settings.get("folders.host")
        if path and os.path.isdir(path):
            return path
    return fallback


def device_start(settings, dev, board):
    """The last folder on this board if it still exists, else the board's root.
    Talks to the board, so call it from the job thread."""
    if settings.get("general.remember_folders"):
        path = settings.get("folders.boards").get(segment(board))
        if path:
            try:
                st = dev.stat(path)
            except Exception:
                st = None
            if st and st.is_dir:
                return path
    return dev.root


def remember_folder(settings, is_device, path, board=None):
    if not settings.get("general.remember_folders") or not path:
        return
    if not is_device:
        settings.set("folders.host", path)
    elif board:
        boards = settings.get("folders.boards")
        boards[segment(board)] = path
        settings.set("folders.boards", boards)


def port_choice(settings, entries):
    """Index in the list of 'device  description' strings to pre-select, or None to leave the choice alone."""
    if settings.get("general.remember_port"):
        last = settings.get("general.last_port")
        for i, entry in enumerate(entries):
            if last and device_of(entry) == last:
                return i
    return 0 if entries else None


def remember_port(settings, device):
    if settings.get("general.remember_port") and device:
        settings.set("general.last_port", device)
