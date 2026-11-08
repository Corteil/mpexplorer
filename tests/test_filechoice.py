import os

from mpexplorer import filechoice, openwith
from mpexplorer.settings import Settings


def test_first_time_folders(tmp_path):
    (tmp_path / "Downloads").mkdir()
    home = str(tmp_path)
    assert filechoice.default_folder("firmware", "linux", {}, home) == str(tmp_path / "Downloads")
    assert filechoice.default_folder("libraries", "win32", {}, home) == str(tmp_path / "Downloads")
    assert filechoice.default_folder("program", "linux", {}, home) == "/usr/bin"
    assert filechoice.default_folder("program", "win32", {"ProgramFiles": "D:\\Apps"}, home) == "D:\\Apps"
    assert filechoice.default_folder("program", "win32", {}, home) == "C:\\Program Files"
    (tmp_path / "Downloads").rmdir()
    assert filechoice.default_folder("backup", "linux", {}, home) == home          # no Downloads: home


def test_each_kind_starts_where_its_last_choice_was(tmp_path):
    s = Settings(str(tmp_path / "cfg.json"))
    fw = tmp_path / "fw"
    fw.mkdir()
    filechoice.remember(s, "firmware", str(fw / "pico.uf2"))
    assert filechoice.start_folder(s, "firmware", home=str(tmp_path)) == str(fw)
    assert filechoice.start_folder(s, "backup", home=str(tmp_path)) == str(tmp_path)  # another kind: its own default
    assert (tmp_path / "cfg.json").exists()                                            # saved at once
    filechoice.remember(s, "libraries", [str(fw / "a.py"), str(fw / "b.py")])          # several files: their folder
    assert s.get("general.last_dirs")["libraries"] == str(fw)
    filechoice.remember(s, "libraries", str(tmp_path / "my-libs"))                     # a chosen folder: beside it
    assert s.get("general.last_dirs")["libraries"] == str(tmp_path)
    fw.rmdir()
    assert filechoice.start_folder(s, "firmware", home=str(tmp_path)) == str(tmp_path)  # gone: the default again
    filechoice.remember(s, "firmware", None)                                            # nothing chosen: no change
    assert s.get("general.last_dirs")["firmware"] == str(fw)


def test_example_paths_follow_the_system():
    assert filechoice.example_path("my-libs", "win32") == "C:\\Users\\you\\my-libs"
    assert filechoice.example_path("my-libs", "linux") == "/home/pi/my-libs"


def test_program_template():
    npp = "C:\\Program Files\\Notepad++\\notepad++.exe"
    assert openwith.program_template(npp, platform="win32") == '"%s" {file}' % npp
    assert openwith.program_template("C:\\tools\\putty.exe", "{port}", "win32") == "C:\\tools\\putty.exe {port}"
    assert openwith.program_template("/usr/bin/geany", platform="linux") == "/usr/bin/geany {file}"
    assert openwith.program_template("/opt/my apps/ed", platform="linux") == "'/opt/my apps/ed' {file}"
    # what Browse fills in must come back as the same program and the file, on both systems
    assert openwith.expand_command(openwith.program_template(npp, platform="win32"), "D:\\a b\\x.py", "win32") == \
        [npp, "D:\\a b\\x.py"]
    assert openwith.expand_command(openwith.program_template("/opt/my apps/ed", platform="linux"), "/tmp/x.py",
                                   "linux") == ["/opt/my apps/ed", "/tmp/x.py"]


def test_settings_know_last_dirs(tmp_path):
    s = Settings(str(tmp_path / "cfg.json"))
    assert s.get("general.last_dirs") == {}
    s.set("general.last_dirs", {"firmware": os.sep + "x"})
