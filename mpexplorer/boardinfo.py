"""Which firmware a board runs, the badge that shows it, and its nickname (GUI-free). Plan: docs/BOARD_LABELS_PLAN.md.

A board is known by its USB key (`vid:pid:serial`) before it is connected and by its chip id (`chip:<hex>`) after.
Some boards share a USB serial (the 2022 badges say 123456, a MicroPython Pico was seen with 000000000000), so for
them the USB key says nothing about which board it is: no nickname is shown under it, and the chip id decides once
connected.
"""
import re
from collections import namedtuple

from .settings import segment

MICROPYTHON, CIRCUITPYTHON = "micropython", "circuitpython"
Badge = namedtuple("Badge", "text fill")
BADGES = {MICROPYTHON: Badge("MP", "#1F7A6D"), CIRCUITPYTHON: Badge("CP", "#6B3FA0"), None: Badge("??", "#6E6E6E")}
FIRMWARE_NAMES = {MICROPYTHON: "MicroPython", CIRCUITPYTHON: "CircuitPython"}
SHARED_USB_SERIALS = {"123456"}
# the MicroPython RP2 build, Pimoroni's build, the Tildagon, the 2022 badge, an EMF badge
MICROPYTHON_USB = {(0x2E8A, 0x0005), (0x16D0, 0x08C7), (0x16D0, 0x120E), (0x16D0, 0x1114), (0x303A, 0x4001)}
ADAFRUIT_VID, RASPBERRY_PI_VID = 0x239A, 0x2E8A
MAX_NICKNAME = 24


def usb_key(vid, pid, serial):
    return "%04x:%04x:%s" % (vid, pid or 0, serial or "")


def unique_usb_serial(serial):
    """False for a serial that more than one board reports (or none at all): the USB key cannot name one board."""
    return bool(serial) and serial not in SHARED_USB_SERIALS and set(serial) != {"0"}


def guess_kind(vid, pid, manufacturer=""):
    """MicroPython, CircuitPython or None from the USB strings alone (right for the boards seen so far, not a proof)."""
    if (manufacturer or "") == "MicroPython" or (vid, pid) in MICROPYTHON_USB:
        return MICROPYTHON
    if vid == ADAFRUIT_VID or vid == RASPBERRY_PI_VID:
        return CIRCUITPYTHON            # a Raspberry Pi vendor id that is not the MicroPython build's own
    return None


def firmware_kind(settings, key, vid, pid, manufacturer=""):
    """What the app learned when it connected to this board, else the USB guess."""
    learned = settings.get("boards.firmware").get(segment(key))
    return learned if learned in (MICROPYTHON, CIRCUITPYTHON) else guess_kind(vid, pid, manufacturer)


def remember_firmware(settings, key, kind):
    """Record what a board runs: learned at connect, or after the firmware tools installed it."""
    if key and kind in (MICROPYTHON, CIRCUITPYTHON):
        table = settings.get("boards.firmware")
        if table.get(segment(key)) != kind:
            table[segment(key)] = kind
            settings.set("boards.firmware", table)


def remember_chip(settings, usb, chip):
    """Learn which chip id sits behind a USB key, so a nickname set on the chip shows before the next connect."""
    if usb and chip and usb != chip and unique_usb_serial(usb.rsplit(":", 1)[-1]):
        ids = settings.get("boards.ids")
        if ids.get(segment(usb)) != chip:
            ids[segment(usb)] = chip
            settings.set("boards.ids", ids)


def clean_nickname(text):
    """Control characters removed, runs of spaces folded, at most MAX_NICKNAME characters; '' when nothing is left."""
    text = re.sub(r"[\x00-\x1f\x7f]", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()[:MAX_NICKNAME].strip()


def nickname_of(settings, board):
    """The nickname of a board known by its chip id or USB key ('' if none)."""
    return settings.get("boards.nicknames").get(segment(board), "") if board else ""


def nickname_before_connect(settings, usb):
    """The nickname for a listed port, from its USB key: only if the USB serial is unique."""
    if not usb or not unique_usb_serial(usb.rsplit(":", 1)[-1]):
        return ""
    return nickname_of(settings, settings.get("boards.ids").get(segment(usb))) or nickname_of(settings, usb)


def set_nickname(settings, board, text):
    """Name a board (by its chip id once connected, else its USB key); blank removes the name. Returns the text kept."""
    text = clean_nickname(text)
    table = settings.get("boards.nicknames")
    if text:
        table[segment(board)] = text
    else:
        table.pop(segment(board), None)
    settings.set("boards.nicknames", table)
    return text


def other_boards_named(settings, text, board):
    """Keys of other boards that already carry this nickname (the Name dialog warns, it does not forbid)."""
    wanted = clean_nickname(text).lower()
    names = settings.get("boards.nicknames")
    return [k for k, v in names.items() if wanted and v.lower() == wanted and k != segment(board)]


def quoted(nickname):
    return '"%s"' % nickname if nickname else ""


def connection_label(port, nickname="", model="", firmware=""):
    """'/dev/ttyACM4  "Bench CP"  Pico 2 (rp2350a)  CircuitPython 10.3.1': the port, the owner's name, what it is."""
    return "  ".join(x for x in (port, quoted(nickname), model, firmware) if x)


def shared_usb_warning(board):
    """For the Name dialog: a warning when the board is known only by a USB id that boards may share ('' if fine)."""
    if board and not board.startswith("chip:") and not unique_usb_serial(board.rsplit(":", 1)[-1]):
        return ("This board could not give its chip id and its USB serial is shared with other boards, so the name "
                "would apply to every board with that serial.")
    return ""
