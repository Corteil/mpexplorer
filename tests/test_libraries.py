import hashlib
import io
import json

import pytest

from mpexplorer import libraries
from mpexplorer.backends import LocalBackend

FILES = {"a.py": b"print('a')\n", "pkg/__init__.py": b"x = 1\n", "pkg/mod.py": b"y = 2\n"}


def short(data):
    return hashlib.sha256(data).hexdigest()[:8]


class Reply(io.BytesIO):
    def __init__(self, data, url):
        super().__init__(data)
        self.url = url

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass


def make_site(bad_hash=False):
    pages = {
        libraries.INDEX + "/index.json": json.dumps({"v": 2, "packages": [
            {"name": "zed", "version": "1.0", "versions": {"6": ["1.0"], "py": ["1.0"]}},
            {"name": "alpha", "version": "2.0", "description": "first", "versions": {"py": ["2.0"]}}]}).encode(),
        libraries.INDEX + "/package/py/zed/latest.json": json.dumps(
            {"version": "1.0", "deps": [["alpha", "latest"]], "hashes": [["a.py", short(FILES["a.py"])]]}).encode(),
        libraries.INDEX + "/package/py/alpha/latest.json": json.dumps(
            {"version": "2.0", "hashes": [["pkg/__init__.py", short(FILES["pkg/__init__.py"])],
                                          ["pkg/mod.py", short(FILES["pkg/mod.py"])]]}).encode(),
    }
    for data in FILES.values():
        h = short(data)
        pages["%s/file/%s/%s" % (libraries.INDEX, h[:2], h)] = b"tampered" if bad_hash else data
    seen = []

    def opener(url):
        seen.append(url)
        if url not in pages:
            raise OSError("404 " + url)
        return Reply(pages[url], url)

    return opener, seen


def test_index_is_sorted_and_reads_mpy_versions(tmp_path):
    opener, _ = make_site()
    libs, saved = libraries.Catalog(str(tmp_path), opener).libraries()
    assert saved is None and [lib.name for lib in libs] == ["alpha", "zed"]
    assert libs[1].mpy_versions == ("6",)


def test_saved_index_is_used_offline(tmp_path):
    opener, _ = make_site()
    libraries.Catalog(str(tmp_path), opener).libraries()
    libs, saved = libraries.Catalog(str(tmp_path), lambda url: (_ for _ in ()).throw(OSError("down"))).libraries()
    assert saved and len(libs) == 2


def test_no_index_and_no_saved_copy_is_an_error(tmp_path):
    with pytest.raises(libraries.LibraryError):
        libraries.Catalog(str(tmp_path), lambda url: (_ for _ in ()).throw(OSError("down"))).libraries()


def test_resolve_puts_dependencies_first_and_falls_back_to_source(tmp_path):
    opener, seen = make_site()
    plan = libraries.Catalog(str(tmp_path), opener).resolve(["zed"], mpy=6)
    assert [p[0] for p in plan] == ["alpha", "zed"] and {p[2] for p in plan} == {"py"}
    assert any("/package/6/zed/" in u for u in seen)      # the .mpy form was tried first


def test_install_writes_skips_and_creates_folders(tmp_path):
    opener, _ = make_site()
    cat = libraries.Catalog(str(tmp_path / "cache"), opener)
    dest = str(tmp_path / "board" / "lib")
    plan = cat.resolve(["zed"])
    assert libraries.install(cat, LocalBackend(), plan, dest) == (3, 0)
    assert (tmp_path / "board" / "lib" / "pkg" / "mod.py").read_bytes() == FILES["pkg/mod.py"]
    assert libraries.install(cat, LocalBackend(), plan, dest) == (0, 3)
    (tmp_path / "board" / "lib" / "a.py").write_bytes(b"old")
    assert libraries.install(cat, LocalBackend(), plan, dest) == (1, 2)
    assert libraries.installed(LocalBackend(), dest) == ["a", "pkg"]


def test_a_file_that_fails_its_hash_is_refused_and_not_cached(tmp_path):
    opener, _ = make_site(bad_hash=True)
    cat = libraries.Catalog(str(tmp_path / "cache"), opener)
    with pytest.raises(libraries.LibraryError, match="hash"):
        libraries.install(cat, LocalBackend(), cat.resolve(["zed"]), str(tmp_path / "lib"))
    assert not (tmp_path / "cache" / "files").exists()


def test_unsafe_paths_and_hosts_are_refused(tmp_path):
    cat = libraries.Catalog(str(tmp_path), lambda url: Reply(b"", url))
    with pytest.raises(libraries.LibraryError):
        cat._read("https://example.com/x", 10)
    with pytest.raises(libraries.LibraryError, match="Unsafe"):
        libraries.install(cat, LocalBackend(), [("p", "1", "py", [("../evil.py", "abcdef01")])], str(tmp_path / "lib"))


def test_mpy_version_is_the_low_byte():
    assert libraries.mpy_version(0x0A06) == 6 and libraries.mpy_version(None) is None


def test_remove_deletes_only_that_library_and_empty_folders(tmp_path):
    opener, _ = make_site()
    cat = libraries.Catalog(str(tmp_path / "cache"), opener)
    dest = str(tmp_path / "lib")
    libraries.install(cat, LocalBackend(), cat.resolve(["zed"]), dest)
    (tmp_path / "lib" / "mine.py").write_text("keep")
    assert libraries.remove(cat, LocalBackend(), ["alpha"], dest=dest) == 2          # pkg/ files; zed's a.py stays
    assert sorted(p.name for p in (tmp_path / "lib").iterdir()) == ["a.py", "mine.py"]       # empty pkg/ is gone
    assert libraries.remove(cat, LocalBackend(), ["alpha"], dest=dest) == 0           # nothing left: no error


class Fw:
    def __init__(self, out):
        self.out = out

    def _run(self, code):
        if isinstance(self.out, Exception):
            raise self.out
        return self.out


def test_board_firmware_names_the_implementation():
    assert libraries.board_firmware(Fw(b"circuitpython\n")) == "circuitpython"
    assert libraries.board_firmware(Fw(b"micropython\n")) == "micropython"
    assert libraries.board_firmware(Fw(OSError("gone"))) is None
