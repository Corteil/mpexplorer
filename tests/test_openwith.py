import os

import pytest

from fakedevice import FakeSerial
from mpexplorer import openwith
from mpexplorer.backends import DeviceBackend
from mpexplorer.settings import Settings
from mpexplorer.transport import RawREPL


@pytest.fixture
def settings(tmp_path):
    return Settings(str(tmp_path / "settings.json"))


@pytest.fixture
def bd(tmp_path):
    """The board's root: the fake board uses real paths, so its files live in a temp folder."""
    (tmp_path / "board").mkdir()
    return str(tmp_path / "board")


@pytest.fixture
def dev():
    return DeviceBackend(RawREPL(FakeSerial(paste="on")))


def put(dev, path, data):
    with dev.open_write(path) as w:
        w.write(data)


def read(dev, path):
    with dev.open_read(path) as r:
        return r.read(1 << 20)


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


# -- choosing ---------------------------------------------------------------
def test_expand_command_never_goes_through_a_shell():
    assert openwith.expand_command("geany {file}", "/a b/it's.py") == ["geany", "/a b/it's.py"]
    assert openwith.expand_command("code --goto={file}:1", "/x/y.py") == ["code", "--goto=/x/y.py:1"]
    assert openwith.expand_command('editor --new "{file}" -w', "/p q.txt") == ["editor", "--new", "/p q.txt", "-w"]
    assert openwith.expand_command("thonny", "/f.py") == ["thonny", "/f.py"]                # no {file}: appended
    assert openwith.expand_command("geany {file}", "; rm -rf ~") == ["geany", "; rm -rf ~"]    # one argument
    for bad in ("", "   ", "geany 'unclosed {file}"):
        with pytest.raises(ValueError):
            openwith.expand_command(bad, "/f")


def test_handler_lookup_order(settings):
    assert openwith.handler_for(settings, "main.py") == "editor"                       # text, nothing saved
    assert openwith.handler_for(settings, "photo.png", is_text=False) is None          # ask the user
    settings.set("open_with.by_extension", {".py": "custom:geany", ".png": "image", "text/x-python": "run"})
    assert openwith.handler_for(settings, "MAIN.PY") == "custom:geany"                  # extension, any case
    assert openwith.handler_for(settings, "photo.png", is_text=False) == "image"
    assert openwith.handler_for(settings, "noext", content_type="text/x-python") == "run"   # then the content type
    assert openwith.handler_for(settings, "a.py", content_type="text/x-python") == "custom:geany"   # extension wins


def test_parse_handler(settings):
    settings.set("open_with.custom", {"geany": "geany {file}"})
    assert openwith.parse_handler(settings, "editor") == ("builtin", "editor")
    assert openwith.parse_handler(settings, "hex") == ("builtin", "hex")
    assert openwith.parse_handler(settings, "app:org.geany.Geany.desktop") == ("app", "org.geany.Geany.desktop")
    assert openwith.parse_handler(settings, "custom:geany") == ("command", "geany {file}")
    for stale in ("custom:gone", "custom:", "app:", "bogus", ""):
        with pytest.raises(ValueError):
            openwith.parse_handler(settings, stale)


def test_corrupt_settings_fall_back_to_defaults(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("{ not json")
    assert openwith.handler_for(Settings(str(path)), "a.py") == "editor"


# -- the cache --------------------------------------------------------------
def test_cache_path_stays_inside_the_cache(tmp_path):
    root = str(tmp_path)
    got = openwith.cache_path(root, "2e8a:0005:AB12", "/lib/m.py")
    assert got == os.path.join(root, "2e8a:0005:AB12", "lib", "m.py")
    assert openwith.cache_path(root, "a/b c", "x.py") == os.path.join(root, "a_b_c", "x.py")
    for bad in ("/", "", "/lib/../../etc/passwd", "/a/./b/..", "/x\0y"):
        with pytest.raises(ValueError):
            openwith.cache_path(root, "b", bad)


def test_keep_both_name():
    assert openwith.keep_both_name("/lib/a.py") == "/lib/a (device).py"
    assert openwith.keep_both_name("/main") == "/main (device)"


# -- the watcher: timing ----------------------------------------------------
def make_watcher(tmp_path, clock, content=b"one\n", **kw):
    f = tmp_path / "f.txt"
    f.write_bytes(content)
    return f, openwith.Watcher(str(f), "/f.txt", openwith.Fingerprint(4, 0, "x"), clock=clock, **kw)


def test_one_save_waits_for_the_settle_time(tmp_path):
    clock = Clock()
    f, w = make_watcher(tmp_path, clock)
    w.notice()
    assert not w.pending                                    # nothing changed yet
    f.write_bytes(b"two\n")
    w.notice()
    assert w.pending and not w.ready()
    clock.t += 0.9
    assert not w.ready()
    clock.t += 0.2
    assert w.ready()


def test_rapid_saves_restart_the_wait(tmp_path):
    clock = Clock()
    f, w = make_watcher(tmp_path, clock)
    for i in range(5):
        f.write_bytes(b"edit %d\n" % i)
        w.notice()
        clock.t += 0.6                                      # each save arrives before the 1 s is up
        assert not w.ready()
    clock.t += 0.5
    assert w.ready()


def test_replace_style_save_and_undo(tmp_path):
    clock = Clock()
    f, w = make_watcher(tmp_path, clock)
    tmp = tmp_path / "f.txt~"
    tmp.write_bytes(b"new\n")
    os.replace(tmp, f)                                      # editors save by replacing the file
    w.notice()
    assert w.pending
    f.write_bytes(b"one\n")                                 # undone back to what the device has
    w.notice()
    assert not w.pending


def test_deleted_copy_and_pause(tmp_path):
    clock = Clock()
    f, w = make_watcher(tmp_path, clock)
    f.write_bytes(b"two\n")
    w.notice()
    clock.t += 5
    w.paused = True
    assert w.pending and not w.ready()                      # paused: waits, never uploads
    w.paused = False
    assert w.ready()
    f.unlink()
    w.notice()
    assert w.gone and not w.pending and not w.ready()


# -- the transfers, through the fake board ----------------------------------
def test_download_is_private_and_matches(dev, bd, tmp_path):
    put(dev, bd + "/main.py", b"print('hi')\n")
    local = str(tmp_path / "cache" / "board" / "main.py")
    w = openwith.download_for_edit(dev, bd + "/main.py", local)
    assert open(local, "rb").read() == b"print('hi')\n"
    assert oct(os.stat(local).st_mode & 0o777) == "0o600"
    assert oct(os.stat(os.path.dirname(local)).st_mode & 0o777) == "0o700"
    assert not w.pending
    with pytest.raises(FileNotFoundError):
        openwith.download_for_edit(dev, bd + "/missing.py", str(tmp_path / "x"))


def test_save_uploads_and_the_old_file_is_replaced(dev, bd, tmp_path):
    put(dev, bd + "/main.py", b"v1\n")
    clock = Clock()
    local = str(tmp_path / "main.py")
    w = openwith.download_for_edit(dev, bd + "/main.py", local, clock=clock)
    open(local, "wb").write(b"v2 with more text\n")
    w.notice()
    clock.t += 2
    assert w.ready()
    assert openwith.upload_changes(dev, w) == "uploaded"
    assert read(dev, bd + "/main.py") == b"v2 with more text\n"
    assert [e.name for e in dev.listdir(bd)] == ["main.py"]            # no .mpx-part left behind
    assert not w.pending and not w.ready()
    open(local, "wb").write(b"v3\n")                                    # a second save goes up too
    w.notice()
    clock.t += 2
    assert openwith.upload_changes(dev, w) == "uploaded" and read(dev, bd + "/main.py") == b"v3\n"


def test_save_during_upload_is_not_lost(dev, bd, tmp_path):
    put(dev, bd + "/a.txt", b"v1\n")
    local = str(tmp_path / "a.txt")
    w = openwith.download_for_edit(dev, bd + "/a.txt", local)
    open(local, "wb").write(b"v2\n")
    w.notice()
    snap = w.snapshot()
    open(local, "wb").write(b"v3 arrived while uploading\n")
    w.uploaded(openwith.device_fingerprint(dev, bd + "/a.txt"), snap)
    assert w.pending                                                    # v3 still has to go up


def test_conflict_asks_then_overwrite(dev, bd, tmp_path):
    put(dev, bd + "/a.txt", b"v1\n")
    local = str(tmp_path / "a.txt")
    w = openwith.download_for_edit(dev, bd + "/a.txt", local)
    put(dev, bd + "/a.txt", b"changed on the board\n")                       # the board (or another tool) changed it
    open(local, "wb").write(b"my edit\n")
    w.notice()
    assert openwith.upload_changes(dev, w) == "conflict"
    assert read(dev, bd + "/a.txt") == b"changed on the board\n"             # nothing was touched
    assert openwith.upload_changes(dev, w, "overwrite") == "uploaded"
    assert read(dev, bd + "/a.txt") == b"my edit\n"


def test_conflict_keep_both(dev, bd, tmp_path):
    put(dev, bd + "/lib.py", b"v1\n")
    local = str(tmp_path / "lib.py")
    w = openwith.download_for_edit(dev, bd + "/lib.py", local)
    put(dev, bd + "/lib.py", b"board version\n")
    open(local, "wb").write(b"my edit\n")
    w.notice()
    assert openwith.upload_changes(dev, w, "keep_both") == "uploaded"
    assert read(dev, bd + "/lib.py") == b"my edit\n"
    assert read(dev, bd + "/lib (device).py") == b"board version\n"


def test_conflict_skip_stops_asking(dev, bd, tmp_path):
    put(dev, bd + "/a.txt", b"v1\n")
    local = str(tmp_path / "a.txt")
    w = openwith.download_for_edit(dev, bd + "/a.txt", local)
    put(dev, bd + "/a.txt", b"board\n")
    open(local, "wb").write(b"mine\n")
    w.notice()
    assert openwith.upload_changes(dev, w, "skip") == "skipped"
    assert read(dev, bd + "/a.txt") == b"board\n" and not w.pending
    with pytest.raises(ValueError):
        w.baseline = None
        open(local, "wb").write(b"mine again\n")
        w.notice()
        openwith.upload_changes(dev, w, "bogus")


def test_device_file_deleted_meanwhile_is_a_conflict(dev, bd, tmp_path):
    put(dev, bd + "/a.txt", b"v1\n")
    local = str(tmp_path / "a.txt")
    w = openwith.download_for_edit(dev, bd + "/a.txt", local)
    dev.remove(bd + "/a.txt")
    open(local, "wb").write(b"mine\n")
    w.notice()
    assert openwith.upload_changes(dev, w) == "conflict"
    assert openwith.upload_changes(dev, w, "keep_both") == "uploaded"      # nothing to rename: just upload
    assert read(dev, bd + "/a.txt") == b"mine\n"


def test_local_copy_deleted_means_nothing_to_upload(dev, bd, tmp_path):
    put(dev, bd + "/a.txt", b"v1\n")
    local = str(tmp_path / "a.txt")
    w = openwith.download_for_edit(dev, bd + "/a.txt", local)
    os.unlink(local)
    assert openwith.upload_changes(dev, w) == "skipped"
    assert read(dev, bd + "/a.txt") == b"v1\n"
