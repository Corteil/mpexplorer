"""Config/cache folders, portable mode, Windows names and drive roots (docs/WINDOWS_PLAN.md). Run on any platform."""
import ntpath
import os
import posixpath

import pytest

from mpexplorer import paths, winnames
from mpexplorer.backends import DRIVES, LocalBackend
from mpexplorer.errors import BackendError


def test_linux_defaults_and_xdg():
    assert paths.config_dir("linux", {}, "/home/pi", None) == os.path.join("/home/pi", ".config", "mpexplorer")
    assert paths.cache_dir("linux", {}, "/home/pi", None) == os.path.join("/home/pi", ".cache", "mpexplorer")
    env = {"XDG_CONFIG_HOME": "/x/c", "XDG_CACHE_HOME": "/x/k"}
    assert paths.config_dir("linux", env, "/home/pi", None) == os.path.join("/x/c", "mpexplorer")
    assert paths.cache_dir("linux", env, "/home/pi", None) == os.path.join("/x/k", "mpexplorer")


def test_windows_uses_appdata_and_localappdata():
    env = {"APPDATA": "C:\\Users\\b\\AppData\\Roaming", "LOCALAPPDATA": "C:\\Users\\b\\AppData\\Local"}
    assert paths.config_dir("win32", env, "C:\\Users\\b", None).startswith("C:\\Users\\b\\AppData\\Roaming")
    assert paths.cache_dir("win32", env, "C:\\Users\\b", None).startswith("C:\\Users\\b\\AppData\\Local")
    # without the variables it falls back to the standard places under the home folder
    assert "Roaming" in paths.config_dir("win32", {}, "C:\\Users\\b", None)
    assert "Local" in paths.cache_dir("win32", {}, "C:\\Users\\b", None)


def test_settings_file_explicit_wins_in_every_mode(tmp_path):
    (tmp_path / paths.PORTABLE_MARKER).write_text("")
    assert paths.settings_file("win32", {"MPEXPLORER_CONFIG": "X.json"}, "h", str(tmp_path)) == "X.json"
    assert paths.settings_file("linux", {"MPEXPLORER_CONFIG": "Y.json"}, "h", None) == "Y.json"


def test_no_marker_means_not_portable(tmp_path):
    assert paths.portable_dir(str(tmp_path)) is None
    assert paths.config_dir("win32", {"APPDATA": "A"}, "h", str(tmp_path)) == os.path.join("A", "mpexplorer")


def test_marker_present_uses_data_folder(tmp_path):
    (tmp_path / paths.PORTABLE_MARKER).write_text("")
    data = os.path.join(str(tmp_path), "data")
    assert paths.portable_dir(str(tmp_path)) == data
    assert paths.config_dir("win32", {"APPDATA": "A"}, "h", str(tmp_path)) == data
    assert paths.cache_dir("win32", {"LOCALAPPDATA": "L"}, "h", str(tmp_path)) == os.path.join(data, "cache")
    assert paths.settings_file("win32", {}, "h", str(tmp_path)) == os.path.join(data, "settings.json")
    assert paths.portable_problem() == ""
    assert not os.path.exists(os.path.join(data, ".write-test"))


def test_marker_but_unwritable_falls_back_and_says_so(tmp_path):
    (tmp_path / paths.PORTABLE_MARKER).write_text("")
    (tmp_path / "data").write_text("a file where the folder should be")      # makedirs fails
    assert paths.portable_dir(str(tmp_path)) is None
    assert "cannot be written" in paths.portable_problem()
    assert paths.config_dir("win32", {"APPDATA": "A"}, "h", str(tmp_path)) == os.path.join("A", "mpexplorer")


def test_source_runs_are_never_portable():
    assert paths.exe_dir() is None          # not frozen under pytest
    assert paths.portable_dir(None) is None


@pytest.mark.parametrize("name,bad", [
    ("notes.txt", False), ("a b.py", False), ("é.txt", False),
    ("a:b.txt", True), ("what?.py", True), ("x*y", True), ("tab\tname", True), ('q"q', True),
    ("name.", True), ("name ", True), ("CON", True), ("nul.txt", True), ("com1.log", True), ("LPT9", True),
    ("console.py", False), ("communication", False), (".hidden", False),
])
def test_name_problem(name, bad):
    assert bool(winnames.name_problem(name)) is bad


def test_name_problem_names_the_character():
    assert "':'" in winnames.name_problem("a:b")
    assert "reserved" in winnames.name_problem("NUL")


def test_path_problem_checks_each_part_but_not_the_drive():
    assert winnames.path_problem("C:\\Users\\b\\ok.txt") is None
    assert "'bad:name'" in winnames.path_problem("C:\\dir\\bad:name\\f.txt")
    assert winnames.path_problem("/lib/aux/x.py")        # a board folder called aux cannot exist on Windows


def test_case_clashes():
    assert winnames.case_clashes(["main.py", "Main.py", "boot.py"]) == [["main.py", "Main.py"]]
    assert winnames.case_clashes(["a", "b"]) == []


def test_long_path():
    short = "C:\\a\\b.txt"
    assert winnames.long_path(short) == short
    long = "C:\\" + "\\".join(["d" * 30] * 10) + "\\f.txt"
    assert winnames.long_path(long) == "\\\\?\\" + long
    assert winnames.long_path("\\\\?\\" + long) == "\\\\?\\" + long
    tail = "\\".join(["d" * 30] * 10)
    assert winnames.long_path("\\\\server\\share\\" + tail) == "\\\\?\\UNC\\server\\share\\" + tail


@pytest.mark.parametrize("path,expected", [
    ("C:\\", "C:\\"), ("C:\\Users", "C:\\"), ("C:\\Users\\b\\", "C:\\Users"), ("C:\\a\\b", "C:\\a"),
])
def test_parent_path_windows(path, expected):
    assert winnames.parent_path(path, ntpath) == expected


@pytest.mark.parametrize("path,expected", [("/", "/"), ("/home", "/"), ("/home/pi/", "/home"), ("/a/b/c", "/a/b")])
def test_parent_path_posix(path, expected):
    assert winnames.parent_path(path, posixpath) == expected


def test_list_drives_from_a_bitmask():
    labels = {"C:\\": "Windows", "E:\\": "CIRCUITPY"}
    got = winnames.list_drives((1 << 2) | (1 << 4), labels.get)
    assert got == [("C:\\", "Windows"), ("E:\\", "CIRCUITPY")]


def test_local_backend_free_space_and_parent(tmp_path):
    be = LocalBackend()
    assert be.free_space(str(tmp_path)) > 0
    assert be.parent(str(tmp_path / "x")) == str(tmp_path)
    root = os.path.abspath(os.sep)
    assert be.parent(root) == (DRIVES if os.name == "nt" else root)     # Windows: up from C:\ is the list of drives


def test_local_backend_refuses_windows_names_only_on_windows(tmp_path, monkeypatch):
    be = LocalBackend()
    be.windows = True
    with pytest.raises(BackendError, match="reserved"):
        be.mkdir(str(tmp_path / "NUL"))
    with pytest.raises(BackendError, match="not allow"):
        be.open_write(str(tmp_path / "a."))
    be.mkdir(str(tmp_path / "fine"))
    be.windows = False
    be.open_write(str(tmp_path / "ok.txt")).close()
