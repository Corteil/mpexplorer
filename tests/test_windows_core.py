"""The rest of the Windows plan's step 1: drives view, hidden files, drive discovery, Open with, the copy check, CRLF.
The Windows branches take the platform or the drive list as arguments, so these run on Linux too."""
import os

import pytest

from mpexplorer import circuitpython as cp
from mpexplorer import firmware as fw
from mpexplorer import openwith, winnames
from mpexplorer.backends import DRIVES, Entry, LocalBackend, is_hidden
from mpexplorer.errors import BackendError
from mpexplorer.ops import ConflictPolicy, Progress, check_windows_names, copy_tree


# -- drives -----------------------------------------------------------------
def test_removable_drives_skip_fixed_disks_and_floppies():
    drives = [("A:\\", ""), ("C:\\", "Windows"), ("E:\\", "RPI-RP2"), ("F:\\", "CIRCUITPY")]
    kinds = {"A:\\": 2, "C:\\": 3, "E:\\": 2, "F:\\": 2}
    assert winnames.removable_drives(drives, kinds.get) == [("E:\\", "RPI-RP2"), ("F:\\", "CIRCUITPY")]


def test_circuitpy_drives_found_by_volume_label():
    drives = [("E:\\", "RPI-RP2"), ("F:\\", "CIRCUITPY"), ("G:\\", "circuitpy1"), ("H:\\", "")]
    assert cp.mounts(drives=drives) == ["F:\\", "G:\\"]


def test_uf2_drives_found_on_drive_letters(tmp_path):
    good, other = tmp_path / "good", tmp_path / "other"
    good.mkdir()
    other.mkdir()
    (good / "INFO_UF2.TXT").write_text("UF2 Bootloader v3.0\nModel: Raspberry Pi RP2\nBoard-ID: RPI-RP2\n")
    found = fw.detect(drives=[(str(good), "RPI-RP2"), (str(other), "STICK")])
    assert [d.path for d in found] == [str(good)]           # a drive counts only with INFO_UF2.TXT on it


# -- drives view and hidden files in the host pane ---------------------------
@pytest.fixture
def win_be(monkeypatch):
    be = LocalBackend()
    be.windows = True
    monkeypatch.setattr(be, "list_drives", lambda: [("C:\\", "Windows"), ("E:\\", "CIRCUITPY")])
    return be


def test_a_drive_root_goes_up_to_the_list_of_drives(win_be):
    assert win_be.parent("C:\\") == DRIVES
    assert win_be.parent(DRIVES) == DRIVES
    assert [e.name for e in win_be.listdir(DRIVES)] == ["C:\\", "E:\\"]
    assert all(e.is_dir for e in win_be.listdir(DRIVES))
    assert win_be.join(DRIVES, "E:\\") == "E:\\"
    assert win_be.stat(DRIVES).is_dir


def test_nothing_can_be_written_into_the_list_of_drives(win_be):
    with pytest.raises(BackendError, match="open a drive"):
        win_be.mkdir(DRIVES)
    with pytest.raises(BackendError, match="Open a drive"):
        win_be.free_space(DRIVES)


def test_linux_has_no_list_of_drives():
    be = LocalBackend()
    be.windows = False
    assert be.parent(os.sep) == os.sep and be.join(DRIVES, "x") == os.path.join(DRIVES, "x")


def test_hidden_is_a_dot_or_the_windows_attribute():
    assert is_hidden(Entry(".git", True)) and is_hidden(Entry("desktop.ini", False, hidden=True))
    assert not is_hidden(Entry("main.py", False))


@pytest.mark.skipif(os.name != "nt", reason="the hidden attribute exists on Windows only")
def test_listing_reads_the_hidden_attribute(tmp_path):
    import ctypes
    (tmp_path / "secret.txt").write_text("x")
    (tmp_path / "plain.txt").write_text("x")
    ctypes.windll.kernel32.SetFileAttributesW(str(tmp_path / "secret.txt"), 2)
    got = {e.name: e.hidden for e in LocalBackend().listdir(str(tmp_path))}
    assert got == {"secret.txt": True, "plain.txt": False}


# -- Open with ----------------------------------------------------------------
def test_system_default_per_platform():
    assert openwith.system_open_command("linux") == "xdg-open {file}"
    assert openwith.system_open_command("win32") == "explorer.exe {file}"
    assert openwith.system_open_command("darwin") == "open {file}"


def test_windows_commands_keep_their_backslashes():
    template = '"C:\\Program Files\\Geany\\bin\\geany.exe" -i {file}'
    path = "C:\\Users\\b\\My Files\\main.py"
    assert openwith.expand_command(template, path, "win32") == [
        "C:\\Program Files\\Geany\\bin\\geany.exe", "-i", path]
    assert openwith.expand_command("notepad.exe", path, "win32") == ["notepad.exe", path]
    # Linux keeps POSIX quoting
    assert openwith.expand_command("geany '{file}'", "/a b/c.py", "linux") == ["geany", "/a b/c.py"]


def test_bad_quoting_still_fails_on_windows():
    with pytest.raises(ValueError):
        openwith.expand_command('"C:\\no closing quote {file}', "x", "win32")


# -- copying to a Windows disk --------------------------------------------------
def test_check_windows_names():
    check_windows_names("/lib", ["a.py", "b.py"])
    with pytest.raises(BackendError, match="aux.py from /lib.*reserved"):
        check_windows_names("/lib", ["aux.py"])
    with pytest.raises(BackendError, match="main.py and Main.py.*capital"):
        check_windows_names("/", ["main.py", "Main.py"])


def test_copy_to_windows_stops_before_writing_a_folder_with_a_clash(tmp_path):
    src, dst = tmp_path / "board", tmp_path / "out"
    src.mkdir()
    (src / "ok.py").write_text("1")
    names = ["ok.py", "README", "readme"]

    class Source(LocalBackend):          # a board with names a Windows disk cannot hold, whatever this host is
        def listdir(self, p, mtimes=True):
            return [Entry(n, False, 1) for n in names] if p == str(src) else super().listdir(p)

    dest = LocalBackend()
    dest.windows = True
    with pytest.raises(BackendError, match="capital"):
        copy_tree(Source(), str(src), dest, str(dst), Progress(), ConflictPolicy(lambda n: ("overwrite", True)))
    assert os.listdir(dst) == []          # the folder was made, but nothing was written into it


def test_long_paths_get_the_prefix_only_on_windows(tmp_path):
    long = "C:\\" + "\\".join(["d" * 40] * 7) + "\\f.txt"
    be = LocalBackend()
    be.windows = True
    assert be._long(long).startswith("\\\\?\\")
    be.windows = False
    assert be._long(long) == long


# -- CRLF from an editor ------------------------------------------------------
def test_crlf_note(tmp_path):
    crlf, lf, txt = tmp_path / "a.py", tmp_path / "b.py", tmp_path / "c.txt"
    crlf.write_bytes(b"print(1)\r\nprint(2)\r\n")
    lf.write_bytes(b"print(1)\n")
    txt.write_bytes(b"x\r\n")
    assert "CRLF" in openwith.crlf_note(str(crlf))
    assert openwith.crlf_note(str(lf)) == "" and openwith.crlf_note(str(txt)) == ""
    assert openwith.crlf_note(str(tmp_path / "gone.py")) == ""
