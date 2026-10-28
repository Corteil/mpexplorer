# Handoff / project memory

Written so a new Claude session can take over with no other context. Update it as you work. Last updated at
`main` = `ae7a1ef` (2026-10-28, end of day); CI green (Python 3.11/3.12/3.13 + real MicroPython v1.26.0 and v1.28.0).

## Where we stopped (2026-10-28) - read this first
- `main` has everything: GTK 3 interface is the default (`python -m mpexplorer`), Tk is `--tk` and the automatic
  fallback. All feature branches are merged and deleted. Only open PR: Dependabot #2 (`softprops/action-gh-release`
  v2 -> v3), deliberately held until the first release tag.
- The owner has tried the GTK build on the Pi 5 with the Tildagon plugged in ("looking good"). Not yet done on
  hardware: the full flow (connect, transfer, edit, console, terminal) in GTK, a Pico, the 2022 badge. First thing to do
  tomorrow: run `python -m mpexplorer`, connect the badge, go through that list, fix what breaks, update HARDWARE.md.
- Outside the repo, on the owner's Pi (so a fresh machine will not have them): `sudo apt install gir1.2-vte-2.91`
  (done), `~/.local/share/applications/mpexplorer.desktop` + `~/.local/share/icons/hicolor/256x256/apps/mpexplorer.png`
  (the taskbar icon), `~/Desktop/mpexplorer.desktop` (runs `python3 -m mpexplorer` from this clone), `quick_exec=1` in
  `~/.config/libfm/libfm.conf` (no "Execute?" prompt). The clone is used directly (no pip install), so the shortcut
  runs whichever branch is checked out: keep it on `main`.
- Test rig (committed in `tools/`, excluded from ruff): `vmouse.py` + `vmouse_ctl.py` create a virtual mouse and
  keyboard through `/dev/uinput` so a session can click and type. Needs `sudo` (passwordless here) and
  `pip install evdev` in a scratch venv. `python3 tools/vmouse_ctl.py start <venv-python> /tmp/vm.fifo`, send lines like
  `click 400 300` to the FIFO, `... stop` when done (and always stop it: it keeps a root process and two input devices).
  `gtk_smoke.py` / `gtk_term_smoke.py` run the GTK app against the fake board in-process (need a display);
  `tk_click_probe.py` is the click-through probe used on the Tk GUI.
- Things Claude tripped on this session, avoid them: `pkill -f` / `pgrep -f` with a pattern that also appears in the
  shell command kills or matches the shell itself (scan `/proc` or use `[x]pattern`); a hard `git reset` and a
  `gh pr merge` were each blocked once by the permission system and worked after the owner repeated the request; the
  virtual mouse clicks whatever is on screen, including the terminal Claude runs in (at x >= 960 on this layout), so
  close real GUIs and keep dialogs away from the terminal; never leave the rig running.
- The ordered to-do list is `docs/TODO.md` (first item: GTK on real hardware; second: replace toolbar buttons with
  icons that have hover hints).
- Possible next features, none started: saved connection profiles, a release tag `v0.1.0` (exercises `release.yml`,
  then merge Dependabot #2), screenshots in the README, GUI smoke tests in CI (needs a display: weston or xvfb),
  deleting the Tk GUI once GTK is proven.

## The user and how they work
- Owner: Corteil (GitHub), builds EMF Camp badge hardware (see their repos `rp2350-hdmi-hexpansion`,
  `tildagon-hdmi-manager`, which are separate and were not changed). Prefers Python / MicroPython / CircuitPython.
- Workflow pattern: asks for a feature -> you build and push to the feature branch -> they say "merge it into main"
  -> then "check if the CI passed". Keep replies short and honest about what was and was not verified.
- The original session had no Raspberry Pi or hardware. Since 2026-10-27 the GUI has been run on the owner's
  Raspberry Pi 5 (Raspberry Pi OS, Python 3.11.2, Tk 8.6, Wayland/XWayland) with a real Tildagon attached, driving it
  from a Claude Code session on that Pi (screenshots with `grim`). Small PRs from `fix-*` branches off `main`, merged
  with `gh pr merge --rebase` (linear history), is the pattern that session used.

## Original request (paraphrased)
"Design/plan a file explorer for MicroPython with a GUI for the Raspberry Pi. Targets are Raspberry Pi (Picos) and the
EMF badges. Upload/download files, copy/paste, all standard explorer features." Clarified later:
- GUI and all code run on a **Raspberry Pi 5**; targets are Picos and the EMF badges (2022, 2024, 2026).
- **No code installed on targets** (an opt-in RAM-only agent mode may be planned, never flash-resident).
- 2026 badge = 2024 badge with a new front plate, same firmware repo.
- Console wanted two modes: emulated (raw REPL) and real REPL terminal; emulated one has a "show all activity" switch.

## Hardware / firmware facts (verified from the firmware repos' web pages)
- 2024 + 2026 badge: `emfcamp/badge-2024-software`, ESP32-S3, ESP-IDF 5.5.1, MicroPython submodule pinned at `e0e9fbb`
  = upstream "Bump version to 1.28.0" -> **MicroPython 1.28.0**.
- 2022 badge: `emfcamp/TiDAL-Firmware`, TiLDA Mk6, **ESP32-S3** (earlier assumption of RP2040 was wrong), ESP-IDF 4.4.1,
  MicroPython submodule at upstream commit `afceb56` (a 2022 master snapshot, not a release; version unconfirmed,
  probably ~1.19.x) plus a `micropython.diff` patch.
- Picos are RP2040 / RP2350 (RP2 time epoch is 2000; handled by probing `time.gmtime(0)`).
- To learn a board's version: `import os; print(os.uname())`.

## Repo and branches
- `Corteil/mpexplorer` (this repo). `main` is protected only by convention. Feature branch:
  `claude/micropython-file-explorer-gui-7tsgfy`. The other two repos in the original session are unrelated here.
- CI: `.github/workflows/ci.yml` (`test` matrix 3.11-3.13; `micropython` matrix v1.26.0, v1.28.0 builds the unix port,
  cached), `release.yml` (wheel on `v*` tags, never exercised), Dependabot is on (it opened PRs to bump actions).

## What is built (all in main)
File explorer core + GUI, upload/download/copy/cut/paste/rename/delete, conflicts, progress/cancel, editor, run script,
raw-paste with fallback, REPL console (emulated: `_`, Ctrl-D reset, `input()`, interrupt, activity trace), folder sync
(size + on-device SHA-256, preview, optional mirror delete, atomic writes), terminal mode (real REPL: keystrokes pass
through, `AnsiScreen`, key/paste mapping, device pane paused while active), three test layers (see DESIGN.md).

## GTK interface (default from 2026-10-28)
`python -m mpexplorer` now opens the GTK 3 interface (`mpexplorer/gtkui/`, PyGObject from `python3-gi`; GTK 3.24 is
what Raspberry Pi OS Bookworm ships: no GTK 4, libadwaita or GtkSourceView). It was written because the Tk GUI kept
hitting labwc/XWayland quirks (see lessons: clicks falling through new windows, dialogs ignoring the mouse, taskbar
icon matching, minimise/restore). GTK talks Wayland natively. `--tk` / `MPEXPLORER_UI=tk` runs the Tk GUI, which is
also the automatic fallback when PyGObject is missing. Both GUIs use the same core modules unchanged; port discovery
moved to `mpexplorer/ports.py`. Feature parity: panes (sort, filter, hidden, drag between panes, context menu), transfers,
copy/cut/paste, rename/delete/new, properties, free space, soft reset, conflict prompt, editor (unsaved-changes info
bar inside the window), run script output, folder sync, REPL console (emulated) and Terminal mode (VTE widget,
`gir1.2-vte-2.91`, optional; Tk's hand-drawn `AnsiScreen` is not used by GTK), Window menu. The Tk GUI is kept, not
deleted; remove it once GTK has had enough hardware time.

GTK notes: import GTK only through `gtkui/common.py` (it pins Gtk/Gdk/GdkPixbuf versions before import, otherwise
PyGIWarning). The window app id is `mpexplorer` (`GLib.set_prgname`), so the taskbar matches
`~/.local/share/applications/mpexplorer.desktop` and its `Icon=mpexplorer`; no `StartupWMClass` is needed. VTE here is
0.70: it has no `set_enable_bracketed_paste` (added in 0.76; MicroPython never asks for bracketed paste anyway) and
`get_text()` is deprecated. Install with `pipx install --system-site-packages` or pipx cannot see PyGObject. Wayland
clients do not know their own screen position, so the virtual-mouse rig (below, lessons) cannot aim at a GTK window
with `winfo_rootx()`-style calls: find the window by differencing `grim` screenshots instead.

## Verified vs not verified
- Verified in tests: core logic against the fake board (77 tests) and against real MicroPython unix port
  v1.26.0 / v1.28.0 (16 tests, through `rawrepl_shim.py` for file/console/sync and the unix REPL for terminal tests).
  There are **no automated GUI tests**.
- Verified by hand on a Tildagon (Pi 5, 2026-10-27): the GUI launches and renders; Scan, Connect and Disconnect work
  (the button toggles); the device pane lists `/`; the toolbar fits the window; the REPL console opens with a visible
  input box that has keyboard focus (confirmed by the owner after the fix; it used to be hidden and unfocused); the
  text editor opens focused and clicks stay in it; double-clicking a selected device file no longer downloads a copy.
  The editor and drop fixes were seen working, but a save from the editor was not traced.
  What was typed into the console and what came back was not recorded, so running code through it is not confirmed.
- GTK interface, verified by tests against the fake board (in-process probes, not in CI): list both panes, upload,
  open a device file in the editor, save back, run code in the emulated console, switch to Terminal mode (VTE shows the
  board's banner and prompt, `print(6*7)` returns `42`), switch back and the device pane un-pauses. Seen on the Pi 5
  with a Tildagon plugged in: the window renders with its taskbar icon and the owner said it "looks good" (2026-10-28).
  NOT verified for GTK: clicks with the virtual mouse, real typing into the terminal, anything on the actual badge
  beyond the above, drag between panes, the conflict/sync dialogs with the mouse, the Window menu.
- NOT verified (Tk, and mostly GTK too): running code in the REPL console, terminal mode, upload/download, saving from the editor, run script, sync and soft reset on a real
  board; whether raw-paste is negotiated on the badge (not traced); Pico / RP2 and the 2022 badge (the DTR change
  below could behave differently there); transfer speed; whether a clean Disconnect restores the badge's own app
  (see lessons). Add rows to `docs/HARDWARE.md` once tested.

## Open items / suggested next steps
0. GTK follow-ups: run the GTK GUI against the Tildagon and a Pico (connect, list, transfer, editor, console,
   terminal); test clicks and dialogs with the virtual mouse (locate windows via screenshot diffs); decide when to
   delete the Tk GUI; add GUI smoke tests (GTK needs a display: weston/xvfb in CI).
1. Keep running it on the Pi 5 (`sudo apt install python3-tk`; `pip install -e .`; `python -m mpexplorer`) with a
   Pico and a badge and fix breakage. Done so far: connect/list on a Tildagon, toolbar, console opens focused. Still to try:
   running code in the console, terminal mode, transfers, drag between panes, editor, sync, a Pico, the 2022 badge. Consider GUI smoke
   tests (build `App` against the fake board; CI would need `xvfb`).
2. Get `os.uname()` from the 2022 badge; if old, add an older-toolchain CI leg (container `ubuntu:22.04`; GCC 15 breaks
   releases <= ~1.22: they abort with "stack smashing detected" on the first exception).
3. Measure transfer speed with raw-paste on/off on hardware; tune `CHUNK` (2 KB) / `WRITE_BLOCK`.
4. Agent mode (opt-in, RAM-only; design in DESIGN.md) only if measured speed demands it.
5. Nice-to-haves: saved connection profiles, release tag `v0.1.0` (exercise `release.yml`), merge/handle Dependabot PRs,
   screenshots in README, hardware matrix rows.

## Lessons learned (save yourself the time)
- The unix MicroPython port **ignores Ctrl-A** (no raw REPL). `tests/rawrepl_shim.py` supplies the framing. The pty
  helper must wait for the shim's `READY` before writing, or an early Ctrl-C (SIGINT) kills the process.
- Real MicroPython's `input()` echoes what it reads: the console must not echo it locally (it did once, fixed).
- The unix REPL has no tab completion; terminal completion is only tested on the fake board.
- Reading must never swallow bytes after the delimiter (`RawREPL._rx` buffer) - an early bug.
- Raw-REPL exec has no `OK` in raw-paste mode; the device acks the end of input with 0x04 first.
- `exec(timeout=None)` waits forever for the program's stdout but handshake steps keep a 10 s limit.
- `pytest-timeout` (60 s) is used in the real-firmware CI job so a hang fails loudly with a traceback.
- Tests that wait on the fake board must wait for a specific prompt/suffix; matching an earlier `"\n>>> "` races.
- `git checkout <file>` discards uncommitted edits to it. Commit first.
- Web access: only the repos in the session scope are readable through GitHub tools; for other public repos use
  WebFetch on the github.com pages (the API host returned 403).
- **DTR must be asserted on the Tildagon.** `RawREPL.open()` used to set DTR and RTS low; with DTR low the badge's USB
  CDC returns scrambled output for any reply over ~256 bytes (starts mid-string, tail repeated), so listing `/` failed
  with a JSON "Extra data" error. Tested with plain pyserial: DTR on (RTS on or off) is intact, DTR off is corrupt.
  Now `dtr = True`, `rts = False` (RTS is the line that can reset ESP32-S3 boards). Not tried on Pico / 2022 badge.
- **Raw REPL freezes the badge UI.** Connecting sends Ctrl-C to interrupt the running app; the badge screen stays
  frozen while in raw REPL. Killing the GUI (`pkill`) leaves it frozen: send Ctrl-B then Ctrl-D (soft reset) on the
  port, or press the badge's reset button. Disconnect only leaves raw REPL and does not soft-reset, so the app may not
  come back; unconfirmed.
- Tk on this Pi: the clam theme forces a 104 px minimum width on every `TButton`, which made the toolbar 1457 px wide
  in a 1180 px window (right-hand buttons clipped). `style.configure("TButton", width=0, ...)` fixes it and the
  window has a `minsize` equal to the toolbar's requested width.
- Tk packing: a `tk.Text` requests 24 lines by default and squeezed the console's input box to 1x1 px (not viewable,
  so it can never take focus). Give the output Text a small `height` and let `expand` fill. A new `Toplevel` also
  starts with focus on the window itself, not a child widget, so call `lift()` and `inp.focus_force()` after it maps.
  Debug with `root.tk.call("focus")`, `winfo_viewable()` and `winfo_width()/height()`.
  The same focus fix is on the `Editor`. Other pop-ups (conflict and sync dialogs) have not been checked for it.
  `ConflictDialog`, `SyncDialog` and the script Output window are `transient` to the main window.
- Editor and console are NOT transient: they are `AppWindow`s (`widgets.py`) so they get their own taskbar entry and
  can be minimised on their own. On labwc/XWayland a window mapped once sends the pointer's clicks to the window
  underneath (editor looked focused, main app reacted). Found with a virtual mouse (below): `lift`, `-topmost`,
  `lower/lift`, `_NET_ACTIVE_WINDOW`, iconify, a 1 px move and a single late map all fail; mapping, unmapping and
  mapping again with `update_idletasks()` between (about 1 ms unmapped, no visible flash) works. `transient(app)`
  also works but gives no taskbar entry and cannot be minimised separately.
- A modal `messagebox` opened from the editor ignored mouse clicks (keyboard worked). Cause not isolated (a dialog
  parented to the editor took clicks in one test, one parented to the main window did not). The editor now confirms
  unsaved changes with a bar inside its own window and shows save errors in its status text. The other dialogs
  (conflict, sync, message boxes on the main window) have not been tested with the mouse.
- Window menu (`App._fill_window_menu`): lists the open editors/console, Minimise all, Restore all. Tk's
  `deiconify()` does nothing for a minimised window under labwc and `state()`/`winfo_viewable()` can't tell it is
  minimised; `gui/wm.py` sends `_NET_ACTIVE_WINDOW` (ctypes, libX11) to Tk's wrapper window (the parent of
  `winfo_id()`, which is what the window manager manages). Minimising one sub window with its title-bar button hid
  the others for the owner once; not reproduced, cause unknown.
- Taskbar icon (wf-panel-pi on labwc): the panel matches a window to a launcher by its `WM_CLASS` *instance* name
  (first field), not by `_NET_WM_ICON`/`iconphoto`. `App` sets `baseName="mpexplorer"`, `className="Mpexplorer"`; the
  launcher is `~/.local/share/applications/mpexplorer.desktop` (`Icon=mpexplorer`, `StartupWMClass=Mpexplorer`) plus
  `~/.local/share/icons/hicolor/256x256/apps/mpexplorer.png`. Those files live outside the repo. Tk names a window's
  instance after its widget (`!consolewindow`) and sibling names must be unique, so `AppWindow` names itself
  `mpexplorer` under a throwaway parent Frame. Transient pop-ups get no taskbar entry. The desktop icon needs
  `quick_exec=1` in `~/.config/libfm/libfm.conf` or pcmanfm asks "Execute?" on every double-click.
- Testing clicks: Claude cannot click, but `/dev/uinput` works with `sudo` (`pip install evdev` in a scratch venv;
  a root daemon creates a virtual absolute mouse and keyboard, fed through a FIFO). Pitfalls: close every real GUI
  first (the rig clicks whatever is on screen); labwc centres dialogs on the screen, so their buttons can sit under
  the terminal at x >= 960; the first click after a pointer jump is sometimes swallowed; never `pkill -f` the daemon.
  Stop the daemon when done.
- Tk widget paths are not containment: `'.!pane2'.startswith('.!pane')` is True, so `Pane._release` treated a
  double-click on a selected row as a drop on the other pane and downloaded a copy to the Host folder. Use
  `_inside(widget, ancestor)` (walks `.master`).
- Shell gotcha: `pkill -f 'python3 -m mpexplorer'` also matches the shell command that contains that text and kills
  it (exit 144). Use `pgrep -fa '[p]ython3 -m mpexplorer'` or kill by pid. `gh pr edit` can fail on a GraphQL
  "Projects (classic)" warning; use `gh api -X PATCH repos/Corteil/mpexplorer/pulls/N` instead.
- No persistent memory store exists outside the repo, so this file and CLAUDE.md are the memory. Keep them current.
