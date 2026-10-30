# Handoff / project memory

Written so a new Claude session can take over with no other context. Update it as you work. Last updated at
`main` = `eafb13f` (2026-10-30); CI green (Python 3.11/3.12/3.13, real MicroPython
v1.26.0 and v1.28.0, and the `gui` job: GTK windows under xvfb).

**The Tk GUI was removed (2026-10-30).** `python -m mpexplorer` is GTK only and prints how to install PyGObject if
it is missing. `AnsiScreen` (the Tk terminal's emulator) moved to `tests/ansiscreen.py` because the terminal-session tests use it; Tk's key
table (`key_bytes`) and `--tk`/`MPEXPLORER_UI` are gone. The Tk-specific lessons further down are historical (git history has the code).

## Pico and Pico 2 (2026-10-30, Pi session)
Both were in BOOTSEL mode, so they were flashed with MicroPython v1.28.0 (`RPI_PICO` and `RPI_PICO2` UF2s from micropython.org) and now
show up as `/dev/ttyACM1` (Pico, RP2040) and `/dev/ttyACM2` (Pico 2, RP2350); the ports can change on re-plug (see `/dev/serial/by-id`).
`os.uname()` and `tools/bench.py` ran on both through the stock raw REPL; tables and notes are in `docs/HARDWARE.md`. Short version:
uploads 30 KB/s (Pico) and 58 KB/s (Pico 2) in raw-paste, which beats plain there (the opposite of the Tildagon); downloads 67 and
101 KB/s; verdict "agent mode not needed". `tools/bench.py` now skips a size that does not fit in the free flash (the Pico filled up
and left a garbled directory entry; its flash was reformatted with `os.VfsLfs2.mkfs`). The owner then used the GTK app on both
Picos by hand (moved `blink.py` to each and renamed it to `main.py`; console, terminal and editor all worked). **Not done:** the 2022
badge is untried.

**Port list and device label (2026-10-30).** The port dropdown shows `/dev/ttyACM2  Pico 2 (RP2350)  #9842` (name, then the last 4 of the
serial; non-USB ports hidden). A Pico and a Pico 2 look the same on USB, so the exact model is learned at connect (`os.uname().machine`,
kept in settings `boards.names`) and shows from the next Scan. The device pane's frame reads `Device: /dev/ttyACM2  Pico 2 (RP2350)`.
Code: `mpexplorer/ports.py` (`port_label`, `short_model`), `remember_board_name`, `Pane._caption`.

**Sync logic tested on a Pico (2026-10-30, by script, not through the dialog).** Folder `~/Code/mpx-test-folder` (12 files, 5 folders,
random binaries, an empty folder, a hidden file, spaces in a name; outside the repo, 392 KB) synced to `/mpx-test` on the RP2040 and back:
identical by SHA-256, 15 s up and 8 s back, and a second plan was empty. Then, on a scratch copy: a changed text file, a same-size changed
binary (caught: the plan compares content), a new file and folder, a removed file and a removed folder. The plan copied only the 4
changed/new files; with mirror-delete off nothing was deleted; with only some deletes ticked only those went (the unticked folder stayed);
the full mirror removed the rest and left the board identical to the source, with no `.mpx-part` leftovers. **Not tested:** the sync
dialog itself (target confirm, mirror-delete confirm, tick boxes, in-dialog progress): that needs the owner at the GTK window.

## Status after the 2026-10-29 Pi session (read this first)
Done from a Claude Code session on the Pi 5 with the Tildagon (MicroPython 1.28.0) on `/dev/ttyACM0`. Everything below is
merged to `main` and CI was green at `9524f1e`. It was done through scripts, the fake board and GUI tests on a private
Xvfb display; it had **no GUI session with the owner**. On 2026-10-30 the owner checked the new GUI items by hand on the real display and
badge and reported them OK (see below); the hardware checklist (START HERE step 4) was still being worked through.

What was done:
- **Benchmark run** (START HERE step 3): verdict SLOW. Tables and the causes are in `docs/HARDWARE.md`; what it means for
  agent mode is in `docs/AGENT_PLAN.md` ("Phase 0 results"). Short version: host -> board can be about 10x faster with an
  agent (a program reading `sys.stdin.buffer` took in about 118 KB/s); board -> host is capped near 11 KB/s by the board
  itself, for text and binary alike, so an agent cannot fix downloads.
- `tools/bench.py`: the 1024 KB copy step skips itself when the board lacks room (it crashed with `OSError: 28`), and the
  soft reset now also runs after an error (it used to leave the badge frozen in raw REPL).
- **Listing**: `os.stat` costs about 40 ms per file on the Tildagon's FAT, so `DeviceBackend.listdir(path, mtimes=False)`
  takes sizes from `ilistdir` and leaves the modified time 0. Preferences > "Show modified times for device files"
  (`general.device_mtimes`, off by default) turns the times back on; the device pane hides its Modified column when off.
  Copy, delete and sync use the cheap listing too.
- **Board clock**: `DeviceBackend.set_clock()` writes the host's **UTC** time to `machine.RTC()` and reads it back (Device >
  "Set board time now"; Preferences > "Set the board's clock (UTC)... when connecting", `general.set_board_clock`, off by
  default). UTC because badge apps hold the RTC in UTC (the owner's GPS clock app disciplines it from GPS/NTP and adds its
  own offset); an earlier version wrote local time and was corrected before merge. Verified on the Tildagon: set, read back,
  kept ticking, survived a soft reset. The board's clock was 2000-01-01 before.
- **Download keeps the board file's time** (`ops.copy_tree` -> `LocalBackend.set_mtime`), unless the board's clock was never
  set (before 2001, `MIN_VALID_MTIME`). Uploads cannot set a time: MicroPython here has no `os.utime`.
- **Send time button** (toolbar, next to Connect): same action as Device > "Set board time now". The toolbar now
  wraps onto a second row when the window is narrow (GTK `FlowBox` of two groups, Port..Send time and Upload..Delete),
  so the window's minimum width is set by the panes (about 995 px in GTK), and at the 1180 px
  default the toolbar shows two rows (one row needs about 1460 px). Checked under Xvfb, then confirmed OK by the owner on the real display (2026-10-30).
- **Restart on disconnect** (Preferences > "Restart the board when disconnecting or quitting", `general.reset_on_disconnect`,
  off by default): connecting interrupts the badge OS (Ctrl-C), and a plain disconnect (Ctrl-B) leaves its screen frozen; with
  the option on, `RawREPL.close(reset=True)` sends Ctrl-B Ctrl-D so the badge restarts. **Confirmed by the owner on the
  Tildagon** (the badge reset after a connect and disconnect). The screen still stays frozen while connected: unavoidable.
  Note: the badge re-enumerated as `/dev/ttyACM1` after a reset; Scan finds it.
- **Toolbar icons**: toolbar, pane bars, editor, console and status bar use theme icons (PiXflat) with
  hover hints that carry the shortcut; `icon_button`/`set_icon` in `gtkui/common.py` fall back to the old text label if the theme
  lacks an icon. Connect swaps its icon with the state. At 1180 px the toolbar now fits on one row. Rendered under Xvfb with
  `gtk-icon-theme-name=PiXflat` (Xvfb has no settings daemon, so set it in the script); confirmed OK by the owner on the real display (2026-10-30).
- **Sync dialog** (`gtkui/dialogs.py` `SyncDialog`, `sync.py`): shows From/To (with Host/Device and the paths), the target's free space
  (`Plan.dst_free`, gathered on the worker; red when the ticked files will not fit), what is ignored (`Plan.exclude`), a live
  summary, and every item with a tick box. Unticked items are not transferred (`apply_plan(..., selected=...)`); folders stay
  consistent (unticking a folder unticks what is in it, ticking a file ticks the new folders it needs, a folder is only deleted
  with everything inside it). Select all / Select none; the button is now "Sync". Before the comparison, `SyncTargetDialog`
  asks From/To and offers "Create a folder named <name> in the target and sync into it" (ticked when a folder is selected in the
  pane, which is then the source instead of the pane's current folder); the folder is created by the plan's first step. With the
  mirror option, pressing Sync asks `confirm_mirror_delete` (count, biggest folders, red warning above 1,000 items, deleted
  permanently not trashed, default Cancel). Above 2,000 deletions the list shows one row per top-level folder instead of one per
  item. Why: on 2026-10-30 a sync of the badge's `/` into `/home/pi` listed 271,479 items to delete (the whole home folder).
  The whole sync is one `SyncDialog` window: comparing (what is being scanned, a pulsing bar, centred at 60% of the width like the main
  window's bar), the review, then the transfer (bar and the `Progress.describe()` line) and a result line with Close. Its jobs are `inline=True` (`jobs.py`), so the main status bar ignores them
  while the badge screen still shows "Comparing"/"Syncing". Cancel stops the running job, then closes or reports. Tests start at the
  review with `SyncDialog(..., plan=plan)` and intercept `dialog.start_transfer`.
  Rendered under Xvfb in all four phases and tested; the owner has not yet tried it with real folders.
- **Status bar text**: a message now goes back to "Ready" `STATUS_HOLD` (5 s, in `gtkui/app.py`) after the last `set_status`; a running job
  refreshes its progress every 100 ms so the text stays while it runs, and `set_status(text, keep=True)` (used for the settings
  warning) is never cleared. Simple jobs (delete, new folder, rename) now end with "Done"/"Cancelled"/"Failed". The status bar's
  Cancel is the word "Cancel" (a plain button), not the stop icon.
- **Delete progress** (`ops.delete_items`, `Progress.items/items_total/items_word`): a delete used to show "Deleting ...   0 B   0 B/s" because it
  has no bytes. It now collects the whole list first (one stat per selected item, not one per file: on the Tildagon a stat is about
  40 ms), then reports "Deleting x   67 of 1204 item(s) deleted (5%)   4 s left". `Progress.known()` says whether there is any total; when
  not, the status bar and the popup pulse instead of showing a fake 0%, and `describe()` shows only what is known.
- **Transfer popup** (`TransferPopup` in `gtkui/dialogs.py`, `App._update_popup`, `POPUP_DELAY` = 1 s, `POPUP_MIN_VISIBLE` = 3 s): a non-modal progress window opens
  when a normal file job (not `quiet`, not `inline`) has run for a second and closes by itself when the job ends, but not before it has been up for 3 seconds (until then it shows "Finished: ..." with Cancel disabled): the job's status as a
  heading, the bar centred at 60%, the current file, then done of total / speed / time left (`Progress.describe(with_label=False)`),
  with Cancel and Hide while the job runs (Hide closes it for that job only); when the job has ended they give way to a single Close that
  dismisses it at once. The status bar keeps updating underneath. Rendered under Xvfb and tested
  (opens after the delay, never for quick/quiet/inline jobs, Hide, Cancel, Close); the owner has not yet seen it with real transfers.
- **Transfer bar** (GTK only): the progress bar sits over the status or transfer line (file, `1.2 MB of 3.0 MB (40%)`, speed,
  time left: `Progress.describe()`/`eta()` in `ops.py`, tested), with Cancel beside the bar, stretched to the height of both; the bar is centred at about 60% of the width
  (34 px here, the same as a toolbar button). Rendered under Xvfb with a simulated job; confirmed OK by the owner with real transfers (2026-10-30).
  (The Tk GUI tests were flaky when run after the GTK ones, about 4 runs in 10; the owner said Tk could go, so it was removed
  instead of chased.)
- **Badge status screen** (`mpexplorer/badgescreen.py`, `docs/TODO.md` 2d): while connected, the Tildagon's frozen screen shows a
  status page (CONNECTED, this computer's name, device folder, free space, current activity, connect time). Drawn through the raw
  REPL with `display.get_ctx()`/`display.end_frame(ctx)` (RAM only), by a `JobRunner.hook` that runs on the worker thread around
  every non-quiet job and by `folder_changed`. Preferences > "Show a status screen on the badge..." (`general.badge_screen`, on
  by default). Verified on the real Tildagon (idle and busy pages, screenshots with `badge-screengrab/grab.py`); confirmed
  OK by the owner during real use (2026-10-30). Boards without the display API are detected on the first draw and skipped.
- **Adaptive upload mode** (`transport.py`): raw-paste's 128-byte window makes it slower than plain sending on the Tildagon,
  so the first big sends are timed in each mode and plain is kept if 1.25x faster (about 1.6x on uploads, measured with
  interleaved runs); a failed plain send reverts to raw-paste; `tools/bench.py` turns it off (`adaptive=False`).

Not yet done (the owner or the next session): START HERE steps 1, 2 and 4 by hand (settings on Wayland, the GTK hardware
checklist; the owner started on it 2026-10-30). The new GUI items (icons, wrapped toolbar, status bar, badge status page, Send time,
the Preferences options, a downloaded file keeping its time) were checked by the owner and are OK. Still open: the
benchmark and `os.uname()` on the 2022 badge (a Pico and a Pico 2 were done, see below); decide on an upload-focused agent (check integrity at
100+ KB/s first); find why the Tildagon's stdout is capped. Built later on 2026-10-30, shaped by the owner's feedback but not yet
confirmed finished by hand: the sync dialog on real folders (target confirm, mirror-delete confirm, in-dialog progress), and the transfer
popup, delete item counts and the status bar going back to "Ready" during real transfers on the badge.

Practical notes from this session:
- The system Python has no pytest or ruff: `python3 -m venv --system-site-packages <scratch>/venv && <scratch>/venv/bin/pip
  install -e '.[dev]'` (no sudo).
- `xvfb-run` is not installed but `Xvfb` is: `Xvfb :97 -screen 0 1800x900x24 &`, check `/tmp/.X11-unix/X97` exists, then
  `env -u WAYLAND_DISPLAY GDK_BACKEND=x11 DISPLAY=:97 python -m pytest tests/test_gui_gtk.py`, then stop Xvfb by its PID.
  **`GDK_BACKEND=x11` is required**: without it GTK falls back to the Wayland socket and the test windows and dialogs appear on the
  owner's real screen (seen 2026-10-30; a stuck modal dialog also hangs the run).
- Every script that talks to the board must soft-reset it in a `finally` (raw REPL freezes the badge screen), and use
  `mark_dirty(); enter_raw()` first if an earlier script died. Never leave test files on the board (use `/_mpx_*` and remove).

## START HERE: hand-over to the Claude running on the Raspberry Pi (2026-10-28)
(Step 3 was done on 2026-10-29: see the status section above. Steps 1, 2 and 4 are still open.)
The earlier work was done in a cloud sandbox with no hardware. You are on the owner's **Pi 5 with a real display and the
boards plugged in**, which is the thing everything below is waiting for. Read `CLAUDE.md`, then do this in order:

1. **Update and run.** `cd` to the clone, `git checkout main && git pull`, `python3 -m mpexplorer`. Keep the clone on
   `main`: the desktop shortcut runs whichever branch is checked out. The first run creates
   `~/.config/mpexplorer/settings.json` (new this week; see Edit > Preferences...).
2. **Check the new settings on real Wayland** (not yet seen on the Pi): resize the window, change sort order and
   "Show hidden files", navigate both panes, quit, start again: do size, sort, hidden and the host folder come back?
   Connect the Tildagon, go into a subfolder, quit and restart: does it reopen in that folder? Open Edit > Preferences...
   (toggle things, try "Reset all settings...", look at the Settings file tab). Record what you see in `docs/HARDWARE.md`.
3. **Run the benchmark** (decides whether agent mode is built): close mpexplorer, then
   `python3 tools/bench.py --port /dev/ttyACM0` for the Tildagon, and again for a Pico and the 2022 badge if available.
   It only touches `/_mpx_bench` on the board, removes it, and soft-resets the board when done (the raw REPL freezes a
   badge's screen). Paste the tables and the "Decision" lines into `docs/HARDWARE.md`. If any decision says SLOW, the
   agent-mode plan (`docs/AGENT_PLAN.md`) is worth building; if not, drop it.
4. **Go through the GTK hardware checklist** in `docs/TODO.md` section 1 (upload, download, rename, delete, editor save,
   console `input()`, terminal mode, Disconnect behaviour, drag and drop, dialogs) and fix what breaks. Get `os.uname()`
   from every board (especially the 2022 TiLDA Mk6: its MicroPython version is unconfirmed) and put it in `docs/HARDWARE.md`.
5. **Then pick the next feature** with the owner: (a) toolbar icons with hover hints (`docs/TODO.md` section 2), or
   (b) Open with... (`docs/OPEN_WITH_PLAN.md`; decided: saves upload automatically with a conflict check, Geany is the first
   external editor; check `which geany`). Settings keys for it already exist (`open_with.*`); the Preferences File types
   tab is added together with it. Agent mode only if step 3 says so.

Things that behave differently on the Pi than in the sandbox:
- **GUI tests open real windows** on your desktop if `WAYLAND_DISPLAY`/`DISPLAY` is set. They use a temporary settings
  file and the fake board, so the real config and boards are safe, but the windows flash on screen. For quiet runs use
  `sudo apt install xvfb` and `xvfb-run -a -s "-screen 0 1800x900x24" python3 -m pytest tests/test_gui_gtk.py`.
  The Pi's own Python 3.11 already sees the system PyGObject (no venv needed there). On a bare Python they skip.
- **Never kill the GUI with `pkill`/SIGKILL while a board is connected** (frozen badge screen; Ctrl-B then Ctrl-D, or the
  reset button, recovers). **Never put DTR low** (Tildagon corrupts long replies). Close the GUI before `tools/bench.py`.
- The sandbox could `apt-get install` and run GTK under xvfb; the Pi has the real thing, so prefer looking at real
  screenshots (`grim`) and the virtual-mouse rig (`tools/vmouse*.py`, below) over guessing.
- Git: work on a short branch off `main`, merge only when the owner says so (`gh pr merge N --rebase` or
  `git push origin <branch>:main`), then check CI with `gh run list/watch/view --repo Corteil/mpexplorer`. There are two
  merged leftovers on the remote (`claude/micropython-file-explorer-gui-7tsgfy`, `wire-settings`): safe to delete once the
  owner agrees. Dependabot PR #2 (`action-gh-release` v3) is held until the first release tag `v0.1.0`.
- Not a priority but open: GUI smoke tests for the dialogs with the mouse,
  saved connection profiles, README screenshots, release tag.

## Earlier notes from the Pi session (2026-10-28)
- `main` has everything: GTK 3 interface is the default (`python -m mpexplorer`); Tk was the `--tk` fallback then (removed 2026-10-30). All feature branches are merged and deleted. Only open PR: Dependabot #2 (`softprops/action-gh-release`
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
  (`tk_click_probe.py`, the Tk click probe, was removed with the Tk GUI.)
- Things Claude tripped on this session, avoid them: `pkill -f` / `pgrep -f` with a pattern that also appears in the
  shell command kills or matches the shell itself (scan `/proc` or use `[x]pattern`); a hard `git reset` and a
  `gh pr merge` were each blocked once by the permission system and worked after the owner repeated the request; the
  virtual mouse clicks whatever is on screen, including the terminal Claude runs in (at x >= 960 on this layout), so
  close real GUIs and keep dialogs away from the terminal; never leave the rig running.
- Settings are wired into both GUIs with an Edit > Preferences... page (merged 2026-10-28); see "Settings" in
  `docs/DESIGN.md`. GUI tests and a `gui` CI job exist. Still untested on the Pi: position restore on Wayland.
- Plans written 2026-10-28 (not started): `docs/AGENT_PLAN.md` (agent mode, with a measure-first decision gate) and
  `docs/OPEN_WITH_PLAN.md` (choose the program that opens a file, edit device files in external programs).
- The ordered to-do list is `docs/TODO.md` (first item: GTK on real hardware; second: replace toolbar buttons with
  icons that have hover hints).
- Possible next features, none started: saved connection profiles, a release tag `v0.1.0` (exercises `release.yml`,
  then merge Dependabot #2), screenshots in the README, GUI smoke tests in CI (needs a display: weston or xvfb).

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
- `Corteil/mpexplorer` (this repo). `main` is protected only by convention. Leftover merged branches on the remote:
  `claude/micropython-file-explorer-gui-7tsgfy`, `wire-settings` (safe to delete). The other two repos in the original session are unrelated here.
- CI: `.github/workflows/ci.yml` (`test` matrix 3.11-3.13; `micropython` matrix v1.26.0, v1.28.0 builds the unix port,
  cached), `release.yml` (wheel on `v*` tags, never exercised), Dependabot is on (it opened PRs to bump actions).

## What is built (all in main)
`mpexplorer/settings.py` (validated JSON settings, atomic saves) + `remember.py` + Edit > Preferences... in both GUIs;
`tools/bench.py` (transfer benchmark for the agent-mode decision); GUI tests (`tests/test_gui_*.py`) and a `gui` CI job.
Earlier:
File explorer core + GUI, upload/download/copy/cut/paste/rename/delete, conflicts, progress/cancel, editor, run script,
raw-paste with fallback, REPL console (emulated: `_`, Ctrl-D reset, `input()`, interrupt, activity trace), folder sync
(size + on-device SHA-256, preview, optional mirror delete, atomic writes), terminal mode (real REPL: keystrokes pass
through, `AnsiScreen`, key/paste mapping, device pane paused while active), three test layers (see DESIGN.md).

## GTK interface (default from 2026-10-28)
`python -m mpexplorer` now opens the GTK 3 interface (`mpexplorer/gtkui/`, PyGObject from `python3-gi`; GTK 3.24 is
what Raspberry Pi OS Bookworm ships: no GTK 4, libadwaita or GtkSourceView). It was written because the Tk GUI kept
hitting labwc/XWayland quirks (see lessons: clicks falling through new windows, dialogs ignoring the mouse, taskbar
icon matching, minimise/restore). GTK talks Wayland natively. (The Tk GUI that was the `--tk` fallback was removed on 2026-10-30.) Port discovery
moved to `mpexplorer/ports.py`. Feature parity: panes (sort, filter, hidden, drag between panes, context menu), transfers,
copy/cut/paste, rename/delete/new, properties, free space, soft reset, conflict prompt, editor (unsaved-changes info
bar inside the window), run script output, folder sync, REPL console (emulated) and Terminal mode (VTE widget,
`gir1.2-vte-2.91`, optional), Window menu.

GTK notes: import GTK only through `gtkui/common.py` (it pins Gtk/Gdk/GdkPixbuf versions before import, otherwise
PyGIWarning). The window app id is `mpexplorer` (`GLib.set_prgname`), so the taskbar matches
`~/.local/share/applications/mpexplorer.desktop` and its `Icon=mpexplorer`; no `StartupWMClass` is needed. VTE here is
0.70: it has no `set_enable_bracketed_paste` (added in 0.76; MicroPython never asks for bracketed paste anyway) and
`get_text()` is deprecated. Install with `pipx install --system-site-packages` or pipx cannot see PyGObject. Wayland
clients do not know their own screen position, so the virtual-mouse rig (below, lessons) cannot aim at a GTK window
with `winfo_rootx()`-style calls: find the window by differencing `grim` screenshots instead.

## Verified vs not verified
- Verified in tests (about 130 tests, all in CI): core logic against the fake board; the real MicroPython unix port
  v1.26.0 / v1.28.0 (16 tests, through `rawrepl_shim.py` for file/console/sync and the unix REPL for terminal tests, and
  `tools/bench.py` end to end); the settings module (23 tests); and the GTK windows' settings wiring and Preferences
  dialogs (GUI tests under xvfb, the `gui` CI job). The GUI tests do not cover transfers, the editor or the console on
  real boards, and no test touches real hardware or the real raw-REPL framing.
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
- Added 2026-10-29: the Tildagon's clock set and read back (UTC); an upload through the adaptive path (128 KB, SHA-256
  checked on the board); the fast listing compared with the full one on `/` and `/apps` (same names, kinds, sizes); the
  benchmark; 21 GUI tests under Xvfb including the Modified column and the clock setting. The owner then confirmed the new GUI
  controls and a downloaded file's time on the Tildagon (2026-10-30). NOT verified: the clock setting, the status screen and
  the benchmark on a Pico or the 2022 badge.
- NOT verified (mostly): running code in the REPL console, terminal mode, upload/download, saving from the editor, run script, sync and soft reset on a real
  board; whether raw-paste is negotiated on the badge (not traced); Pico / RP2 and the 2022 badge (the DTR change
  below could behave differently there); transfer speed; whether a clean Disconnect restores the badge's own app
  (see lessons). Add rows to `docs/HARDWARE.md` once tested.

## Open items / suggested next steps
0. GTK follow-ups: run the GTK GUI against the Tildagon and a Pico (connect, list, transfer, editor, console,
   terminal); test clicks and dialogs with the virtual mouse (locate windows via screenshot diffs); add GUI smoke tests (GTK needs a display: weston/xvfb in CI).
1. Keep running it on the Pi 5 (`pip install -e .`; `python -m mpexplorer`) with a
   Pico and a badge and fix breakage. Done so far: connect/list on a Tildagon, toolbar, console opens focused. Still to try:
   running code in the console, terminal mode, transfers, drag between panes, editor, sync, a Pico, the 2022 badge. Consider GUI smoke
   tests (build `App` against the fake board; CI would need `xvfb`).
2. Get `os.uname()` from the 2022 badge; if old, add an older-toolchain CI leg (container `ubuntu:22.04`; GCC 15 breaks
   releases <= ~1.22: they abort with "stack smashing detected" on the first exception).
3. Transfer speed is measured on the Tildagon (done, see HARDWARE.md); still to do on a Pico and the 2022 badge; tune `CHUNK`
   (2 KB) / `WRITE_BLOCK` if wanted.
4. Agent mode (opt-in, RAM-only): the gate said yes, but only uploads can gain much (see `docs/AGENT_PLAN.md`, "Phase 0
   results"). Needs the owner's decision.
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
**Historical: the Tk-specific lessons below are kept for the labwc/XWayland/uinput insights; the Tk GUI is gone.**
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
- **Raw-paste window is 128 bytes on the Tildagon** (it stops and waits for the device every 128 bytes, about 8.7 ms per window
  over USB, about 15 KB/s of code text); plain sending (256-byte blocks, 5 ms apart) is about 35 KB/s. Hence the adaptive mode.
- **Board output to the host is capped near 10.7 KB/s** whatever is printed or how (`print`, `sys.stdout.write`,
  `sys.stdout.buffer.write`, any chunk size), also with a bare blocking pyserial read. Not our code; cause unknown.
  Host -> board has no such cap: `sys.stdin.buffer.read` with no pauses took in about 118 KB/s.
- **`os.stat` is about 40 ms per file on the Tildagon's FAT**; `os.ilistdir` is 8-18 ms per entry and includes the size (mount
  points such as `/sdcard` come back as 3-item tuples without one, so those still fall back to `stat`).
- **Badge RTC is UTC by convention** and holds time across a soft reset, but not power loss (no battery): it reads 2000-01-01
  until something sets it (GPS, NTP, or `set_clock`). 0 is used as "time not fetched" in listings; do not run it through
  a time zone conversion (London was UTC+1 all of 1970, which turned 0 into -3600).
- **A hook on the job runner must not keep the window alive**: the worker thread never exits, so `runner.hook = self._badge_hook`
  kept every old app (and its Tk interpreter) alive and made the Tk GUI tests flaky (about 1 run in 3, plus "main thread is not
  in main loop" warnings). The hook now holds the app through a `weakref`; six clean runs after that.
- The default benchmark size of 1024 KB cannot run its on-board copy step on a Tildagon (about 2 MB free, needs 2x).
- Run GUI tests under Xvfb on the Pi (see the status section), not on the real display.
- No persistent memory store exists outside the repo, so this file and CLAUDE.md are the memory. Keep them current.
