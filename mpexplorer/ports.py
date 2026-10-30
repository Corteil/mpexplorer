"""Serial port discovery shared by the GUIs."""
from .settings import segment


# USB ids of the boards this tool targets, for a plain-English name (the OS only says "Board in FS mode - Board CDC")
KNOWN_BOARDS = {
    (0x2E8A, 0x0005): "Raspberry Pi Pico / Pico 2 (MicroPython)",
    (0x303A, 0x4001): "EMF badge / TiLDAGON (MicroPython)",
}


def _key(p):
    return "%04x:%04x:%s" % (p.vid, p.pid or 0, p.serial_number or "")


def port_label(p, names=None):
    """'/dev/ttyACM1  Raspberry Pi Pico / Pico 2 (MicroPython)  #e6614c31': what it is, then the board's own serial
    number, which tells two boards of the same kind apart (ttyACM1/2 follow the order they were plugged in)."""
    # a name learned by connecting (the exact model) beats the USB-id guess: a Pico and a Pico 2 look the same on USB
    name = (names or {}).get(segment(_key(p))) if p.vid is not None else None
    name = name or KNOWN_BOARDS.get((p.vid, p.pid)) or p.product or p.description or "serial port"
    if p.serial_number:
        name += "  #%s" % p.serial_number[-8:].lower()
    return "%s  %s" % (p.device, name)


def list_ports(names=None):
    """Dropdown entries; names maps board id -> the model learned at an earlier connect (settings 'boards.names')."""
    try:
        from serial.tools import list_ports as lp
    except ImportError:
        return []
    # onboard UARTs (/dev/ttyAMA*, ttyS*) have no USB ids and no description: they are not boards, so leave them out
    ports = [p for p in lp.comports() if p.vid is not None]
    ports.sort(key=lambda p: (p.vid != 0x2E8A, p.device))   # RP2 boards first
    return [port_label(p, names) for p in ports]


def board_id(device):
    """A key that stays the same for one board whichever /dev/ttyACMx it appears as: vid:pid:serial, else the path."""
    try:
        from serial.tools import list_ports as lp
        for p in lp.comports():
            if p.device == device and p.vid is not None:
                return _key(p)
    except ImportError:
        pass
    return device


def device_of(entry):
    """'/dev/ttyACM0  TiLDAGON - Board CDC' -> '/dev/ttyACM0'."""
    return entry.split("  ")[0].strip()
