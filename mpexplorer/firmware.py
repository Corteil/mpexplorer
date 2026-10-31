"""Install MicroPython on a board in its bootloader: boards with a UF2 drive so far. See docs/FIRMWARE_PLAN.md."""
import os
import re
import struct
import time
from dataclasses import dataclass

from .errors import BackendError, Cancelled
from .transport import RawREPL, TransportError

INFO_FILE = "INFO_UF2.TXT"
BLOCK = 512
MAX_IMAGE = 32 * 1024 * 1024
FLASH_START, FLASH_END = 0x10000000, 0x11000000      # the XIP window a UF2 for flash must stay in
MAGIC_START0, MAGIC_START1, MAGIC_END = 0x0A324655, 0x9E5D5157, 0x0AB16F30
FLAG_NOT_MAIN_FLASH, FLAG_HAS_FAMILY = 0x0001, 0x2000
MAX_PAYLOAD = 476
FAMILY_ABSOLUTE = 0xE48BFF57                         # SDK UF2s start with a block of this family: not part of the image

# family id -> the name of the CPU the image runs on
CHIP_FAMILIES = {
    "rp2040": {0xE48BFF56: "Arm"},
    "rp2350": {0xE48BFF59: "Arm", 0xE48BFF5A: "RISC-V"},
}
# the bootrom's Board-ID; a maker can change the RP2350's in OTP, which then reads as unknown
BOARD_IDS = {"RPI-RP2": "rp2040", "RP2350": "rp2350"}
CHIP_NAMES = {"rp2040": "RP2040", "rp2350": "RP2350"}
RP2_MICROPYTHON_USB = (0x2E8A, 0x0005)


class FirmwareError(BackendError):
    """A firmware image or an install was refused or failed."""


@dataclass
class BootDrive:
    path: str
    model: str
    board_id: str
    chip: str | None            # None: a UF2 bootloader this app has no rule for


@dataclass
class Uf2Info:
    family: int
    cpu: str
    blocks: int
    size: int                   # bytes of firmware, without the UF2 framing


@dataclass
class Installed:
    port: str
    release: str
    machine: str


def parse_info(text):
    """INFO_UF2.TXT is 'Key: value' lines after a title line; returns {key: value}."""
    fields = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()
    return fields


def read_drive(path):
    """The BootDrive at a folder, or None unless it holds a readable INFO_UF2.TXT with a Board-ID."""
    try:
        with open(os.path.join(path, INFO_FILE), encoding="ascii", errors="replace") as f:
            fields = parse_info(f.read(4096))
    except OSError:
        return None
    board_id = fields.get("Board-ID")
    if not board_id:
        return None
    return BootDrive(path, fields.get("Model", ""), board_id, BOARD_IDS.get(board_id))


def _mount_points(mounts_file):
    points = []
    with open(mounts_file, encoding="utf-8", errors="surrogateescape") as f:
        for line in f:
            parts = line.split()
            if len(parts) >= 3 and parts[2] in ("vfat", "msdos"):
                points.append(re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), parts[1]))   # \040 is a space
    return points


def detect(mounts_file="/proc/self/mounts"):
    """Mounted UF2 bootloader drives. A drive counts only if it holds INFO_UF2.TXT, never by its name."""
    try:
        points = _mount_points(mounts_file)
    except OSError:
        return []
    return [d for d in map(read_drive, points) if d]


def check_uf2(path, chip):
    """Refuse anything but one complete flash image for this chip; returns its Uf2Info. Reads the whole file."""
    families = CHIP_FAMILIES.get(chip)
    if not families:
        raise FirmwareError("No firmware rule for this bootloader (%s)." % chip)
    name = os.path.basename(path)
    try:
        size = os.path.getsize(path)
        if size == 0 or size % BLOCK or size > MAX_IMAGE:
            raise FirmwareError("%s is not a UF2 file: its size is %d bytes." % (name, size))
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        raise FirmwareError("Cannot read %s: %s" % (name, e))
    found, seen, total, payload = None, set(), None, 0
    for offset in range(0, len(data), BLOCK):
        m0, m1, flags, addr, count, number, blocks, family = struct.unpack_from("<8I", data, offset)
        if (m0, m1) != (MAGIC_START0, MAGIC_START1) or struct.unpack_from("<I", data, offset + 508)[0] != MAGIC_END:
            raise FirmwareError("%s is not a UF2 file: bad block %d." % (name, offset // BLOCK))
        if flags & FLAG_NOT_MAIN_FLASH:
            continue
        if not flags & FLAG_HAS_FAMILY:
            raise FirmwareError("%s does not say which chip it is for." % name)
        if family == FAMILY_ABSOLUTE:
            continue
        if family not in families:
            raise FirmwareError("%s is not for the %s (family %08X)." % (name, CHIP_NAMES[chip], family))
        if found is None:
            found, total = family, blocks
        if family != found or blocks != total:
            raise FirmwareError("%s holds more than one image." % name)
        if not 0 < count <= MAX_PAYLOAD or not FLASH_START <= addr < FLASH_END or number >= total or number in seen:
            raise FirmwareError("%s has a bad block (%d)." % (name, number))
        seen.add(number)
        payload += count
    if found is None:
        raise FirmwareError("%s holds no firmware for the %s." % (name, CHIP_NAMES[chip]))
    if len(seen) != total:
        raise FirmwareError("%s is incomplete: %d of %d blocks." % (name, len(seen), total))
    return Uf2Info(found, families[found], total, payload)


def wait_gone(drive, timeout=20.0, poll=0.1):
    """The bootloader drops its drive when it restarts the board; True once it has."""
    end = time.monotonic() + timeout
    while True:
        if read_drive(drive.path) is None:
            return True
        if time.monotonic() >= end:
            return False
        time.sleep(poll)


def install(image, drive, on_progress=None, cancelled=None, gone_timeout=20.0):
    """Copy a checked UF2 to a bootloader drive and wait for the board to restart.

    A cancel can leave the flash part written: the board stays in its bootloader, ready for another try. The board may
    restart as the last data lands, so errors from then on are expected; the drive going away decides the result."""
    if drive.chip is None:
        raise FirmwareError("No firmware rule for this bootloader (%s)." % drive.board_id)
    info = check_uf2(image, drive.chip)
    if read_drive(drive.path) != drive:
        raise FirmwareError("The board at %s has gone or changed. Scan again." % drive.path)
    with open(image, "rb") as f:
        data = f.read()
    view = memoryview(data)
    step = 64 * BLOCK
    sent = 0
    try:
        out = open(os.path.join(drive.path, "firmware.uf2"), "wb", buffering=0)
    except OSError as e:
        raise FirmwareError("Cannot write to %s: %s" % (drive.path, e))
    try:
        while sent < len(data):
            if cancelled and cancelled():
                raise Cancelled()
            try:
                sent += out.write(view[sent:sent + step])
            except OSError as e:
                if len(data) - sent > step:
                    raise FirmwareError("Writing to the board failed after %d of %d bytes: %s" % (sent, len(data), e))
                break                   # the last chunk: the board may already be restarting
            if on_progress:
                on_progress(sent, len(data))
        try:
            os.fsync(out.fileno())
        except OSError:
            pass
    finally:
        try:
            out.close()
        except OSError:
            pass
    if not wait_gone(drive, gone_timeout):
        raise FirmwareError("The board did not restart after the copy. It is still in its bootloader.")
    return info


def rp2_ports():
    """Serial ports that are MicroPython on an RP2 board."""
    try:
        from serial.tools import list_ports as lp
    except ImportError:
        return set()
    return {p.device for p in lp.comports() if (p.vid, p.pid) == RP2_MICROPYTHON_USB}


def wait_for_new_port(before, timeout=15.0, poll=0.2, ports=rp2_ports):
    """The port that appears after an install; `before` is the set taken before it, so other Picos do not count."""
    end = time.monotonic() + timeout
    while True:
        new = sorted(set(ports()) - set(before))
        if new:
            return new[0]
        if time.monotonic() >= end:
            raise FirmwareError("The board restarted but no MicroPython serial port appeared.")
        time.sleep(poll)


def identify(port, chip, timeout=10.0, poll=0.5, open_board=RawREPL.open_board):
    """Prove the install: talk to the new firmware and check it runs on the expected chip."""
    end = time.monotonic() + timeout
    while True:
        try:
            repl, _ = open_board(port)      # a port that has just appeared may not answer yet
            break
        except (TransportError, OSError) as e:
            if time.monotonic() >= end:
                raise FirmwareError("MicroPython did not answer on %s: %s" % (port, e))
            time.sleep(poll)
    try:
        text = repl.exec("import os;u=os.uname();print(u.release);print(u.machine)").decode()
    except TransportError as e:
        raise FirmwareError("MicroPython did not answer on %s: %s" % (port, e))
    finally:
        repl.close(reset=True)
    lines = text.strip().splitlines()
    if len(lines) != 2:
        raise FirmwareError("Unexpected reply from %s: %r" % (port, text))
    release, machine = lines
    if CHIP_NAMES[chip] not in machine:
        raise FirmwareError("%s is running on '%s', not the expected %s." % (port, machine, CHIP_NAMES[chip]))
    return Installed(port, release, machine)
