# Plan: a Windows standalone build, and the external terminal

Status (2026-11-06): **step 1 started** on branch `windows-portability` (see "Progress" below); the GUI has not been run on Windows.

## Progress (2026-11-06, on the owner's Windows 11 PC, Python 3.12, no GTK)
- Done: `paths.py` (`config_dir`, `cache_dir`, `settings_file`, `portable.txt` mode with the read-only fallback and `portable_problem()`),
  used by settings, clipboard, libraries, firmware, firmware catalog, watchdog and the Open with cache. `winnames.py` (`name_problem`,
  `path_problem`, `case_clashes`, `long_path`, `parent_path`, `list_drives`, `volume_label`). `LocalBackend`: `shutil.disk_usage`, drive
  roots are their own parent, Windows names refused with the reason (mkdir, rename, open for writing). Tests: `tests/test_paths.py`.
- Bugs found by running the suite on Windows and fixed: esptool's output was read with `select()` on a pipe (fails on Windows: now a
  reader thread); the CIRCUITPY copy called `fsync` on a read-only handle; `libraries._mkdirs` split host paths on `/`; the adaptive
  upload probe timed sends with `time.monotonic()`, which ticks every ~15 ms on Windows (now `perf_counter`).
- Suite on Windows: 485 passed, 38 skipped (pty / real MicroPython, the Linux launcher, a shell-script fake esptool, six Open with
  tests whose fake board uses host paths as board paths, and the GUI tests without PyGObject). The fake board gained `os.uname` and
  `os.statvfs` shims where the host has none.
- Not done in step 1: a drives view in the host pane (the helper exists, the pane does not use it), the Windows hidden attribute,
  CIRCUITPY / UF2 drive discovery by drive letter, Open with (`os.startfile`), checking a whole copy for case clashes and long paths
  before it starts, the CRLF warning.
- Step 2 done (2026-11-06): CI job `windows` on `windows-latest`, Python 3.11 and 3.13, ruff + the suite with `-rs` so every skip and its
  reason is in the log. No GUI tests there (no PyGObject) until the packaging step brings MSYS2. Section 1 comes from reading the code on
`main` (`02e9bc7`, v0.1.1); sections 2 to 4 also use the PuTTY and Tera Term manuals, quoted where it matters. Anything marked
*unverified* is from memory and must be checked on a real Windows machine before it is relied on.

## Decisions (owner, 2026-11-06)
- On Windows the console window has **one** kind of REPL (the emulated one) plus a **button that opens the board's real terminal
  in an external program**. The Mode row with the two radio buttons is not shown there.
- **GTK 3** for the Windows build. MSYS2 packages GTK 3, GTK 4 and PyGObject (*unverified*), so no GTK 4 port is needed; it would
  be a rewrite of every window and would not give a terminal widget either.
- **After the external terminal closes, reconnect automatically** (`terminal.reconnect` defaults to `auto`; `ask` and `never` stay
  available as settings).
- **Windows 10 and 11 only.** (MSYS2's UCRT64 toolchain needs Windows 10 or later anyway.)
- **First release: a portable zip**, no installer yet (see "Portable build" below).
- **Code signing: yes, but it waits for the certificate** (owner, 2026-11-06). Nothing is built or wired for signing until the owner
  has one. Until then builds are unsigned and the README says what SmartScreen will show. The executables in the zip are what get signed.
- **`portable.txt` mode: yes.** If a file named `portable.txt` sits next to the exe, settings and caches live in a `data` folder beside
  it instead of `%APPDATA%` / `%LOCALAPPDATA%`.
- **Tests run on the owner's Windows PC** (offered 2026-11-06), connected to this session through Remote Control; real boards for the
  hardware table are plugged into that PC.

## 1. What stops the program running on Windows today
Portable already: serial through pyserial (`COMn`, vendor/product ids), raw REPL, copy/sync logic, settings JSON, board paths
(always `/`, via `posixpath`), worker threads.

| Area | Where | Problem | Fix |
|---|---|---|---|
| Free disk space | `backends.py` `LocalBackend.free_space` | `os.statvfs` does not exist | `shutil.disk_usage` |
| Local navigation | `backends.py` `LocalBackend.parent/join` | `os.path.dirname(p.rstrip(os.sep)) or os.sep` is wrong for drive roots (`C:\`); no list of drives; "hidden" means a leading dot only | a drives view, the Windows hidden attribute |
| Config and cache folders | `settings.py` (`~/.config`), `libraries.py`, `clipboard.py`, `watchdog.py` (`~/.cache`) | wrong places on Windows | one helper: `%APPDATA%` / `%LOCALAPPDATA%` (honouring the existing `MPEXPLORER_CONFIG`) |
| Finding drives | `circuitpython.py` (`/proc/mounts`, `CIRCUITPY1`), `firmware.py` `detect()` (`/proc/self/mounts`, `INFO_UF2.TXT`) | Linux only | list drive letters and volume labels (`CIRCUITPY`, a drive holding `INFO_UF2.TXT`) |
| Open with | `openwith.py` (`SYSTEM_OPEN = "xdg-open {file}"`, `os.chmod 0o600`) and the Gio app list | `xdg-open` missing; Gio lists few Windows apps | `os.startfile` for the system default, custom commands for the rest, chmod is a harmless no-op |
| Desktop launcher | `desktop.py` (`.desktop` files) | Linux only | none for the portable zip (run it from its folder); the icon is built into the exe and the process sets an explicit app id so the taskbar groups and names it. A Start Menu shortcut comes with a later installer |
| Terminal mode | `gtkui/terminal.py` (VTE) | no Windows build of VTE (*unverified for MSYS2*) | section 3 |
| esptool | `firmware.py` runs it as a separate program (`pipx install esptool`) | a standalone build has no pipx | bundle it (the `firmware` extra in `pyproject.toml`) |
| File names from the board | `ops.copy_tree`, sync | names Windows refuses (`:`, `*`, trailing dot, `CON`/`NUL`), two board files that differ only in letter case, paths over 260 characters | check names before writing and say which file and why; long-path prefix |
| Line endings | Open with: a file edited in Notepad comes back with CRLF | CRLF gets uploaded and sync sees the file as changed | decide: upload bytes as they are (default) and warn once for `.py` files with CRLF |
| Serial behaviour | `transport.py` | Windows drivers may toggle DTR/RTS on open; the Tildagon needs DTR on and RTS off | hardware test (section 4) |
| Tests | `ptyserial.py`, `rawrepl_shim.py`, `test_real_micropython.py`, GUI tests | POSIX only | a Windows CI job that runs the portable subset (section 5) |

## 2. GTK 3 on Windows: packaging
Route: **MSYS2 UCRT64 + PyInstaller** (package names *unverified*: `mingw-w64-ucrt-x86_64-gtk3`, `-python-gobject`,
`-python-pyserial`, `-python-pip`, then `pip install pyinstaller` inside it).
- Bundle: the GTK/Gdk/GdkPixbuf/Pango/Atk typelibs and DLLs, the gdk-pixbuf loaders cache, GSettings schemas
  (`glib-compile-schemas`), an icon theme (Adwaita + hicolor; the code already falls back to text labels when an icon is missing),
  fonts, `icon.png`; a launcher or runtime hook sets `GI_TYPELIB_PATH`, `GDK_PIXBUF_MODULE_FILE`, `XDG_DATA_DIRS`.
- Output: a **folder, shipped as a zip** (first release; an installer such as Inno Setup can come later), not a single exe: a
  one-file exe unpacks on every start and is flagged by antivirus more often. Without a code-signing certificate Windows
  SmartScreen will warn on first run; the decision is to sign (see Decisions).
- Expect: roughly 100 to 200 MB, a non-native look, high-DPI scaling needing care (a DPI-aware manifest).
- CI: `windows-latest` with `msys2/setup-msys2`, upload the folder as an artifact, later attach the zip to the release workflow.

### Portable build
- Unzip anywhere and run `mpexplorer.exe`; nothing is written outside the folder except the user's settings and cache.
- Settings and caches go to `%APPDATA%\mpexplorer` / `%LOCALAPPDATA%\mpexplorer` as for any program. **If a file named `portable.txt`
  sits next to the exe, they are kept in a `data` folder beside it instead** (the way Tera Term's `portable.ini` works), so the whole
  thing can live on a USB stick. `MPEXPLORER_CONFIG` still overrides the settings file in both cases. The same helper that picks the
  config and cache folders (step 1) decides this, and has tests for: no marker, marker present, marker present but the folder is
  read-only (fall back to `%APPDATA%` and say so once).
- The exe needs no Python or GTK installed; the board drivers are Windows' own (Windows 10 and 11 include a USB serial driver
  for the CDC boards; the UF2 bootloader appears as a normal drive).
- Rejected for now: **Tk** (removed on 2026-10-30; the app has grown far past what the old code did), **Qt/PySide6** (easiest
  packaging but a rewrite), **WSL2** (not a standalone binary).

## 3. Console and terminal on Windows
No terminal widget exists in GTK 3 itself; the terminal is VTE, a separate library, and it has no Windows build. `miniterm` (pyserial)
is a console program, not a widget, so it cannot be embedded either. Hence:

**One REPL plus an "Open in external terminal..." button.**
- The Mode row (Emulated / Terminal) is shown only where `terminal.available()` is true (VTE present). The button is shown
  everywhere, so a Linux machine without VTE gets the single console plus the button instead of a greyed-out radio.
- Only one program can hold a serial port, so the button **disconnects** first:
  1. confirm ("mpexplorer will disconnect while the terminal is open");
  2. leave the raw REPL (the board is at its `>>>` prompt) and close the port, without a reset unless the user has that option on;
  3. start the program, **without a shell** (an argument list), with `{port}`, `{baud}`, `{title}` and `{portnum}` filled in;
  4. watch the process; when it exits **reconnect automatically** (setting `terminal.reconnect`: `auto` (default) / `ask` / `never`);
  5. after a reset the board can come back under another port name, so find it again by `ports.board_id()` (vid:pid:serial).
- Settings: `terminal.external_command` (template, empty = the default for this system) and `terminal.reconnect` (default `auto`).
- Auto-reconnect waits for the board to come back (a port with the same `board_id`, up to a timeout) and says so in the status bar;
  if it does not appear it stops and tells the user, so a board that was unplugged does not leave the app polling forever.
- While it is open the file panes are disconnected and a badge's screen stays frozen as it is when connected today.

### Launch commands
| Program | Command | Notes |
|---|---|---|
| **miniterm** (pyserial, no extra install) | `python -m serial.tools.miniterm --dtr 1 --rts 0 {port} {baud}` | `--dtr`/`--rts` "set initial DTR/RTS line state (possible values: 0, 1)" (checked in `--help` locally). In the frozen exe there is no separate Python: start our own exe with a hidden `--miniterm` option, in a new console window. Exit with Ctrl+]. The recommended default. |
| **PuTTY** | `putty -serial {port} -sercfg {baud},8,n,1,N` | See quotes below. Line states at open are not documented. |
| **Tera Term** | `ttermpro /C={portnum} /BAUD={baud} /CDATABIT=8 /CPARITY=none /CSTOPBIT=1 /CFLOWCTRL=none /W="{title}" /WAITCOM /AUTOWINCLOSE=off` | Optional `/M=<macro file>` with `setdtr 1` and `setrts 0`. |
| Linux / Pi (*unverified*) | `picocom -b {baud} {port}`, or `mpremote connect {port} repl`, run inside the system terminal emulator | |

**PuTTY manual (0.85, `puttydoc.txt`)**
- Section 3.11.3 (starting PuTTY): "To start a connection to a serial port, e.g. COM1: `putty.exe -serial com1`".
- 3.11.3.24 `-sercfg`: "Any single digit from 5 to 9 sets the number of data bits. `1`, `1.5` or `2` sets the number of stop bits. Any
  other numeric string is interpreted as a baud rate. A single lower-case letter specifies the parity: `n` for none, `o` for odd,
  `e` for even, `m` for mark and `s` for space. A single upper-case letter specifies the flow control: `N` for none, `X` for
  XON/XOFF, `R` for RTS/CTS and `D` for DSR/DTR." Example: `-sercfg 19200,8,n,1,N`.
- 4.29.6 flow control: None / XON/XOFF / RTS/CTS / DSR/DTR are described, but **the manual does not say what DTR and RTS are
  when the port opens with flow control None**, and has no option to set them separately.
- 3.6: "PuTTY in serial mode will remain connected until you close the window using the close button."
- 4.1.3 "Close window on exit" (Always / Never / Only on clean exit, the default) is a saved-session setting, reachable from a
  launcher only through `-load "session name"`. I found no `-title` option in the manual. The serial text only names Windows
  `COMn` ports.

**Tera Term 5 manual (`commandline/teraterm.html`, `macro/command/setdtr.html`, `setrts.html`, `connect.html`)**
- `/C=<serial port#>`: "`/C=1` COM1 ... `/C=256` COM256. By default, maximum serial port number is 256" (the `MaxComPort` setting).
- `/BAUD=<speed>` (same as `/SPEED=`), `/CDATABIT=7|8`, `/CPARITY=none|odd|even|mark|space`, `/CSTOPBIT=1|1.5|2`,
  `/CFLOWCTRL=x|hard|rtscts|none|dsrdtr`, `/W="<window title>"`, `/F=<setup file>` (relative paths are under
  `%APPDATA%\teraterm5\`), `/M=<macro file>`, `/X=` `/Y=` position.
- `/WAITCOM`: "When serial port is specified with the /C= option and the port does not exist, Tera Term will wait for port
  connection" (useful when a badge re-appears after a reset). `/AUTOWINCLOSE=on|off`.
- `setdtr <flag>` / `setrts <flag>`: "If the connection is not serial connection or flow control is not 'none', this command will
  do nothing" (version 4.59 or later). **The manual does not say what the lines are before the macro runs**, so a brief
  assertion at open is still possible.
- The `connect` macro command can start Tera Term with command-line parameters, so one macro file can connect and then set the lines.

### Unknowns that need a real Windows PC and boards
| Question | miniterm | PuTTY | Tera Term |
|---|---|---|---|
| Does a Tildagon (ESP32-S3) reset when the program opens the port? | | | |
| Does a Pico / Pico 2 (MicroPython) reset or misbehave? | | | |
| Does a CircuitPython Pico work (REPL responds)? | | | |
| Does the REPL work (tab completion, paste mode, Ctrl-C/D)? | | | |
| Does reconnecting after the program closes work, including after a port rename? | | | |

## 4. Implementation order
1. **Portability fixes in the core** (section 1) behind small helpers that take the platform as an argument, so Linux tests can
   exercise the Windows branches (`config_dir()`, `cache_dir()`, `free_space()`, `list_drives()`, `windows_safe_name()`).
2. **Windows CI job** (`windows-latest`): the portable tests only (skip the pty / real-MicroPython / GUI ones with a marker).
3. **External terminal** (GUI-free `extterm.py`: command building, per-OS defaults, the disconnect / launch / wait / reconnect
   sequence), the button in `gtkui/console.py`, the two settings and Preferences entries. Tests against the fake board: argument
   building with spaces and quotes, no shell, `COMn` to number, defaults per OS, close-leaves-REPL, reconnect after a port rename,
   program missing (clear message, stay connected), non-zero exit.
4. **Packaging spike:** the MSYS2 + PyInstaller build in CI as an artifact; list what breaks.
5. **Portable zip, signing, README Windows section, hardware tests** (the table above), then fill in `docs/HARDWARE.md`. Signing needs the
   owner's certificate or signing service; an installer is later work.

Rough effort (an estimate, not a measurement): 1 about 1-2 days, 2 half a day, 3 1-2 days, 4 1-3 days, 5 1-2 days without an installer.

## Still open
- The code-signing certificate or service (the owner; we wait for it), and whether signing then runs in CI (needs a secret) or by hand.
- The hardware test table above, and what the PuTTY and Tera Term line states at open actually are (not in their manuals).
