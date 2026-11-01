"""What the GUIs restore at start and record as the user works (GUI-free)."""
import os
import re

from .errors import BackendError
from .ports import device_of
from .settings import segment


def host_start(settings, fallback):
    """The last host folder if remembering is on and it still exists, else the fallback."""
    if settings.get("general.remember_folders"):
        path = settings.get("folders.host")
        if path and os.path.isdir(path):
            return path
    return fallback


# USB serials that more than one board reports: 2022 badges all say 123456. A folder remembered under one says nothing
# about which board it was, so it is never moved to a chip id.
SHARED_USB_SERIALS = {"123456"}
_CHIP_ID = "import machine,binascii\nprint(binascii.hexlify(machine.unique_id()).decode())"


def chip_key(repl):
    """The settings key for this board's own chip, or None if its firmware cannot say. Talks to the board."""
    try:
        text = repl.exec(_CHIP_ID, timeout=5).decode().strip()
    except (BackendError, OSError):
        return None
    return "chip:" + text if re.fullmatch(r"[0-9a-f]+", text) else None


def _usb_entry_is_this_boards(usb):
    return bool(usb) and usb.rsplit(":", 1)[-1] not in SHARED_USB_SERIALS


def move_folder_to_chip(settings, usb, key):
    """A folder remembered before boards had chip ids follows the board, once. Call on the GUI thread."""
    boards = settings.get("folders.boards")
    if key == usb or segment(usb) not in boards or not _usb_entry_is_this_boards(usb):
        return
    path = boards.pop(segment(usb))
    boards.setdefault(segment(key), path)
    settings.set("folders.boards", boards)


def device_start(settings, dev, board, usb=None):
    """The last folder on this board if it still exists, else the board's root. usb is its USB id, for a folder
    remembered before it had a chip id. Talks to the board, so call it from the job thread."""
    if settings.get("general.remember_folders"):
        folders = settings.get("folders.boards")
        path = folders.get(segment(board))
        if not path and _usb_entry_is_this_boards(usb):
            path = folders.get(segment(usb))
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


def remember_source(settings, side, source):
    """What a pane's list has chosen: its port, or Host. Side is left or right."""
    if settings.get("general.remember_port") and source:
        settings.set("general.source." + side, device_of(source))


def source_choice(settings, side, entries):
    """The saved choice for a pane if it can still be made: an entry of the list, or Host. Else None."""
    if not settings.get("general.remember_port"):
        return None
    saved = settings.get("general.source." + side)
    return next((e for e in entries if saved and device_of(e) == saved), None)


def remember_board_name(settings, board, machine):
    """Keep the model a board reported (os.uname().machine) so the port list can name it next time."""
    machine = (machine or "").strip()
    if board and machine:
        names = settings.get("boards.names")
        if names.get(segment(board)) != machine:
            names[segment(board)] = machine
            settings.set("boards.names", names)


def remember_board_rts(settings, board, rts):
    """Keep whether this board only answers with RTS asserted (learned at connect, see transport.open_board)."""
    if not board:
        return
    table = settings.get("boards.rts")
    if rts and not table.get(segment(board)):
        table[segment(board)] = True
        settings.set("boards.rts", table)
    elif not rts and segment(board) in table:
        del table[segment(board)]               # it answers without RTS now: stop forcing it
        settings.set("boards.rts", table)
