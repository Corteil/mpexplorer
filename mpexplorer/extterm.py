"""The board's real REPL in an external terminal program (docs/WINDOWS_PLAN.md, section 3). GUI-free.

Only one program can hold a serial port, so the window disconnects first, then this module starts the program (never
through a shell), watches it, and when it exits finds the board again by its USB id (the port name can change after a
reset) so the window can reconnect.

Command templates are split into words first and the placeholders filled in afterwards, so a port or title with
spaces cannot split an argument: {port} (COM5, /dev/ttyACM0), {portnum} (5 for COM5, for Tera Term's /C=), {baud},
{title}. An empty template means the default for this system: pyserial's miniterm (no extra install), in a new
console window on Windows and inside a terminal emulator on Linux.
"""
import os
import re
import shutil
import subprocess
import sys
import time

from .openwith import split_command

BAUD = 115200
RECONNECT = ("auto", "ask", "never")
WAIT_FOR_BOARD = 15.0           # seconds to wait for the board's port to come back after the program exits

# Ready-made templates for the Preferences list (the PuTTY and Tera Term options are from their manuals).
PRESETS = {
    "putty": "putty -serial {port} -sercfg {baud},8,n,1,N",
    "teraterm": 'ttermpro /C={portnum} /BAUD={baud} /CDATABIT=8 /CPARITY=none /CSTOPBIT=1 /CFLOWCTRL=none '
                '/W={title} /WAITCOM /AUTOWINCLOSE=off',     # one argument: Windows quotes it if it has spaces
    "picocom": "picocom -b {baud} {port}",
}

# Boards that hang when their port is closed while RTS is on: the screen freezes, the USB serial stops answering
# (Windows: error 31, "A device attached to the system is not functioning") and only the reset button brings it back.
# Opening with RTS on is harmless, and turning RTS off before the close avoids it. Seen on Windows with a Tildagon,
# 2026-11-08.
HANGS_ON_CLOSE_WITH_RTS = {"16d0:120e": "Tildagon"}
# Programs that keep RTS on with no option to turn it off: PuTTY's serial code asserts it whatever -sercfg says (plink
# shares that code). The name the warning uses for each.
KEEP_RTS_ON = {"putty": "PuTTY", "plink": "plink"}

# Linux terminal emulators that take a command after -e (or --), tried in this order for the default command.
LINUX_TERMINALS = (("x-terminal-emulator", "-e"), ("lxterminal", "-e"), ("gnome-terminal", "--"),
                   ("konsole", "-e"), ("xfce4-terminal", "-x"), ("xterm", "-e"))


class TerminalError(Exception):
    pass


def is_windows(platform=None):
    return (platform or sys.platform).startswith("win")


def port_number(port):
    """'COM5' -> '5' (Tera Term wants the number), anything else -> ''."""
    m = re.fullmatch(r"(?i)(?:\\\\\.\\)?COM(\d+)", port.strip())
    return m.group(1) if m else ""


def miniterm_argv(port, baud=BAUD, frozen=None, executable=None):
    """pyserial's miniterm with DTR on and RTS off, as the Tildagon needs (DTR off corrupts long replies, RTS can reset
    an ESP32-S3). A frozen build has no separate Python: it runs itself with --miniterm (see __main__)."""
    from .paths import helper_executable
    exe = executable or helper_executable()
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    head = [exe, "--miniterm"] if frozen else [exe, "-m", "serial.tools.miniterm"]
    return head + ["--dtr", "1", "--rts", "0", port, str(baud)]


def expand(template, port, baud=BAUD, title="", platform=None):
    """A custom template as an argument list. ValueError for an empty template or bad quoting."""
    words = split_command(template, platform)
    if not words:
        raise ValueError("the command is empty")
    values = {"port": port, "portnum": port_number(port), "baud": str(baud), "title": title or port}
    return [re.sub(r"\{(port|portnum|baud|title)\}", lambda m: values[m.group(1)], w) for w in words]


def linux_terminal(which=shutil.which):
    """(program, flag) of the first terminal emulator found, or None."""
    for program, flag in LINUX_TERMINALS:
        if which(program):
            return program, flag
    return None


def command_for(template, port, baud=BAUD, title="", platform=None, which=shutil.which, frozen=None):
    """(argv, new_console): what to start. new_console says the program needs a console window of its own (Windows)."""
    platform = platform or sys.platform
    if (template or "").strip():
        return expand(template, port, baud, title, platform), is_windows(platform)
    argv = miniterm_argv(port, baud, frozen)
    if is_windows(platform):
        return argv, True
    found = linux_terminal(which)
    if not found:
        raise TerminalError("No terminal program was found to run miniterm in. Install one (for example lxterminal), "
                            "or set a command in Preferences > Terminal.")
    program, flag = found
    return [program, flag] + argv, False


def launch(argv, new_console=False, popen=subprocess.Popen):
    """Start the program, without a shell. A missing program is a TerminalError that names it.

    With a console of its own nothing is redirected: on Windows, redirecting any one stream makes the child take the
    other two from the parent, and the windowed mpexplorer.exe has none, so miniterm died at once (exit code 1) when it
    first wrote. Seen with the Windows build on 2026-11-06, reproduced from a parent started without std handles."""
    if new_console:
        kwargs = {"creationflags": getattr(subprocess, "CREATE_NEW_CONSOLE", 0)}
    else:
        kwargs = {"stdin": subprocess.DEVNULL}
    try:
        return popen(argv, **kwargs)
    except FileNotFoundError:
        raise TerminalError("%s was not found. Install it, or choose another program in Preferences > Terminal."
                            % os.path.basename(argv[0]))
    except OSError as e:
        raise TerminalError("Cannot start %s: %s" % (os.path.basename(argv[0]), e))


def program_name(argv):
    """'C:\\Program Files\\PuTTY\\putty.exe' -> 'putty'."""
    name = re.split(r"[\\/]", argv[0])[-1].lower() if argv else ""
    return name[:-4] if name.endswith(".exe") else name


def close_hang_warning(argv, usb_key):
    """The warning to show before this program opens this board, or '' when the pair is safe."""
    board = HANGS_ON_CLOSE_WITH_RTS.get(":".join((usb_key or "").lower().split(":")[:2]))
    program = KEEP_RTS_ON.get(program_name(argv))
    if not board or not program:
        return ""
    return ("%s keeps the RTS line on, and a %s hangs when its port is closed with RTS on: when you close %s, the "
            "badge's screen freezes and it stops answering on USB until you press its reset button, so mpexplorer "
            "cannot reconnect.\n\nminiterm (the default terminal) keeps RTS off and does not have this problem."
            % (program, board, program))


def board_hung(exc):
    """True when opening the port failed because the board stopped answering on USB, not because it is busy or gone:
    Windows' error 31, which pyserial puts in its "Cannot configure port" message."""
    return getattr(exc, "winerror", None) == 31 or "not functioning" in str(exc)


HUNG_ADVICE = ("The board is plugged in but has stopped answering on USB. Press its reset button (or unplug it and "
               "plug it in again), then press Connect.")


def find_port(usb_key, comports=None):
    """The port the board with this USB id (vid:pid:serial, as ports.board_id gives) is on now, or None."""
    from .ports import _key
    if comports is None:
        try:
            from serial.tools import list_ports
        except ImportError:
            return None
        comports = list_ports.comports()
    for p in comports:
        if p.vid is not None and _key(p) == usb_key:
            return p.device
    return None


class ExternalTerminal:
    """One run of the external program for one board: launch, then poll() until it ends, then wait_for_board().
    clock and finder are for tests."""

    def __init__(self, port, usb_key, template="", title="", platform=None, launcher=launch, which=shutil.which,
                 finder=find_port, clock=time.monotonic):
        self.port, self.usb_key, self.title = port, usb_key, title
        self.template, self.platform = template, platform
        self._launch, self._which, self._find, self._clock = launcher, which, finder, clock
        self.proc = None
        self.exit_code = None
        self._ended_at = None

    def check(self):
        """Before disconnecting: build the command and make sure its program exists, so a missing program leaves the
        board connected. Raises TerminalError or ValueError (a bad template)."""
        argv, new_console = command_for(self.template, self.port, BAUD, self.title, self.platform, self._which)
        program = argv[0]
        if not os.path.isabs(program) and not self._which(program):
            raise TerminalError("%s was not found. Install it, or choose another program in Preferences > Terminal."
                                % program)
        self.argv, self.new_console = argv, new_console
        return argv

    def start(self):
        if getattr(self, "argv", None) is None:
            self.check()
        self.proc = self._launch(self.argv, self.new_console)
        return self.argv

    def running(self):
        """True while the program runs. A program that exits at once with an error code is reported by the caller."""
        if self.proc is None or self.exit_code is not None:
            return False
        code = self.proc.poll()
        if code is None:
            return True
        self.exit_code = code
        self._ended_at = self._clock()
        return False

    def board_port(self):
        """Where the board is now: the same port if it is still there (or it has no USB id), else wherever its USB id
        shows up, else None while it is gone."""
        if self.usb_key == self.port or ":" not in self.usb_key:
            return self.port
        return self._find(self.usb_key)

    def gave_up(self):
        """True once WAIT_FOR_BOARD seconds have passed since the program ended without the board coming back."""
        return self._ended_at is not None and self._clock() - self._ended_at > WAIT_FOR_BOARD
