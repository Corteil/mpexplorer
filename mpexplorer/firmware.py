"""Install MicroPython on a board in its bootloader: UF2 drives, and esptool for badges. See docs/FIRMWARE_PLAN.md."""
import base64
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
from .ports import short_model
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
ESP_IMAGE_MAGIC, ESP32S3_CHIP_ID = 0xE9, 9
ESP_PARTITION_TABLE, ESP_APP_OFFSET, ESP_FLASH_BYTES = 0x8000, 0x10000, 8 * 1024 * 1024
ESPTOOL_MAJOR = 5
ESPTOOL_STAGES = (("Erasing flash", "Erasing flash"), ("Writing at", "Writing firmware"), ("Verifying", "Verifying"),
                  ("Reading from", "Reading flash"))
UF2_PAYLOAD = 256
UF2_FAMILY_FOR_BACKUP = {"rp2040": 0xE48BFF56, "rp2350": 0xE48BFF59}
REPL_CHUNK = 30000                                   # a multiple of 3, so each piece of base64 stands alone
ESPTOOL_LOG = "esptool.log"                          # every run's full output, for when the short message is not enough
INSTALL_ESPTOOL = "Installing on this board needs esptool %d. Install it with: pipx install esptool" % ESPTOOL_MAJOR


class FirmwareError(BackendError):
    """A firmware image or an install was refused or failed."""


@dataclass
class Badge:
    chip: tuple                 # type, MB of flash, MB of PSRAM, embedded flash: as read from a real one
    usb: tuple
    soft_reset: bool            # the 2022 badge drops off USB until replugged after a soft reset
    label: str
    boot_from_repl: bool        # machine.bootloader() works (reported by the owner on a 2026 badge; not on a 2022 one)
    steps: str                  # how to reach the bootloader by hand


BADGES = {
    "TILDAGON": Badge(("ESP32-S3-PICO-1", 8, 2, True), (0x16D0, 0x120E), True, "Tildagon (2024, 2026)", True,
                      "power it off from its menu, then hold boop while plugging USB into USB in. Keep holding for a "
                      "few seconds"),
    "TIDAL": Badge(("ESP32-S3", 8, None, True), (0x16D0, 0x1114), False, "TiDAL (2022 badge)", False,
                   "hold the bottom right button, tap bottom-left, then release both"),
}


@dataclass
class EspChip:
    type: str
    flash_mb: int | None
    psram_mb: int | None
    embedded_flash: bool = True

    @property
    def badge(self):
        """The badge this chip reads as, else None. No badge id can be read: another board with the same chip and
        memory looks the same."""
        details = (self.type, self.flash_mb, self.psram_mb, self.embedded_flash)
        return next((name for name, badge in BADGES.items() if badge.chip == details), None)

    @property
    def text(self):
        return "%s, %s flash, %s PSRAM" % (self.type, "%d MB" % self.flash_mb if self.flash_mb else "unknown",
                                           "%d MB" % self.psram_mb if self.psram_mb else "no")


@dataclass
class BootDrive:
    path: str
    model: str
    board_id: str
    chip: str | None            # None: a UF2 bootloader this app has no rule for
    method: str = "uf2"         # uf2: path is a drive; esptool: path is a serial port
    esp_chip: EspChip | None = None     # read from the chip before anything is offered for it


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
class RunningInfo:
    machine: str
    release: str
    flash_mb: int | None


@dataclass
class Backup:
    path: str
    size: int
    source: str


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


def detect_running():
    """RP2 boards running MicroPython: they can be backed up, but need their bootloader for anything else."""
    return [BootDrive(port, "MicroPython", "MicroPython", None, "repl") for port in sorted(rp2_ports())]


def detect_running_badges():
    """Badges in normal use, named by their USB id: nothing is sent to them, so their app keeps running."""
    return [BootDrive(port, BADGES[name].label, name, None, "badge")
            for name in BADGES for port in sorted(badge_ports(name))]


def detect_all(mounts_file="/proc/self/mounts"):
    return detect(mounts_file) + detect_rom() + detect_running() + detect_running_badges()


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


def check_esp_image(path, flash_bytes=ESP_FLASH_BYTES):
    """Refuse anything but a complete ESP32-S3 image that fits this flash. An app-only micropython.bin is what the
    badges' GitHub releases hold: it starts like an image, but written at 0 on a blank chip it would not boot."""
    name = os.path.basename(path)
    try:
        size = os.path.getsize(path)
        if size > flash_bytes:
            raise FirmwareError("%s is larger than the flash (%d bytes)." % (name, size))
        with open(path, "rb") as f:
            data = f.read(ESP_APP_OFFSET + 1)
    except OSError as e:
        raise FirmwareError("Cannot read %s: %s" % (name, e))
    if len(data) < 16 or data[0] != ESP_IMAGE_MAGIC:
        raise FirmwareError("%s is not an ESP firmware image." % name)
    if struct.unpack_from("<H", data, 12)[0] != ESP32S3_CHIP_ID:
        raise FirmwareError("%s is not for the ESP32-S3." % name)
    wanted = (1 << (data[3] >> 4)) * 1024 * 1024        # the header's size field: 0 is 1 MB, 3 is 8 MB
    if wanted > flash_bytes:
        raise FirmwareError("%s is for %d MB of flash; this chip has %d MB."
                            % (name, wanted // (1024 * 1024), flash_bytes // (1024 * 1024)))
    if len(data) <= ESP_APP_OFFSET or data[ESP_PARTITION_TABLE:ESP_PARTITION_TABLE + 2] != b"\xaa\x50" \
            or data[ESP_APP_OFFSET] != ESP_IMAGE_MAGIC:
        raise FirmwareError("%s is not a complete image: it needs the bootloader, partition table and app "
                            "(a merged firmware file)." % name)
    return EspInfo("Xtensa", size)


def check_image(path, chip, flash_bytes=ESP_FLASH_BYTES):
    return check_esp_image(path, flash_bytes) if chip == "esp32s3" else check_uf2(path, chip)


def parse_esp_chip(text):
    """What esptool's flash-id prints; FirmwareError if it does not say what the chip is."""
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
    kind = re.search(r"Chip type:\s*(\S+)", text)
    if not kind:
        raise FirmwareError("esptool did not say what this chip is.\n%s" % text[-300:])
    flash = re.search(r"Detected flash size:\s*(\d+)MB", text)
    psram = re.search(r"Embedded PSRAM (\d+)MB", text)
    return EspChip(kind[1], int(flash[1]) if flash else None, int(psram[1]) if psram else None,
                   "Embedded Flash" in text)


def read_esp_chip(port, tool=None):
    """Ask what this chip is, without writing or leaving the bootloader. Not --no-stub: it fails with "Failed to
    configure SPI flash pins" if an earlier esptool run left its stub running, which this works with."""
    argv = (tool or find_esptool()) + ["--chip", "esp32s3", "--port", port, "--before", "no-reset", "--after",
                                       "no-reset", "flash-id"]
    try:
        run = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
                             timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        raise FirmwareError("Cannot read the chip on %s: %s" % (port, e))
    if run.returncode != 0:
        raise FirmwareError("Cannot read the chip on %s. Is it in its bootloader?\n%s"
                            % (port, _esptool_tail(run.stdout.splitlines())))
    return parse_esp_chip(run.stdout)


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


def flash_esptool(image, drive, on_progress=None, cancelled=None, tool=None, stall=90.0, erase=False,
                  on_stage=None):
    """Write a merged image at offset 0, after erasing the whole flash if asked. esptool checks what it wrote. A cancel
    or failure can leave the flash part written; a board in its ROM bootloader stays there, ready for another try."""
    flash_mb = drive.esp_chip.flash_mb if drive.esp_chip else None
    info = check_esp_image(image, flash_mb * 1024 * 1024 if flash_mb else ESP_FLASH_BYTES)
    if drive.path not in _ports_with(ESP32S3_ROM_USB) and not tool:
        raise FirmwareError("The board at %s has gone. Scan again." % drive.path)
    # Watchdog reset after: on a real Tildagon esptool's default "hard reset via RTS" left it in the ROM, and this one
    # starts the new firmware
    argv = (tool or find_esptool()) + _rom_args(drive, "watchdog-reset") + ["write-flash"] + \
        (["--erase-all"] if erase else []) + ["0", image]
    if erase:
        stall = max(stall, 300.0)       # a chip erase prints nothing for as long as it takes
    _run_esptool(argv, on_progress, on_stage, cancelled, stall)
    return info


def _rom_args(drive, after):
    """no-reset before connecting: the chip is already in its ROM."""
    return ["--chip", "esp32s3", "--port", drive.path, "--baud", "460800", "--before", "no-reset", "--after", after]


def _run_esptool(argv, on_progress, on_stage, cancelled, stall):
    log_path = esptool_log_path()
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except OSError as e:
        raise FirmwareError("Cannot run esptool: %s" % e)
    lines, partial, last, shown = [], b"", time.monotonic(), None
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
                stage = next((text for mark, text in ESPTOOL_STAGES if mark in line), None)
                if stage and stage != shown:
                    shown = stage
                    if on_stage:
                        on_stage(stage)
                m = re.search(r"(?:Writing at|Reading from) .*?(\d+(?:\.\d+)?) ?%", line)
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


def check_nuke_uf2(path):
    """The erase tool is a program run from RAM: check that it is one, for every chip it says it covers."""
    name = os.path.basename(path)
    try:
        with open(path, "rb") as f:
            data = f.read(MAX_IMAGE + 1)
    except OSError as e:
        raise FirmwareError("Cannot read %s: %s" % (name, e))
    if not data or len(data) % BLOCK or len(data) > MAX_IMAGE:
        raise FirmwareError("%s is not a UF2 file." % name)
    seen = {}
    for offset in range(0, len(data), BLOCK):
        m0, m1, flags, addr, _count, number, total, family = struct.unpack_from("<8I", data, offset)
        if (m0, m1) != (MAGIC_START0, MAGIC_START1) or struct.unpack_from("<I", data, offset + 508)[0] != MAGIC_END:
            raise FirmwareError("%s is not a UF2 file: bad block %d." % (name, offset // BLOCK))
        if not 0x20000000 <= addr < 0x20100000:
            raise FirmwareError("%s writes outside RAM: refusing it as an erase tool." % name)
        seen.setdefault((family, total), set()).add(number)
    if not seen or any(len(numbers) != total for (_family, total), numbers in seen.items()):
        raise FirmwareError("%s is incomplete." % name)


def to_uf2(data, family, base=FLASH_START):
    """A flash image as a UF2, so a backup goes back on through the UF2 drive like any firmware."""
    total = (len(data) + UF2_PAYLOAD - 1) // UF2_PAYLOAD
    out = bytearray()
    for n in range(total):
        piece = data[n * UF2_PAYLOAD:(n + 1) * UF2_PAYLOAD]
        out += struct.pack("<8I", MAGIC_START0, MAGIC_START1, FLAG_HAS_FAMILY, base + n * UF2_PAYLOAD, UF2_PAYLOAD, n,
                           total, family)
        out += piece.ljust(MAX_PAYLOAD, b"\0") + struct.pack("<I", MAGIC_END)
    return bytes(out)


def _save_private(path, data):
    """Backups can hold Wi-Fi passwords: only this user may read them. Written whole, or not at all."""
    tmp = path + ".part"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


_REPL_SIZE = """
import uctypes,os
b=0x10000000
s=bytes(uctypes.bytearray_at(b,256))
n=0
for m in (1,2,4,8,16):
    if bytes(uctypes.bytearray_at(b+m*1048576,256))==s:
        n=m
        break
print(n)
print(os.uname().machine)
print(os.uname().release)
"""
_REPL_READ = """
import uctypes,binascii,hashlib
c=bytes(uctypes.bytearray_at(0x10000000+%d,%d))
print(binascii.b2a_base64(c).decode().strip())
print(binascii.hexlify(hashlib.sha256(c).digest()).decode())
"""


def read_running(port, open_board=RawREPL.open_board):
    """What a running RP2 board is. This stops its program and soft-resets it, which restarts its main.py."""
    try:
        repl, _ = open_board(port)
    except (TransportError, OSError) as e:
        raise FirmwareError("Cannot read %s: is it connected in the app? (%s)" % (port, e))
    try:
        lines = repl.exec(_REPL_SIZE, timeout=10).decode().strip().splitlines()
        return RunningInfo(lines[1], lines[2], int(lines[0]) or None)
    except (TransportError, ValueError, IndexError) as e:
        raise FirmwareError("Cannot read %s: %s" % (port, e))
    finally:
        repl.close(reset=True)


def enter_bootloader(port, timeout=20.0, poll=0.2, open_board=RawREPL.open_board, find=None):
    """Ask a running board to restart into its bootloader, with no button; returns the device when it appears. It drops
    off USB as soon as it obeys, so a lost connection here is the expected result, and the device appearing decides.
    find lists what to watch for: UF2 drives unless told otherwise."""
    find = find or detect
    before = {d.path for d in find()}
    try:
        repl, _ = open_board(port)
    except (TransportError, OSError) as e:
        raise FirmwareError("Cannot reach %s: is it connected in the app? (%s)" % (port, e))
    try:
        repl.exec("import machine\nmachine.bootloader()", timeout=2.0)
    except (TransportError, OSError):
        pass
    try:
        repl.close()
    except (TransportError, OSError):
        pass
    end = time.monotonic() + timeout
    while True:
        new = [d for d in find() if d.path not in before]
        if new:
            return new[0]
        if time.monotonic() >= end:
            raise FirmwareError("The board did not reach its bootloader. Use its buttons instead.")
        time.sleep(poll)


def device_label(drive, running=None):
    """The device list's text: what the board is, then where."""
    if drive.method == "repl":
        name = short_model(running.machine) if isinstance(running, RunningInfo) else "MicroPython board"
    elif drive.method == "badge":
        name = BADGES[drive.board_id].label
    elif drive.method == "esptool":
        chip = drive.esp_chip
        name = BADGES[chip.badge].label if chip and chip.badge else "ESP32-S3"
    else:
        name = CHIP_NAMES.get(drive.chip, "Unsupported (%s)" % drive.board_id)
    return "%s at %s" % (name, drive.path)


def can_enter_bootloader(drive):
    return drive.method == "repl" or (drive.method == "badge" and BADGES[drive.board_id].boot_from_repl)


def running_badge_hint(drive):
    if BADGES[drive.board_id].boot_from_repl:
        return "This badge is running normally. Press Put in bootloader, or use the steps above."
    return "This badge is running normally. To use this tool, follow the steps above."


PICO_STEPS = "hold the BOOTSEL button while plugging it in"
ESP32_STEPS = "hold its BOOT button while plugging it in (the name can differ between boards)"
OTHER_STEPS = "see the instructions for this board"


def bootloader_steps(drive):
    """How this kind of device reaches its bootloader, by hand."""
    if drive.method == "badge":
        return BADGES[drive.board_id].steps
    if drive.method == "esptool":
        chip = drive.esp_chip
        return BADGES[chip.badge].steps if chip and chip.badge else ESP32_STEPS
    return PICO_STEPS if drive.method == "repl" or drive.chip else OTHER_STEPS


def device_details(drive, running=None):
    """(name, value) lines for the device's details panel."""
    return _device_facts(drive, running) + [("How to reach its bootloader", bootloader_steps(drive))]


def _device_facts(drive, running):
    if drive.method == "badge":
        return [("State", "running normally"), ("Port", drive.path),
                ("Details", "more can be read once it is in its bootloader")]
    if drive.method == "repl":
        lines = [("State", "running MicroPython"), ("Port", drive.path)]
        if isinstance(running, RunningInfo):
            lines += [("Board", running.machine), ("MicroPython", running.release)]
            if running.flash_mb:
                lines.append(("Flash", "%d MB" % running.flash_mb))
        elif running:
            lines.append(("Details", running))
        else:
            lines.append(("Details", "reading (this restarts its program)"))
        return lines
    if drive.method == "esptool":
        lines = [("State", "in its ROM bootloader"), ("Port", drive.path)]
        chip = drive.esp_chip
        if chip:
            lines += [("Chip", chip.type), ("Flash", "%s MB" % chip.flash_mb if chip.flash_mb else "unknown"),
                      ("PSRAM", "%d MB" % chip.psram_mb if chip.psram_mb else "none")]
            if chip.badge:
                lines.append(("Reads as", "%s, though another board with this chip would read the same" % chip.badge))
        return lines
    lines = [("State", "in BOOTSEL (USB drive)"), ("Drive", drive.path), ("Model", drive.model),
             ("Board ID", drive.board_id)]
    if drive.chip:
        lines.append(("Exact board", "cannot be read in BOOTSEL: only the chip"))
    return lines


def backup_repl(port, path, on_progress=None, cancelled=None, open_board=RawREPL.open_board):
    """Read a running RP2 board's whole flash through the raw REPL into a UF2 at `path`. Only reads the board, then
    soft-resets it. The flash size is found by where its contents repeat in the address space."""
    try:
        repl, _ = open_board(port)
    except (TransportError, OSError) as e:
        raise FirmwareError("MicroPython did not answer on %s: %s" % (port, e))
    try:
        lines = repl.exec(_REPL_SIZE, timeout=10).decode().strip().splitlines()
        size, machine = int(lines[0]) * 1024 * 1024, lines[1]
        chip = next((c for c in UF2_FAMILY_FOR_BACKUP if CHIP_NAMES[c] in machine), None)
        if not chip:
            raise FirmwareError("Backing up %s is not supported." % machine)
        if not size:
            raise FirmwareError("Cannot work out the flash size of %s." % machine)
        data = bytearray()
        while len(data) < size:
            if cancelled and cancelled():
                raise Cancelled()
            n = min(REPL_CHUNK, size - len(data))
            text, sha = repl.exec(_REPL_READ % (len(data), n), timeout=30).decode().strip().splitlines()
            piece = base64.b64decode(text)
            if len(piece) != n or hashlib.sha256(piece).hexdigest() != sha:
                raise FirmwareError("A read from the board at offset %d did not check out." % len(data))
            data += piece
            if on_progress:
                on_progress(len(data), size)
    except (TransportError, ValueError, IndexError) as e:
        raise FirmwareError("Reading the flash failed: %s" % e)
    finally:
        repl.close(reset=True)
    _save_private(path, to_uf2(bytes(data), UF2_FAMILY_FOR_BACKUP[chip]))
    return Backup(path, size, machine)


def backup_esptool(drive, path, on_progress=None, on_stage=None, cancelled=None, tool=None):
    """Read an ESP chip's whole flash to `path`, which goes back on as a firmware file. It stays in its bootloader."""
    mb = drive.esp_chip.flash_mb if drive.esp_chip else None
    if not mb:
        raise FirmwareError("The flash size of the board on %s is not known." % drive.path)
    tmp = path + ".part"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.close(fd)
    size = mb * 1024 * 1024
    _run_esptool((tool or find_esptool()) + _rom_args(drive, "no-reset") + ["read-flash", "0", str(size), tmp],
                 on_progress, on_stage, cancelled, 90.0)
    if os.path.getsize(tmp) != size:
        raise FirmwareError("The backup is the wrong size: %d of %d bytes." % (os.path.getsize(tmp), size))
    os.replace(tmp, path)
    return Backup(path, size, drive.esp_chip.text)


def erase_esptool(drive, on_stage=None, cancelled=None, tool=None):
    """Erase the whole flash. The chip stays in its bootloader: with no firmware left, it has nowhere else to go."""
    _run_esptool((tool or find_esptool()) + _rom_args(drive, "no-reset") + ["erase-flash"], None, on_stage, cancelled,
                 300.0)


def erase_uf2(image, drive, on_progress=None, cancelled=None, timeout=90.0, poll=0.5):
    """Run the erase tool from a UF2 drive. It erases, then restarts into the bootloader, so the drive comes back."""
    check_nuke_uf2(image)
    if read_drive(drive.path) != drive:
        raise FirmwareError("The board at %s has gone or changed. Scan again." % drive.path)
    before = {d.path for d in detect() if d.path != drive.path}
    _copy_to_drive(image, drive, on_progress, cancelled)
    if not wait_gone(drive):
        raise FirmwareError("The board did not start the erase. It is still in its bootloader.")
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        back = next((d for d in detect() if d.chip == drive.chip and d.path not in before), None)
        if back:
            return back
        time.sleep(poll)
    raise FirmwareError("The board did not come back after the erase. Unplug it and check it with BOOTSEL held.")


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
    _copy_to_drive(image, drive, on_progress, cancelled)
    if not wait_gone(drive, gone_timeout):
        raise FirmwareError("The board did not restart after the copy. It is still in its bootloader.")
    return info


def _copy_to_drive(image, drive, on_progress=None, cancelled=None):
    """The board may restart as the last data lands, so an error on the last chunk or after it is expected."""
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


def rp2_ports():
    """Serial ports that are MicroPython on an RP2 board."""
    return _ports_with(RP2_MICROPYTHON_USB)


def badge_ports(badge):
    return _ports_with(BADGES[badge].usb)


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


def identify(port, chip, timeout=10.0, poll=0.5, open_board=RawREPL.open_board, reset=True):
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
        repl.close(reset=reset)
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


def install_and_verify(image, drive, on_progress=None, on_stage=None, cancelled=None, expect_badge=None,
                       erase=False):
    """The whole install: write, wait for the restart, then talk to the new firmware. Returns an Installed, with no
    port if the board cannot be followed. expect_badge: None decides by the chip; "" when another board's firmware
    was chosen for a chip that reads like a badge's."""
    stage = on_stage or (lambda text: None)
    if drive.method == "esptool":
        stage("Checking the chip")
        if read_esp_chip(drive.path) != drive.esp_chip:     # another board was plugged in after this one was checked
            raise FirmwareError("The board on %s has changed. Nothing was written. Scan again." % drive.path)
        badge = drive.esp_chip.badge if expect_badge is None else expect_badge
        before = badge_ports(badge) if badge else set()
        flash_esptool(image, drive, on_progress, cancelled, erase=erase, on_stage=stage)
        if not badge:                                       # no way to tell what other boards appear as when they start
            return Installed("", "", "")
        stage("Waiting for the badge to start")
        try:
            port = wait_for_new_port(before, timeout=60.0, ports=lambda: badge_ports(badge))
        except FirmwareError:
            raise FirmwareError("The firmware was written and checked, but the badge did not come back. If this is "
                                "a badge, unplug it and plug it in again. If it is another board with the same chip, "
                                "it now has badge firmware: put it in its bootloader and install firmware for it.")
        stage("Checking the board on %s" % port)
        return identify(port, drive.chip, timeout=30.0, reset=BADGES[badge].soft_reset)
    before = rp2_ports()
    stage("Copying the firmware")
    install(image, drive, on_progress, cancelled)
    stage("Waiting for MicroPython to start")
    port = wait_for_new_port(before)
    stage("Checking the board on %s" % port)
    return identify(port, drive.chip)
