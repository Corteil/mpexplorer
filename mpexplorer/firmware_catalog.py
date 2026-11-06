"""What micropython.org offers for a chip: boards, builds and downloads, with a cache. Reads the site's HTML: no API."""
import html
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass

from . import paths
from .errors import Cancelled
from .firmware import MAX_IMAGE, FirmwareError

BASE = "https://micropython.org"
EMF_FLASHER = "https://emfcamp.github.io/badge-2024-software/merged-firmware.bin"
TIDAL_FLASHER = "https://emfcamp.github.io/TiDAL-Firmware/merged-firmware.bin"
NUKE_URL = "https://datasheets.raspberrypi.com/soft/flash_nuke.uf2"
NUKE_FILE = "flash_nuke.uf2"
# Raspberry Pi's address for the erase tool redirects to its GitHub release files.
ALLOWED = (BASE + "/", "https://emfcamp.github.io/", "https://datasheets.raspberrypi.com/",
           "https://github.com/raspberrypi/", "https://release-assets.githubusercontent.com/",
           "https://objects.githubusercontent.com/", "https://downloads.circuitpython.org/",
           "https://raw.githubusercontent.com/adafruit/", "https://api.github.com/repos/adafruit/")
FIRMWARE_PATH = "/resources/firmware/"
TIMEOUT = 20
VARIANT_NAMES = {"RISCV": "RISC-V"}
_CARD = re.compile(r'<a class="board-card" href="([A-Z0-9][A-Za-z0-9_\-]*)/?">(.*?)</a>', re.S)
_PRODUCT = re.compile(r'class="board-product">(.*?)</div>', re.S)
_VENDOR = re.compile(r'class="board-vendor">(.*?)</div>', re.S)
_LINK = re.compile(r'href="%s([^"/]+)"' % re.escape(FIRMWARE_PATH))
EXTENSIONS = {"esp32s3": ".bin"}        # the full image for esptool; default .uf2, for a UF2 drive
# On the site, but its page says to use dfu-util, and erasing it loses its DFU bootloader.
NOT_FOR_ESPTOOL = {"ARDUINO_NANO_ESP32"}


@dataclass
class Board:
    id: str
    product: str
    vendor: str

    @property
    def label(self):
        return "%s %s" % (self.vendor, self.product) if self.vendor else self.product


@dataclass
class Build:
    filename: str
    version: str
    date: str               # YYYY-MM-DD
    variant: str            # "" for the standard build
    preview: bool
    url: str = ""           # set for a file that changes under one name; otherwise it is on micropython.org

    @property
    def label(self):
        if not self.date:
            return self.version
        extras = [VARIANT_NAMES.get(self.variant, self.variant)] if self.variant else []
        return "%s (%s)%s" % (self.version, self.date, "  " + ", ".join(extras) if extras else "")


# Boards that micropython.org does not build for. The web flasher's "Latest" is the only full image they publish.
BADGE_BOARDS = {"esp32s3": [Board("TILDAGON", "Tildagon (2024, 2026)", "EMF Camp"),
                            Board("TIDAL", "TiDAL (2022)", "EMF Camp")]}
BADGE_BUILDS = {"TILDAGON": [Build("TILDAGON-merged-firmware.bin", "Latest from the EMF flasher", "", "", False,
                                   EMF_FLASHER)],
                "TIDAL": [Build("TIDAL-merged-firmware.bin", "Latest from the EMF flasher", "", "", False,
                                TIDAL_FLASHER)]}


def default_build(builds):
    """The newest release of the standard build, if there is one: previews and variants must be asked for."""
    plain = [b for b in builds if not b.preview and not b.variant]
    return plain[0] if plain else None


def parse_boards(text):
    boards = []
    for board_id, body in _CARD.findall(text):
        product, vendor = _PRODUCT.search(body), _VENDOR.search(body)
        boards.append(Board(board_id, html.unescape(product[1]).strip() if product else board_id,
                            html.unescape(vendor[1]).strip() if vendor else ""))
    return sorted(boards, key=lambda b: b.label.lower())


def parse_builds(text, board_id, ext=".uf2"):
    """A board's builds of one file type, newest first. Names: BOARD[-VARIANT]-YYYYMMDD-vX.Y.Z[-preview...].ext"""
    name = re.compile(r"%s(?:-([A-Z0-9_]+))?-(\d{4})(\d\d)(\d\d)-(v[0-9A-Za-z.\-]+)%s"
                      % (re.escape(board_id), re.escape(ext)))
    builds = {}
    for filename in _LINK.findall(text):
        m = name.fullmatch(filename)
        if m:
            builds[filename] = Build(filename, m[5], "%s-%s-%s" % (m[2], m[3], m[4]), m[1] or "", "preview" in m[5])
    return sorted(builds.values(), key=lambda b: (b.date, b.version, not b.variant), reverse=True)


def default_cache_dir():
    return os.path.join(paths.cache_dir(), "firmware")


def _open_url(url):
    request = urllib.request.Request(url, headers={"User-Agent": "mpexplorer"})
    return urllib.request.urlopen(request, timeout=TIMEOUT)


class Catalog:
    """Each list is saved when it is read, so a board can still be installed with no network (`saved` says when)."""

    def __init__(self, cache_dir=None, opener=_open_url, allowed=None):
        self.dir, self.opener = cache_dir or default_cache_dir(), opener
        self.allowed = tuple(allowed) if allowed else ALLOWED       # URL prefixes it may read

    def _read(self, url, limit, on_progress=None, cancelled=None):
        if not url.startswith(self.allowed):
            raise FirmwareError("Refusing to download from %s" % url)
        try:
            with self.opener(url) as reply:
                if not reply.geturl().startswith(self.allowed):
                    raise FirmwareError("The download was redirected to %s" % reply.geturl())
                length = reply.headers.get("Content-Length")
                total, data = int(length) if length and length.isdigit() else 0, bytearray()
                while True:
                    if cancelled and cancelled():
                        raise Cancelled()
                    chunk = reply.read(1 << 16)
                    if not chunk:
                        break
                    data += chunk
                    if len(data) > limit:
                        raise FirmwareError("The download from %s is too large." % url)
                    if on_progress and total:
                        on_progress(len(data), total)
        except (OSError, urllib.error.HTTPError, ValueError) as e:      # URLError and timeouts are OSError
            raise FirmwareError("Cannot read %s: %s" % (url, e))
        if total and len(data) != total:
            raise FirmwareError("The download from %s stopped early (%d of %d bytes)." % (url, len(data), total))
        return bytes(data)

    def _list(self, name, url, parse, make):
        """Returns (items, saved): saved is None for a fresh list, else the time of the saved copy used instead."""
        path = os.path.join(self.dir, name)
        try:
            items = parse(self._read(url, MAX_IMAGE).decode("utf-8", "replace"))
            if not items:
                raise FirmwareError("Found nothing on %s: the page may have changed." % url)
        except FirmwareError as e:
            try:
                with open(path, encoding="utf-8") as f:
                    return [make(**row) for row in json.load(f)], os.path.getmtime(path)
            except (OSError, ValueError, TypeError):
                raise e
        os.makedirs(self.dir, exist_ok=True)
        tmp = path + ".part"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump([asdict(i) for i in items], f)
        os.replace(tmp, path)
        return items, None

    def boards(self, chip, badges=()):
        """(boards, saved). The badges named come first, and survive the site being out of reach."""
        first = [b for b in BADGE_BOARDS.get(chip, []) if b.id in badges]
        try:
            site, saved = self._list("boards-%s.json" % chip, "%s/download/?mcu=%s" % (BASE, chip), parse_boards, Board)
        except FirmwareError:
            if first:
                return first, None
            raise
        return first + [b for b in site if b.id not in NOT_FOR_ESPTOOL], saved

    def builds(self, board_id, chip=None):
        if board_id in BADGE_BUILDS:
            return list(BADGE_BUILDS[board_id]), None
        ext = EXTENSIONS.get(chip, ".uf2")
        return self._list("builds-%s.json" % board_id, "%s/download/%s/" % (BASE, board_id),
                          lambda text: parse_builds(text, board_id, ext), Build)

    def path_of(self, build):
        if os.path.basename(build.filename) != build.filename or not build.filename.endswith((".uf2", ".bin")):
            raise FirmwareError("Bad firmware name: %s" % build.filename)
        return os.path.join(self.dir, build.filename)

    def download(self, build, on_progress=None, cancelled=None):
        """The build's file. A micropython.org name is never reused, so a saved copy stays good; a file that changes
        under one name is read again, and the saved copy is used only if that fails."""
        path = self.path_of(build)
        if os.path.exists(path) and not build.url:
            return path
        try:
            data = self._read(build.url or BASE + FIRMWARE_PATH + build.filename, MAX_IMAGE, on_progress, cancelled)
        except FirmwareError:
            if os.path.exists(path):
                return path
            raise
        os.makedirs(self.dir, exist_ok=True)
        tmp = path + ".part"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        return path

    def erase_tool(self, on_progress=None, cancelled=None):
        """Raspberry Pi's erase program for a UF2 drive; a saved copy is used if it cannot be read again."""
        return self.download(Build(NUKE_FILE, "", "", "", False, NUKE_URL), on_progress, cancelled)

    def forget(self, build):
        """Drop a copy that failed its check, so the next try downloads it again."""
        try:
            os.unlink(self.path_of(build))
        except OSError:
            pass


def saved_text(saved):
    return time.strftime("%Y-%m-%d", time.localtime(saved))
