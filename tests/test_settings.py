import json
import os
import stat
import threading

import pytest

from mpexplorer import settings as st
from mpexplorer.settings import Settings, config_path, segment


@pytest.fixture
def path(tmp_path):
    return str(tmp_path / "cfg" / "settings.json")


def test_defaults_when_there_is_no_file(path):
    s = Settings.load(path)
    assert s.get("window.width") == 1180 and s.get("general.last_port") == "" and s.get("window.x") is None
    assert s.get("open_with.auto_upload") is True and s.get("connection.fast_mode") is False
    assert s.problems == [] and not s.dirty
    assert s.save() is True and not os.path.exists(path)          # nothing changed: nothing written


def test_round_trip_and_file_permissions(path):
    s = Settings.load(path)
    assert s.set("general.last_port", "/dev/ttyACM0") and s.set("window.width", 900) and s.set("window.x", -5)
    s.set("open_with.by_extension", {".py": "app:geany.desktop"})
    s.set("general.sort.host", ["size", True])
    assert s.dirty and s.save() and not s.dirty
    again = Settings.load(path)
    assert again.get("general.last_port") == "/dev/ttyACM0" and again.get("window.width") == 900
    assert again.get("window.x") == -5 and again.get("open_with.by_extension") == {".py": "app:geany.desktop"}
    assert again.get("general.sort.host") == ["size", True] and again.problems == []
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode) == 0o700
    assert json.load(open(path))["version"] == st.VERSION
    assert not [f for f in os.listdir(os.path.dirname(path)) if f.endswith(".tmp")]


def test_set_reports_whether_anything_changed(path):
    s = Settings.load(path)
    assert s.set("window.width", 1180) is False and not s.dirty    # equal to the default
    assert s.set("window.width", 1000) is True
    assert s.set("window.width", 1000) is False
    s.reset("window.width")
    assert s.get("window.width") == 1180 and s.dirty


def test_invalid_values_and_unknown_keys_are_rejected(path):
    s = Settings.load(path)
    for key, bad in [("window.width", 5), ("window.width", "wide"), ("window.width", True),
                     ("general.show_hidden", "yes"), ("general.sort.host", ["colour", False]),
                     ("open_with.by_extension", {".py": 1}), ("window.x", 1.5)]:
        with pytest.raises(ValueError):
            s.set(key, bad)
    with pytest.raises(KeyError):
        s.set("window.widht", 1)
    with pytest.raises(KeyError):
        s.get("nope")
    assert not s.dirty


def test_get_returns_copies(path):
    s = Settings.load(path)
    s.set("open_with.custom", {"geany": "geany {file}"})
    s.get("open_with.custom")["evil"] = "rm"
    s.get("general.sort.host").append("x")
    assert s.get("open_with.custom") == {"geany": "geany {file}"} and s.get("general.sort.host") == ["name", False]


@pytest.mark.parametrize("content", ["", "   \n", "not json", "[1, 2]", '{"window": ', "\x00\x01"])
def test_bad_files_never_stop_startup(path, content):
    os.makedirs(os.path.dirname(path))
    with open(path, "w") as f:
        f.write(content)
    s = Settings.load(path)
    assert s.get("window.width") == 1180
    if content.strip():
        assert s.problems and os.path.exists(path + ".bad") and not os.path.exists(path)
    s.set("window.width", 800)
    assert s.save() and Settings.load(path).get("window.width") == 800


def test_partly_invalid_values_fall_back_one_by_one(path):
    os.makedirs(os.path.dirname(path))
    json.dump({"version": 1, "window": {"width": "huge", "height": 500}, "general": {"show_hidden": True}},
              open(path, "w"))
    s = Settings.load(path)
    assert s.get("window.width") == 1180 and s.get("window.height") == 500 and s.get("general.show_hidden") is True
    assert any("window.width" in p for p in s.problems) and len(s.problems) == 1


def test_unknown_keys_survive_a_save(path):
    os.makedirs(os.path.dirname(path))
    json.dump({"version": 1, "future": {"colour": "blue"}, "window": {"width": 900, "dpi_hint": 2}},
              open(path, "w"))
    s = Settings.load(path)
    s.set("window.height", 400)
    assert s.save()
    data = json.load(open(path))
    assert data["future"] == {"colour": "blue"} and data["window"] == {"width": 900, "dpi_hint": 2, "height": 400}


def test_newer_file_is_used_but_never_overwritten(path):
    os.makedirs(os.path.dirname(path))
    original = json.dumps({"version": st.VERSION + 1, "window": {"width": 777}})
    open(path, "w").write(original)
    s = Settings.load(path)
    assert s.read_only and s.get("window.width") == 777 and any("newer" in p for p in s.problems)
    s.set("window.height", 400)
    assert s.save() is False and open(path).read() == original


def test_failed_write_keeps_the_old_file_and_leaves_no_temp_files(path, monkeypatch):
    s = Settings.load(path)
    s.set("window.width", 900)
    assert s.save()
    s.set("window.width", 950)

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", boom)
    assert s.save() is False and s.dirty and any("disk full" in p for p in s.problems)
    monkeypatch.undo()
    assert Settings.load(path).get("window.width") == 900
    assert not [f for f in os.listdir(os.path.dirname(path)) if f.endswith(".tmp")]
    assert s.save() is True and Settings.load(path).get("window.width") == 950   # a later save recovers


def test_unwritable_location_does_not_raise(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    s = Settings.load(str(blocker / "sub" / "settings.json"))          # parent is a file, not a directory
    s.set("window.width", 900)
    assert s.save() is False and s.problems


def test_in_memory_settings_have_no_file():
    s = Settings()
    s.set("general.show_hidden", True)
    assert s.save() is True and s.get("general.show_hidden") is True and not s.dirty


def test_listeners_see_changes_and_cannot_break_saving(path):
    s = Settings.load(path)
    seen = []
    s.subscribe(lambda k, v: seen.append((k, v)))
    s.subscribe(lambda k, v: 1 / 0)
    s.set("general.last_port", "COM3")
    s.set("general.last_port", "COM3")                                  # no change, no event
    assert seen == [("general.last_port", "COM3")] and s.save()


def test_unsubscribe(path):
    s = Settings.load(path)
    seen = []
    cb = lambda k, v: seen.append(k)  # noqa: E731
    s.subscribe(cb)
    s.unsubscribe(cb)
    s.unsubscribe(cb)                                                   # twice is harmless
    s.set("window.width", 900)
    assert seen == []


def test_concurrent_sets_and_saves(path):
    s = Settings.load(path)

    def worker(n):
        for i in range(50):
            s.set("window.width", 300 + n * 100 + i)
            s.save()
    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert s.save()
    assert Settings.load(path).get("window.width") == s.get("window.width")


def test_config_path_rules(monkeypatch, tmp_path):
    monkeypatch.delenv("MPEXPLORER_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert config_path() == str(tmp_path / "xdg" / "mpexplorer" / "settings.json")
    monkeypatch.delenv("XDG_CONFIG_HOME")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert config_path() == str(tmp_path / ".config" / "mpexplorer" / "settings.json")
    monkeypatch.setenv("MPEXPLORER_CONFIG", "/x/y.json")
    assert config_path() == "/x/y.json"


def test_segment_makes_safe_dict_keys():
    assert segment("0.1.2") == "0_1_2" and segment("/dev/tty") == "/dev/tty"


def test_every_default_passes_its_own_validator():
    for key, (default, check) in st.SPEC.items():
        assert check(default) == default, key
