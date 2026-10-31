"""Install MicroPython on a board in its bootloader: UF2 drives, and esptool for badges. See docs/FIRMWARE_PLAN.md."""
import hashlib
import importlib.util
import os
import re
import select
import shutil
import struct
import subprocess
import sys
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
CHIP_NAMES = {"rp2040": "RP2040", "rp2350": "RP2350", "esp32s3": "ESP32-S3"}
MACHINE_NAMES = {"esp32s3": "ESP32S3"}               # how os.uname().machine spells it, if not as CHIP_NAMES does
RP2_MICROPYTHON_USB = (0x2E8A, 0x0005)
ESP32S3_ROM_USB = (0x303A, 0x1001)                   # "USB JTAG/serial debug unit", seen on a real Tildagon
TILDAGON_USB = (0x16D0, 0x120E)
ESP_IMAGE_MAGIC, ESP32S3_CHIP_ID, ESP_FLASH_8MB = 0xE9, 9, 3
ESP_PARTITION_TABLE, ESP_APP_OFFSET, ESP_FLASH_BYTES = 0x8000, 0x10000, 8 * 1024 * 1024
ESPTOOL_MAJOR = 5
ESPTOOL_LOG = "esptool.log"                          # every run's full output, for when the short message is not enough
INSTALL_ESPTOOL = "Installing on this board needs esptool %d. Install it with: pipx install esptool" % ESPTOOL_MAJOR


class FirmwareError(BackendError):
    """A firmware image or an install was refused or failed."""


@dataclass
class BootDrive:
    path: str
    model: str
    board_id: str
    chip: str | None            # None: a UF2 bootloader this app has no rule for
    method: str = "uf2"         # uf2: path is a drive; esptool: path is a serial port


@dataclass
class Uf2Info:
    family: int
    cpu: str
    blocks: int
    size: int                   # bytes of firmware, without the UF2 framing


@dataclass
class EspInfo:
    cpu: str
    size: int


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


def _ports_with(usb):
    try:
        from serial.tools import list_ports as lp
    except ImportError:
        return set()
    return {p.device for p in lp.comports() if (p.vid, p.pid) == usb}


def detect_rom():
    """ESP32-S3 chips in their ROM bootloader, which appear as a serial port."""
    return [BootDrive(port, "ESP32-S3 ROM bootloader", "ESP32-S3", "esp32s3", "esptool")
            for port in sorted(_ports_with(ESP32S3_ROM_USB))]


def detect_all(mounts_file="/proc/self/mounts"):
    return detect(mounts_file) + detect_rom()


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


def check_esp_image(path):
    """Refuse anything but a complete 8 MB image for an ESP32-S3 badge. An app-only micropython.bin is what the badges'
    GitHub releases hold: it starts like an image, but written at 0 on a blank chip it would not boot."""
    name = os.path.basename(path)
    try:
        size = os.path.getsize(path)
        if size > ESP_FLASH_BYTES:
            raise FirmwareError("%s is larger than the flash (%d bytes)." % (name, size))
        with open(path, "rb") as f:
            data = f.read(ESP_APP_OFFSET + 1)
    except OSError as e:
        raise FirmwareError("Cannot read %s: %s" % (name, e))
    if len(data) < 16 or data[0] != ESP_IMAGE_MAGIC:
        raise FirmwareError("%s is not an ESP firmware image." % name)
    if struct.unpack_from("<H", data, 12)[0] != ESP32S3_CHIP_ID:
        raise FirmwareError("%s is not for the ESP32-S3." % name)
    if data[3] >> 4 != ESP_FLASH_8MB:
        raise FirmwareError("%s is not for 8 MB of flash." % name)
    if len(data) <= ESP_APP_OFFSET or data[ESP_PARTITION_TABLE:ESP_PARTITION_TABLE + 2] != b"\xaa\x50" \
            or data[ESP_APP_OFFSET] != ESP_IMAGE_MAGIC:
        raise FirmwareError("%s is not a complete image: it needs the bootloader, partition table and app "
                            "(a merged firmware file)." % name)
    return EspInfo("Xtensa", size)


def check_image(path, chip):
    return check_esp_image(path) if chip == "esp32s3" else check_uf2(path, chip)


def _esptool_version(argv):
    try:
        out = subprocess.run(argv + ["version"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"\bv?(\d+)\.\d+", out)
    return int(m[1]) if m else None


def find_esptool():
    """The command to run esptool: this Python's module, else a program (pipx puts it in ~/.local/bin, which a
    desktop launcher's PATH may leave out). Only one major version: its commands were renamed between 4 and 5."""
    candidates = []
    if importlib.util.find_spec("esptool"):
        candidates.append([sys.executable, "-m", "esptool"])
    for program in (shutil.which("esptool"), os.path.expanduser("~/.local/bin/esptool")):
        if program and os.path.exists(program):
            candidates.append([program])
    for argv in candidates:
        if _esptool_version(argv) == ESPTOOL_MAJOR:
            return argv
    raise FirmwareError(INSTALL_ESPTOOL)


def esptool_log_path():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "mpexplorer", ESPTOOL_LOG)


def _esptool_tail(lines):
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", "\n".join(lines))
    return "\n".join(line for line in text.splitlines() if line.strip())[-600:]


def flash_esptool(image, drive, on_progress=None, cancelled=None, tool=None, stall=90.0):
    """Write a merged image at offset 0. esptool checks what it wrote. A cancel or failure can leave the flash part
    written; a board in its ROM bootloader stays there, ready for another try."""
    info = check_esp_image(image)
    if drive.path not in _ports_with(ESP32S3_ROM_USB) and not tool:
        raise FirmwareError("The board at %s has gone. Scan again." % drive.path)
    # no-reset before connecting: the chip is already in its ROM. Watchdog reset after: on a real Tildagon esptool's
    # default "hard reset via RTS" left it in the ROM, and this one starts the new firmware
    argv = (tool or find_esptool()) + ["--chip", "esp32s3", "--port", drive.path, "--baud", "460800",
                                       "--before", "no-reset", "--after", "watchdog-reset",
                                       "write-flash", "0", image]
    log_path = esptool_log_path()
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except OSError as e:
        raise FirmwareError("Cannot run esptool: %s" % e)
    lines, partial, last = [], b"", time.monotonic()
    log = open(log_path, "wb")
    log.write((" ".join(argv) + "\n").encode())
    try:
        fd = proc.stdout.fileno()
        while True:
            if cancelled and cancelled():
                raise Cancelled()
            if time.monotonic() - last > stall:
                raise FirmwareError("esptool stopped making progress.\n%s" % _esptool_tail(lines))
            if not select.select([fd], [], [], 0.1)[0]:
                continue
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            last = time.monotonic()
            log.write(chunk)
            log.flush()
            parts = re.split(rb"[\r\n]+", partial + chunk)     # its progress bar rewrites one line with \r
            partial = parts.pop()
            for part in parts:
                line = part.decode("utf-8", "replace")
                lines = (lines + [line])[-40:]
                m = re.search(r"Writing at .*?(\d+(?:\.\d+)?) ?%", line)
                if m and on_progress:
                    on_progress(float(m[1]), 100)
        if partial:
            lines.append(partial.decode("utf-8", "replace"))
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    finally:
        proc.stdout.close()
        log.close()
    if proc.wait() != 0:
        raise FirmwareError("esptool failed.\n%s\n\nFull output: %s" % (_esptool_tail(lines), log_path))
    return info


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
    return _ports_with(RP2_MICROPYTHON_USB)


def tildagon_ports():
    return _ports_with(TILDAGON_USB)


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
    if MACHINE_NAMES.get(chip, CHIP_NAMES[chip]) not in machine:
        raise FirmwareError("%s is running on '%s', not the expected %s." % (port, machine, CHIP_NAMES[chip]))
    return Installed(port, release, machine)


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def install_and_verify(image, drive, on_progress=None, on_stage=None, cancelled=None):
    """The whole install: copy, wait for the restart, then talk to the new firmware. Returns an Installed."""
    stage = on_stage or (lambda text: None)
    if drive.method == "esptool":
        before = tildagon_ports()
        stage("Writing the firmware")
        flash_esptool(image, drive, on_progress, cancelled)
        stage("Waiting for the badge to start")
        try:
            port = wait_for_new_port(before, timeout=60.0, ports=tildagon_ports)
        except FirmwareError:
            raise FirmwareError("The firmware was written and checked, but the badge did not come back. "
                                "Unplug it and plug it in again; it should then start normally.")
        stage("Checking the board on %s" % port)
        return identify(port, drive.chip, timeout=30.0)
    before = rp2_ports()
    stage("Copying the firmware")
    install(image, drive, on_progress, cancelled)
    stage("Waiting for MicroPython to start")
    port = wait_for_new_port(before)
    stage("Checking the board on %s" % port)
    return identify(port, drive.chip)
