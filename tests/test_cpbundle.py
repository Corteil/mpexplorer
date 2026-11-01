import io
import json
import zipfile

import pytest

from mpexplorer import cpbundle, libraries
from mpexplorer.backends import LocalBackend

TAG = "20261031"
PREFIX = "adafruit-circuitpython-bundle"
META = {
    "adafruit_bus_device": {"dependencies": [], "package": True, "path": "lib/adafruit_bus_device", "version": "5.0"},
    "adafruit_thing": {"dependencies": ["adafruit_bus_device"], "package": False, "path": "lib/adafruit_thing",
                       "version": "1.2", "pypi_description": "A thing"},
    "Zeta": {"dependencies": [], "package": False, "path": "lib/Zeta", "version": "0.1"},
}


def make_zip(kind):
    root = "%s-%s-%s" % (PREFIX, kind, TAG)
    ext = ".py" if kind == "py" else ".mpy"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(root + "/lib/adafruit_bus_device/__init__" + ext, b"init " + kind.encode())
        z.writestr(root + "/lib/adafruit_bus_device/i2c_device" + ext, b"i2c " + kind.encode())
        z.writestr(root + "/lib/adafruit_thing" + ext, b"thing " + kind.encode())
        z.writestr(root + "/lib/Zeta" + ext, b"zeta")
        z.writestr(root + "/examples/x.py", b"not a library")
    return buf.getvalue()


class Reply(io.BytesIO):
    def __init__(self, data, url):
        super().__init__(data)
        self.url, self.headers = url, {"Content-Length": str(len(data))}

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass


def site(tag=TAG, missing=()):
    base = "https://github.com/adafruit/Adafruit_CircuitPython_Bundle/releases"
    pages = {base + "/latest": b"", base + "/download/%s/%s-%s.json" % (tag, PREFIX, tag): json.dumps(META).encode(),
             base + "/download/%s/%s-10.x-mpy-%s.zip" % (tag, PREFIX, tag): make_zip("10.x-mpy"),
             base + "/download/%s/%s-py-%s.zip" % (tag, PREFIX, tag): make_zip("py")}
    seen = []

    def opener(url):
        seen.append(url)
        if url in missing or url not in pages:
            raise OSError("404 " + url)
        return Reply(pages[url], base + "/tag/" + tag if url.endswith("/latest") else url)
    return opener, seen


def test_list_is_sorted_and_carries_versions(tmp_path):
    opener, _ = site()
    libs, saved = cpbundle.Catalog("adafruit", str(tmp_path), opener).libraries()
    assert saved is None and [lib.name for lib in libs] == ["adafruit_bus_device", "adafruit_thing", "Zeta"]
    assert libs[1].description == "A thing" and libs[1].version == "1.2"


def test_resolve_uses_the_mpy_zip_and_puts_dependencies_first(tmp_path):
    opener, _ = site()
    cat = cpbundle.Catalog("adafruit", str(tmp_path), opener)
    plan = cat.resolve(["adafruit_thing"], 10)
    assert [p[0] for p in plan] == ["adafruit_bus_device", "adafruit_thing"] and {p[2] for p in plan} == {"10"}
    assert [f[0] for f in plan[0][3]] == ["adafruit_bus_device/__init__.mpy", "adafruit_bus_device/i2c_device.mpy"]
    assert plan[1][3][0][0] == "adafruit_thing.mpy"


def test_a_missing_mpy_zip_falls_back_to_source(tmp_path):
    opener, _ = site()
    cat = cpbundle.Catalog("adafruit", str(tmp_path), opener)
    plan = cat.resolve(["Zeta"], 12)                 # there is no 12.x zip
    assert plan[0][2] == "py" and plan[0][3][0][0] == "Zeta.py"
    assert cpbundle.Catalog("adafruit", str(tmp_path / "b"), opener).resolve(["Zeta"], None)[0][2] == "py"


def test_install_and_remove_through_the_common_code(tmp_path):
    opener, _ = site()
    cat = cpbundle.Catalog("adafruit", str(tmp_path / "c"), opener)
    dest = str(tmp_path / "lib")
    assert libraries.install(cat, LocalBackend(), cat.resolve(["adafruit_thing"], 10), dest) == (3, 0)
    assert (tmp_path / "lib" / "adafruit_bus_device" / "i2c_device.mpy").read_bytes() == b"i2c 10.x-mpy"
    assert libraries.install(cat, LocalBackend(), cat.resolve(["adafruit_thing"], 10), dest) == (0, 3)
    assert libraries.remove(cat, LocalBackend(), ["adafruit_thing"], 10, dest) == 1          # not its dependency
    assert (tmp_path / "lib" / "adafruit_bus_device").exists()
    assert libraries.remove(cat, LocalBackend(), ["adafruit_bus_device"], 10, dest) == 2
    assert not (tmp_path / "lib" / "adafruit_bus_device").exists()


def test_unknown_library_hosts_and_keys_are_refused(tmp_path):
    opener, _ = site()
    cat = cpbundle.Catalog("adafruit", str(tmp_path), opener)
    cat.libraries()
    with pytest.raises(libraries.LibraryError, match="not in"):
        cat.resolve(["nope"], 10)
    with pytest.raises(libraries.LibraryError):
        cat._read("https://example.com/x", 10)
    with pytest.raises(libraries.LibraryError, match="Bad file"):
        cat.fetch("../evil.zip|x")


def test_offline_uses_the_saved_tag_and_list(tmp_path):
    opener, _ = site()
    cpbundle.Catalog("adafruit", str(tmp_path), opener).libraries()

    def down(url):
        raise OSError("down")
    libs, saved = cpbundle.Catalog("adafruit", str(tmp_path), down).libraries()
    assert saved and len(libs) == 3
    with pytest.raises(libraries.LibraryError):
        cpbundle.Catalog("adafruit", str(tmp_path / "empty"), down).libraries()
