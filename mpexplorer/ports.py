"""Serial port discovery shared by the GUIs."""


def list_ports():
    try:
        from serial.tools import list_ports as lp
    except ImportError:
        return []
    ports = sorted(lp.comports(), key=lambda p: (p.vid != 0x2E8A, p.device))   # RP2 boards first
    return ["%s  %s" % (p.device, p.description) for p in ports]
