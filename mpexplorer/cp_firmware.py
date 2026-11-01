"""CircuitPython builds for RP2040 and RP2350 boards (UF2), for Tools > Install firmware.

Checked 2026-11-01: `circuitpython-org/_data/files.json` lists every board with its versions and file types; a
board's page (`_board/<id>.md`) has `family: rp2040|rp2350`; the Raspberry Pi boards are the folders of
`ports/raspberrypi/boards` in adafruit/circuitpython; files come from downloads.circuitpython.org. Same shape as
`firmware_catalog.Catalog` (boards / builds / download), so the dialog treats the two alike. The UF2 family check in
`firmware.check_image` is what stops a wrong-chip write.
"""
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor

from .firmware import FirmwareError
from .firmware_catalog import Board, Build, Catalog, default_cache_dir

FILES_JSON = "https://raw.githubusercontent.com/adafruit/circuitpython-org/main/_data/files.json"
BOARD_PAGE = "https://raw.githubusercontent.com/adafruit/circuitpython-org/main/_board/%s.md"
RP_BOARDS = "https://api.github.com/repos/adafruit/circuitpython/contents/ports/raspberrypi/boards"
DOWNLOAD = "https://downloads.circuitpython.org/bin/%s/en_US/adafruit-circuitpython-%s-en_US-%s.uf2"
MAX_LIST = 16 << 20
_FIELD = re.compile(r'^(family|name|manufacturer):\s*"?([^"\n]*?)"?\s*$', re.M)


def parse_board_page(text):
    """{'family', 'name', 'manufacturer'} from a board page's front matter ({} if there is none)."""
    head = text.split("---")[1] if text.startswith("---") and text.count("---") >= 2 else ""
    return {k: v.strip() for k, v in _FIELD.findall(head)}


def version_key(version):
    nums = re.match(r"(\d+)\.(\d+)\.(\d+)", version)
    return tuple(int(n) for n in nums.groups()) if nums else (0, 0, 0)


def uf2_builds(entry):
    """Builds of one board from its files.json entry: stable releases first (newest first), then previews."""
    board = entry["id"]
    found = [Build("adafruit-circuitpython-%s-en_US-%s.uf2" % (board, v["version"]), v["version"], "", "",
                   not v.get("stable"), DOWNLOAD % (board, board, v["version"]))
             for v in entry.get("versions", []) if "uf2" in v.get("extensions", []) and
             "en_US" in v.get("languages", ["en_US"])]
    return sorted(found, key=lambda b: (not b.preview, version_key(b.version)), reverse=True)


class CircuitPythonCatalog(Catalog):
    def __init__(self, cache_dir=None, opener=None):
        kw = {"opener": opener} if opener else {}
        super().__init__((cache_dir or default_cache_dir()) + "-circuitpython", **kw)
        self.files = None

    def _json(self, name, url, limit=MAX_LIST):
        """(parsed JSON, saved): saved is None when fresh, else the time of the saved copy used instead."""
        path = os.path.join(self.dir, name)
        try:
            data = self._read(url, limit)
            value = json.loads(data)
        except (FirmwareError, ValueError) as e:
            try:
                with open(path, encoding="utf-8") as f:
                    return json.load(f), os.path.getmtime(path)
            except (OSError, ValueError):
                raise FirmwareError(str(e))
        os.makedirs(self.dir, exist_ok=True)
        with open(path + ".part", "wb") as f:
            f.write(data)
        os.replace(path + ".part", path)
        return value, None

    def _page(self, board_id):
        try:
            return board_id, parse_board_page(self._read(BOARD_PAGE % board_id, 1 << 20).decode("utf-8", "replace"))
        except FirmwareError:
            return board_id, {}

    def boards(self, chip, badges=()):
        """(boards, saved) of Raspberry Pi RP boards with a CircuitPython UF2 for `chip` ('rp2040' or 'rp2350')."""
        files, saved = self._json("files.json", FILES_JSON)
        self.files = {e["id"]: e for e in files}
        listing, saved2 = self._json("rp-boards.json", RP_BOARDS)
        ids = [e["name"] for e in listing if e.get("type") == "dir" and e["name"] in self.files
               and uf2_builds(self.files[e["name"]])]
        path = os.path.join(self.dir, "rp-chips.json")
        try:
            with open(path, encoding="utf-8") as f:
                known = json.load(f)
        except (OSError, ValueError):
            known = {}
        missing = [i for i in ids if i not in known]
        if missing:
            with ThreadPoolExecutor(8) as pool:
                for board_id, page in pool.map(self._page, missing):
                    if page.get("family"):
                        known[board_id] = page
            os.makedirs(self.dir, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(known, f)
        boards = [Board(i, known[i].get("name", i), known[i].get("manufacturer", ""))
                  for i in ids if i in known and known[i]["family"].lower() == chip]
        if not boards:
            raise FirmwareError("Found no CircuitPython boards for %s: the site may have changed." % chip)
        return sorted(boards, key=lambda b: b.label.lower()), saved or saved2

    def builds(self, board_id, chip=None):
        if self.files is None:
            files, _ = self._json("files.json", FILES_JSON)
            self.files = {e["id"]: e for e in files}
        if board_id not in self.files:
            raise FirmwareError("%s is not on the CircuitPython site." % board_id)
        return uf2_builds(self.files[board_id]), None

    def download(self, build, on_progress=None, cancelled=None):
        """CircuitPython names are never reused, so a saved copy is good."""
        path = self.path_of(build)
        if os.path.exists(path):
            return path
        return super().download(build, on_progress, cancelled)
