"""Serial port discovery shared by the GUIs."""


def list_ports():
    try:
        from serial.tools import list_ports as lp
    except ImportError:
        return []
    ports = sorted(lp.comports(), key=lambda p: (p.vid != 0x2E8A, p.device))   # RP2 boards first
    return ["%s  %s" % (p.device, p.description) for p in ports]


def board_id(device):
    """A key that stays the same for one board whichever /dev/ttyACMx it appears as: vid:pid:serial, else the path."""
    try:
        from serial.tools import list_ports as lp
        for p in lp.comports():
            if p.device == device and p.vid is not None:
                return "%04x:%04x:%s" % (p.vid, p.pid or 0, p.serial_number or "")
    except ImportError:
        pass
    return device


def device_of(entry):
    """'/dev/ttyACM0  TiLDAGON - Board CDC' -> '/dev/ttyACM0'."""
    return entry.split("  ")[0].strip()
