"""Open in external terminal: command building, per-OS defaults, finding the board again (docs/WINDOWS_PLAN.md s.3)."""
import subprocess
from types import SimpleNamespace as P

import pytest

from mpexplorer import extterm
from mpexplorer.settings import Settings


def which_all(name):
    return "/usr/bin/" + name


def which_none(name):
    return None


def test_port_number():
    assert extterm.port_number("COM5") == "5" and extterm.port_number("com12") == "12"
    assert extterm.port_number("\\\\.\\COM15") == "15"
    assert extterm.port_number("/dev/ttyACM0") == ""


def test_default_on_windows_is_miniterm_in_a_new_console():
    argv, new_console = extterm.command_for("", "COM5", platform="win32", frozen=False)
    assert argv[1:] == ["-m", "serial.tools.miniterm", "--dtr", "1", "--rts", "0", "COM5", "115200"]
    assert new_console


def test_a_frozen_build_runs_its_own_miniterm():
    argv = extterm.miniterm_argv("COM3", frozen=True, executable="C:\\mpx\\mpexplorer.exe")
    assert argv == ["C:\\mpx\\mpexplorer.exe", "--miniterm", "--dtr", "1", "--rts", "0", "COM3", "115200"]


def test_default_on_linux_runs_inside_a_terminal_emulator():
    argv, new_console = extterm.command_for("", "/dev/ttyACM0", platform="linux", which=which_all, frozen=False)
    assert argv[:2] == ["x-terminal-emulator", "-e"] and argv[-2:] == ["/dev/ttyACM0", "115200"]
    assert not new_console
    only_xterm = lambda n: "/usr/bin/xterm" if n == "xterm" else None    # noqa: E731
    assert extterm.command_for("", "/dev/ttyACM0", platform="linux", which=only_xterm)[0][:2] == ["xterm", "-e"]
    with pytest.raises(extterm.TerminalError, match="No terminal program"):
        extterm.command_for("", "/dev/ttyACM0", platform="linux", which=which_none)


def test_putty_and_tera_term_templates_fill_in_and_keep_arguments_whole():
    putty, _ = extterm.command_for(extterm.PRESETS["putty"], "COM7", platform="win32")
    assert putty == ["putty", "-serial", "COM7", "-sercfg", "115200,8,n,1,N"]
    tt, _ = extterm.command_for(extterm.PRESETS["teraterm"], "COM7", title="mpexplorer - Bench badge", platform="win32")
    assert tt[:3] == ["ttermpro", "/C=7", "/BAUD=115200"]
    assert "/W=mpexplorer - Bench badge" in tt and "/WAITCOM" in tt
    assert subprocess.list2cmdline(["/W=mpexplorer - Bench badge"]) == '"/W=mpexplorer - Bench badge"'
    custom, _ = extterm.command_for('"C:\\Program Files\\PuTTY\\putty.exe" -serial {port}', "COM7", platform="win32")
    assert custom == ["C:\\Program Files\\PuTTY\\putty.exe", "-serial", "COM7"]


def test_the_preferences_list_starts_with_miniterm_and_shows_only_this_systems_programs():
    win, linux = extterm.choices("win32"), extterm.choices("linux")
    assert win[0] == linux[0] == extterm.MINITERM
    assert extterm.PRESETS["putty"] in win and extterm.PRESETS["teraterm"] in win
    assert extterm.PRESETS["picocom"] not in win                    # picocom was "not found" on Windows (2026-11-08)
    assert extterm.PRESETS["picocom"] in linux and extterm.PRESETS["teraterm"] not in linux
    assert extterm.template_of(extterm.MINITERM) == "" and extterm.template_of("  ") == ""
    assert extterm.template_of(" putty -serial {port} ") == "putty -serial {port}"


def test_entries_whose_program_is_missing_are_reported_so_they_can_be_greyed_out():
    only_putty = lambda n: "C:\\Program Files\\PuTTY\\putty.exe" if n == "putty" else None    # noqa: E731
    assert extterm.installed(extterm.MINITERM, which=which_none)                 # bundled: always there
    assert extterm.installed("", which=which_none)
    assert extterm.installed(extterm.PRESETS["putty"], "win32", which=only_putty)
    assert not extterm.installed(extterm.PRESETS["teraterm"], "win32", which=only_putty)
    tt = '"C:\\Program Files (x86)\\teraterm\\ttermpro.exe" /C={portnum}'
    assert extterm.installed(tt, "win32", exists=lambda p: p.endswith("ttermpro.exe"))
    assert not extterm.installed(tt, "win32", exists=lambda p: False)
    assert not extterm.installed('putty "unclosed', "linux", which=which_all)    # bad quoting: cannot run


def test_putty_on_a_tildagon_gets_a_warning_and_other_pairs_do_not():
    # 2026-11-08, Windows: closing a Tildagon's port with RTS on froze the badge until its reset button was pressed.
    tildagon = "16d0:120e:7C4FAD337AC00000"
    for argv in (["putty", "-serial", "COM8"], ["C:\\Program Files\\PuTTY\\putty.exe", "-serial", "COM8"],
                 ["/usr/bin/PuTTY.EXE"], ["plink", "-serial", "COM8"]):
        said = extterm.close_hang_warning(argv, tildagon)
        assert "Tildagon" in said and "reset button" in said and "miniterm" in said, argv
    assert "PuTTY keeps the RTS line on" in extterm.close_hang_warning(["putty"], "16D0:120E:abc")
    assert extterm.close_hang_warning(["putty"], "2e8a:0005:E661") == ""            # a Pico
    assert extterm.close_hang_warning(["putty"], "COM8") == ""                       # no USB id
    assert extterm.close_hang_warning(["putty"], "") == ""
    assert extterm.close_hang_warning(extterm.miniterm_argv("COM8", frozen=False), tildagon) == ""
    assert extterm.close_hang_warning(["ttermpro", "/C=8"], tildagon) == ""
    assert extterm.close_hang_warning([], tildagon) == ""


def test_a_hung_board_is_told_apart_from_a_busy_or_missing_port():
    # pyserial's message for a Tildagon frozen by a close with RTS on (2026-11-08)
    hung = Exception("Cannot configure port, something went wrong. Original message: PermissionError(13, 'A device "
                     "attached to the system is not functioning.', None, 31)")
    assert extterm.board_hung(hung)
    raw = OSError(13, "x")
    raw.winerror = 31
    assert extterm.board_hung(raw)
    busy = Exception("could not open port 'COM8': PermissionError(13, 'Access is denied.', None, 5)")
    assert not extterm.board_hung(busy)
    assert not extterm.board_hung(Exception("could not open port 'COM9': FileNotFoundError(2, 'The system cannot find "
                                            "the file specified.', None, 2)"))


def test_a_port_with_odd_characters_stays_one_argument():
    argv = extterm.expand("picocom -b {baud} {port}", "/dev/serial/by-id/usb-My Board; rm -rf", platform="linux")
    assert argv == ["picocom", "-b", "115200", "/dev/serial/by-id/usb-My Board; rm -rf"]


def test_bad_templates():
    with pytest.raises(ValueError):
        extterm.expand("   ", "COM1")
    with pytest.raises(ValueError):
        extterm.expand("putty 'unclosed", "COM1", platform="linux")


def test_launch_never_uses_a_shell_and_names_a_missing_program():
    seen = {}

    def fake_popen(argv, **kw):
        seen.update(kw, argv=argv)
        return P(poll=lambda: None)
    extterm.launch(["putty", "-serial", "COM1"], True, popen=fake_popen)
    assert seen["argv"] == ["putty", "-serial", "COM1"] and "shell" not in seen
    # its own console: nothing redirected, or Windows hands it the windowed parent's missing stdout and it dies
    assert "stdin" not in seen and "stdout" not in seen and "stderr" not in seen
    seen.clear()
    extterm.launch(["x-terminal-emulator", "-e", "miniterm"], False, popen=fake_popen)
    assert seen["stdin"] == subprocess.DEVNULL and "creationflags" not in seen

    def missing(argv, **kw):
        raise FileNotFoundError(argv[0])
    with pytest.raises(extterm.TerminalError, match="putty.exe was not found"):
        extterm.launch(["C:\\x\\putty.exe"], popen=missing)


def test_find_port_follows_the_board_to_another_port():
    ports = [P(device="COM9", vid=0x16D0, pid=0x120E, serial_number="64E8"),
             P(device="COM3", vid=None, pid=None, serial_number=None)]
    assert extterm.find_port("16d0:120e:64E8", ports) == "COM9"
    assert extterm.find_port("16d0:120e:FFFF", ports) is None


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


class Proc:
    def __init__(self):
        self.code = None

    def poll(self):
        return self.code


def make(found, template="", usb="2e8a:0005:abc", which=which_all):
    clock, proc, launched = Clock(), Proc(), []

    def launcher(argv, new_console):
        launched.append((argv, new_console))
        return proc
    ext = extterm.ExternalTerminal("COM4", usb, template, "t", platform="win32", launcher=launcher, which=which,
                                   finder=lambda key: found[0], clock=clock)
    return ext, clock, proc, launched


def test_the_run_reconnects_to_wherever_the_board_comes_back():
    found = [None]
    ext, clock, proc, launched = make(found)
    ext.start()
    assert launched and launched[0][1] is True
    assert ext.running()
    proc.code = 0
    assert not ext.running() and ext.exit_code == 0
    assert ext.board_port() is None and not ext.gave_up()     # it reset and has not come back yet
    clock.t = 5
    found[0] = "COM6"
    assert ext.board_port() == "COM6"


def test_the_wait_for_the_board_ends():
    ext, clock, proc, _ = make([None])
    ext.start()
    proc.code = 1
    ext.running()
    clock.t = extterm.WAIT_FOR_BOARD + 1
    assert ext.gave_up() and ext.exit_code == 1


def test_a_board_without_a_usb_id_comes_back_on_the_same_port():
    ext, _clock, proc, _ = make([None], usb="COM4")
    ext.start()
    proc.code = 0
    ext.running()
    assert ext.board_port() == "COM4"


def test_check_refuses_a_missing_program_before_anything_is_closed():
    ext, _clock, _proc, launched = make([None], template="putty -serial {port}", which=which_none)
    with pytest.raises(extterm.TerminalError, match="putty was not found"):
        ext.check()
    assert launched == []


def test_settings_keys():
    s = Settings(None)
    assert s.get("terminal.external_command") == "" and s.get("terminal.reconnect") == "auto"
    s.set("terminal.reconnect", "ask")
    with pytest.raises(ValueError):
        s.set("terminal.reconnect", "sometimes")


def test_miniterm_option_starts_pyserials_miniterm(monkeypatch):
    import sys

    from mpexplorer import __main__ as entry
    from serial.tools import miniterm
    called = []
    monkeypatch.setattr(miniterm, "main", lambda: called.append(list(sys.argv)))
    monkeypatch.setattr(sys, "argv", ["mpexplorer", "--miniterm", "--dtr", "1", "COM5", "115200"])
    entry.main()
    assert called == [["miniterm", "--dtr", "1", "COM5", "115200"]]


def test_miniterm_failing_keeps_its_exit_code(monkeypatch):
    import sys

    from mpexplorer import __main__ as entry
    from serial.tools import miniterm

    def fails():
        sys.stderr.write("could not open port 'COM99'\n")
        sys.exit(1)
    monkeypatch.setattr(miniterm, "main", fails)
    monkeypatch.setattr(sys, "argv", ["mpexplorer", "--miniterm", "COM99"])
    monkeypatch.setattr(sys, "stdin", None)                     # no console to wait on: it must not block
    with pytest.raises(SystemExit) as stop:
        entry.main()
    assert stop.value.code == 1
    monkeypatch.setattr(miniterm, "main", lambda: None)
    monkeypatch.setattr(sys, "argv", ["mpexplorer", "--miniterm", "COM99"])
    entry.main()                                                # a normal quit (Ctrl+]) is not an error


@pytest.mark.timeout(30)
def test_miniterm_with_these_options_starts_and_reports_a_missing_port():
    """The default command's options are ones this pyserial accepts (it fails on the port, not on an option)."""
    import sys
    argv = extterm.miniterm_argv("NO_SUCH_PORT_X", frozen=False, executable=sys.executable)
    out = subprocess.run(argv, capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL)
    text = out.stdout + out.stderr
    assert "unrecognized arguments" not in text and "invalid choice" not in text
    assert "NO_SUCH_PORT_X" in text or "could not open port" in text.lower()
