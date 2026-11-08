"""Libraries from a folder or a zip on this computer, and the exact revision read from os.uname().version."""
import zipfile

import pytest

from mpexplorer import custom_libraries as CL
from mpexplorer import firmware as fw
from mpexplorer import libraries
from mpexplorer import sources as S
from mpexplorer.backends import LocalBackend
from mpexplorer.libraries import LibraryError
from mpexplorer.local_source import LocalFiles, LocalFolder, from_chosen
from mpexplorer.settings import Settings


def make_tree(root):
    (root / "blink.py").write_text("print('blink')\n", newline="\n")
    (root / "tone.mpy").write_bytes(b"M\x06\x00\x1f")
    pkg = root / "mything"
    (pkg / "sub").mkdir(parents=True)
    (pkg / "__init__.py").write_text("x = 1\n")
    (pkg / "sub" / "deep.py").write_text("y = 2\n")
    (pkg / "__pycache__").mkdir()
    (pkg / "__pycache__" / "junk.py").write_text("no")
    (pkg / ".hidden.py").write_text("no")
    (root / "notes.txt").write_text("not a library")


def test_a_folder_lists_files_and_packages_and_skips_the_rest(tmp_path):
    make_tree(tmp_path)
    cat = LocalFolder({"name": "Mine", "path": str(tmp_path)})
    libs, saved = cat.libraries()
    assert saved is None and [lib.name for lib in libs] == ["blink", "mything", "tone"]
    plan = cat.resolve(["mything"])
    assert plan == [("mything", "", "py", [("mything/__init__.py", "mything/__init__.py"),
                                           ("mything/sub/deep.py", "mything/sub/deep.py")])]
    assert cat.fetch("blink.py") == b"print('blink')\n"
    with pytest.raises(LibraryError, match="is not in"):
        cat.resolve(["nothing"])


def test_install_compare_and_remove_work_like_any_other_source(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    make_tree(src)
    cat = LocalFolder({"name": "Mine", "path": str(src)})
    dest = str(tmp_path / "lib")
    plan = cat.resolve(["mything", "blink"])
    assert libraries.install(cat, LocalBackend(), plan, dest) == (3, 0)
    assert libraries.install(cat, LocalBackend(), plan, dest) == (0, 3)                  # nothing changed: all skipped
    assert libraries.compare(cat, LocalBackend(), ["mything"], None, dest) == {"mything": "current"}
    (src / "mything" / "__init__.py").write_text("x = 2\n")                              # the owner edited their copy
    assert libraries.compare(cat, LocalBackend(), ["mything"], None, dest) == {"mything": "differs"}
    assert libraries.remove(cat, LocalBackend(), ["blink"], None, dest) == 1


def test_chosen_files_are_one_library_each_and_install_like_a_folder(tmp_path):
    # Tools > Install libraries > From this computer > Files: loose files, no source saved (docs/FILE_CHOOSER_PLAN.md)
    src = tmp_path / "src"
    src.mkdir()
    make_tree(src)
    (src / "blink.mpy").write_bytes(b"M\x06")
    chosen = [str(src / "blink.py"), str(src / "blink.mpy"), str(src / "tone.mpy"), str(src / "notes.txt")]
    cat = from_chosen(chosen)
    assert isinstance(cat, LocalFiles) and cat.title == "3 files from src"           # notes.txt is not a library
    libs, saved = cat.libraries()
    assert saved is None and [(lib.name, lib.description) for lib in libs] == [("blink", "blink.mpy, blink.py"),
                                                                               ("tone", "tone.mpy")]
    plan = cat.resolve(["blink"])
    assert plan == [("blink", "", "py", [("blink.mpy", "blink.mpy"), ("blink.py", "blink.py")])]
    dest = str(tmp_path / "lib")
    assert libraries.install(cat, LocalBackend(), plan, dest) == (2, 0)
    assert (tmp_path / "lib" / "blink.py").read_bytes() == (src / "blink.py").read_bytes()
    assert libraries.compare(cat, LocalBackend(), ["blink"], None, dest) == {"blink": "current"}
    with pytest.raises(LibraryError, match="not among"):
        cat.resolve(["mything"])
    with pytest.raises(LibraryError, match="was not chosen"):
        cat.fetch("../secret.py")
    with pytest.raises(LibraryError, match="none of the chosen files"):
        from_chosen([str(src / "notes.txt")])
    with pytest.raises(LibraryError, match="Nothing"):
        from_chosen([])


def test_one_chosen_folder_or_zip_is_read_as_a_folder(tmp_path):
    make_tree(tmp_path)
    folder = from_chosen(str(tmp_path))
    assert isinstance(folder, LocalFolder) and folder.title == tmp_path.name
    assert [lib.name for lib in folder.libraries()[0]] == ["blink", "mything", "tone"]
    z = tmp_path / "mine.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("hello.py", "print(1)\n")
    assert isinstance(from_chosen([str(z)]), LocalFolder) and from_chosen([str(z)]).title == "mine.zip"


def test_a_zip_is_read_the_same_and_a_single_wrapper_folder_is_looked_through(tmp_path):
    z = tmp_path / "libs.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("mylibs/blink.py", "a")
        f.writestr("mylibs/pkg/__init__.py", "b")
        f.writestr("mylibs/pkg/__pycache__/x.py", "no")
        f.writestr("mylibs/readme.md", "no")
    cat = LocalFolder({"name": "Z", "path": str(z)})
    assert [lib.name for lib in cat.libraries()[0]] == ["blink", "pkg"]
    assert cat.fetch("pkg/__init__.py") == b"b" and cat.fetch("blink.py") == b"a"


def test_bad_keys_and_unreadable_sources_are_refused(tmp_path):
    make_tree(tmp_path)
    cat = LocalFolder({"name": "Mine", "path": str(tmp_path)})
    for key in ("../x.py", "/etc/passwd", "a/../b.py", "nothere.py"):
        with pytest.raises(LibraryError):
            cat.fetch(key)
    (tmp_path / "big.py").write_bytes(b"x" * ((2 << 20) + 1))
    with pytest.raises(LibraryError, match="larger than"):
        cat.fetch("big.py")
    bad = tmp_path / "bad.zip"
    bad.write_text("not a zip")
    with pytest.raises(LibraryError, match="Cannot read"):
        LocalFolder({"name": "B", "path": str(bad)}).libraries()


def test_validate_and_check_for_a_folder_source(tmp_path):
    make_tree(tmp_path)
    src = {"name": "Mine", "kind": "folder", "path": str(tmp_path)}
    assert CL.validate_library_source(src) == {"name": "Mine", "kind": "folder", "for": "micropython",
                                               "path": str(tmp_path)}
    got = CL.check_source(src, Settings())
    assert got.ok and "3 libraries" in got.message and got.trust_host is None          # nothing to trust: it is local
    for path, why in (("relative/dir", "full path"), (str(tmp_path / "nope"), "not a folder")):
        with pytest.raises(S.SourceError, match=why):
            CL.validate_library_source(dict(src, path=path))
    empty = tmp_path / "empty"
    empty.mkdir()
    assert "Found no libraries" in CL.check_source(dict(src, path=str(empty)), Settings()).message


# -- the exact revision of a running Pimoroni build -----------------------------------------------------------
def test_exact_release_reads_a_revision_only_when_there_is_one():
    assert S.exact_release("picow v1.29.0-2 on 2026-08-31") == "1.29.0-2"
    assert S.exact_release("v1.29.0 on 2026-08-01") == ""                            # stock: no revision
    assert S.exact_release("v1.27.0-72-g1234abc on 2025-01-01") == ""     # commits since the tag
    assert S.exact_release("") == ""


def test_update_marks_use_the_revision_when_the_board_reports_one():
    assert S.newer_than("v1.29.0-3", "1.29.0-2") and not S.newer_than("v1.29.0-2", "1.29.0-2")
    assert not S.newer_than("v1.29.0", "1.29.0-2") and S.newer_than("v1.30.0", "1.29.0-2")
    assert not S.newer_than("v1.29.0-2", "1.29.0")                                   # only a release known: not "newer"
    assert S.newer_than("v1.30.0", "1.29.0")


def test_read_running_returns_the_exact_revision_from_the_fourth_line():
    class Repl:
        closed = False

        def exec(self, code, timeout=10):
            return b"2\r\nRaspberry Pi Pico W with RP2040\r\n1.29.0\r\npicow v1.29.0-2 on 2026-08-31\r\n"

        def close(self, reset=False):
            self.closed = True
    got = fw.read_running("/dev/x", open_board=lambda port: (Repl(), None))
    assert got == fw.RunningInfo("Raspberry Pi Pico W with RP2040", "1.29.0", 2, "1.29.0-2")
