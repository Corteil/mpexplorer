# Design

## Goals
- Run on a Raspberry Pi 5; targets are Pico-class boards (RP2040/RP2350) and the EMF Camp badges:
  2022 (TiLDA Mk6, ESP32-S3), 2024 Tildagon and 2026 (the same hardware and firmware as 2024, new front plate; ESP32-S3). The raw REPL is chip-independent, so all
  of them use the same code path.

## Per-board notes
- **ESP32-S3 (Tildagon):** opening the port with DTR/RTS asserted can reset the board, so the port is opened with both low.
  The badge OS app keeps running; Ctrl-C x2 on connect interrupts it (the board returns to its app after a reset).
  Time epoch is 2000 as on RP2; detected at connect from `time.gmtime(0)`.
- **2026 badge:** same board and firmware repo as the 2024 Tildagon, so it is treated identically.
- Standard explorer features: browse, upload/download, copy/cut/paste, rename, delete, edit.
- **Default: no code is installed on the target.**

## Layers
```
GUI (Tkinter) -> JobRunner (one worker thread) -> Backend
                                                  |- LocalBackend   host disk
                                                  |- DeviceBackend  raw-REPL snippets   (default)
                                                  |- AgentBackend   RAM-resident helper (opt-in, planned)
```
- `transport.py` - raw REPL framing (Ctrl-A, code, Ctrl-D, `OK` stdout `\x04` stderr `\x04`).
- `backends.py` - same small interface for every backend, so both panes and all operations are backend-agnostic.
- `ops.py` - copy / move / delete trees, conflict policy, progress and cancel. Partial files are removed on failure.
- `jobs.py` - all serial access happens on one thread; the GUI never blocks and never interleaves commands.

## Default mode: raw REPL
Each operation is a short Python snippet run on the board. File data travels as base64 in 2 KB chunks. Copies within
the device run entirely on the board. Expected speed is tens of KB/s.

## Raw-paste mode (implemented, still no installed code)
MicroPython >= 1.14 supports raw-paste (`Ctrl-E A Ctrl-A`), a flow-controlled way to send snippets: the device
reports a window size and says (`\x01`) when it can take more, so the host no longer pauses after every 256 bytes.
`RawREPL` probes on the first command and remembers the result:

| Device reply | Meaning | Action |
|---|---|---|
| `R\x01` + window | supported | upload with flow control |
| `R\x00` | understood but disabled | plain raw REPL |
| `ra...` (banner) | old firmware | consume banner, plain raw REPL |

`RawREPL(ser, paste=False)` turns it off. The fake board in `tests/` supports all three behaviours and checks the
host never exceeds the window. Real speed gains still need measuring on hardware.

## Planned: agent mode (opt-in)
**What:** a ~3 KB helper (`mpexplorer/agent_src.py`) that speaks a binary, CRC-checked protocol, for fast transfers and
folder listings.

**It still leaves nothing on the board.** The host sends the helper's source over the raw REPL and `exec`s it, so it
lives only in RAM. It is gone after a reset or when the host sends the quit command. The user turns it on per
connection (View > "Fast mode (RAM agent)"); the default stays pure raw REPL. Writing the agent to flash (so it
survives resets) is deliberately **not** planned.

**Protocol (host -> device frames):** `[cmd:1][len:4 LE][payload][crc32:4]`; replies `[status:1][len:4][payload][crc32:4]`.

| cmd | meaning | payload |
|---|---|---|
| `L` | list dir | path -> packed entries |
| `S` | stat | path |
| `R` | read file | path, offset, count -> raw bytes |
| `W` | write file | path, offset, bytes (`T` flag = truncate first) |
| `C` | copy file on device | src, dst |
| `M` / `D` / `K` | rename / delete / mkdir | paths |
| `H` | hash | path -> sha256 (for sync/verify) |
| `Q` | quit | returns the board to the raw REPL |

**Host side:** `AgentBackend` implements the same interface as `DeviceBackend`, so the GUI and `ops.py` do not change.
Binary reads/writes use 4-8 KB frames, giving an estimated 5-10x speed-up and one round trip per folder listing.

**Risks to check on hardware:** USB CDC buffering on RP2040 vs RP2350 vs the badge firmware; free RAM (agent must fit
in a few KB on a 264 KB device); a running `main.py` must be interrupted first; resync after a lost frame (the CRC
and a `Ctrl-C` x2 recovery sequence).

## REPL console: two modes
The console window has a mode selector. **Emulated** is built; **Terminal** is planned.

### Emulated mode (built)
`console.py` (GUI-free, tested) runs each entered line through the same raw REPL used for file operations, so no
second connection or mode switch is needed. The raw REPL keeps its globals between executions, which gives the console
its persistent state.
- Expressions are detected on the host (`compile(..., "eval")`), evaluated on the board, echoed, and stored in `_`.
  Statements and multi-line blocks run as typed.
- Output is streamed while the program runs (`exec(on_output=...)`, no timeout). Esc sends Ctrl-C from the GUI thread.
- `input()`: stdin is the same serial line, so while a command runs Enter writes the line to the board
  (`RawREPL.send_input`, control characters stripped). Because the host cannot tell if the program is actually waiting,
  anything left unread is discarded afterwards by re-entering raw mode (`mark_dirty`), which cancels pending input.
- Ctrl+D on an empty prompt soft-resets (variables are lost), like the real REPL.
- **Show activity** sets `RawREPL.trace`, a callback fed with `send` / `recv` / `error` / `info` events for every
  exchange with the board, including file listings and transfers (long lines are truncated in the window).
- Limits: no tab completion, auto-indent or line editing on the board; helper names (`os json gc b _f _v`) share the
  namespace; a long-running command blocks other device operations because there is one worker thread.

### Terminal mode (built): the board's real REPL
Keystrokes are passed straight through, so the board's own tab completion, history, auto-indent, paste mode (Ctrl-E),
Ctrl-D and `input()` all work, with no emulation.

- **Connection ownership.** The raw REPL (files, sync, emulated console) and the normal REPL cannot share the line.
  `RawREPL.begin_terminal()` sends Ctrl-B (or CR if not in raw mode) and sets `terminal = True`; from then on
  `exec()` raises `TransportError` instead of corrupting the session. `end_terminal()` clears the flag and marks raw
  mode as not entered, so the next operation re-enters it with Ctrl-C/Ctrl-A. Neither switch resets the interpreter, so
  variables survive both ways (tested). The app pauses the device pane while the terminal is active
  (`Pane.set_paused`, `App.set_device_paused`); entering terminal mode is refused while a job is running.
- **`TerminalSession`** (`terminal.py`): a reader thread hands received bytes to a callback; `send()` writes keystrokes;
  a dead port is reported through `on_error`.
- **`AnsiScreen`** (`screen.py`): applies CR, LF, backspace, tab and the CSI sequences the REPL's line editor emits
  (erase line/screen, cursor left/right/up/down, delete/insert/erase characters, column). Colours, window titles and
  private modes are accepted and ignored. Lines are unbounded logical lines with a 1000-line scrollback; absolute row
  addressing is not supported (the REPL never needs it).
- **Keys** (`key_bytes`): Enter -> CR, Backspace -> 0x7f, arrows/Home/End/Delete -> CSI sequences, Ctrl+letter ->
  control byte (so Ctrl-C always interrupts, as in a real terminal). **Paste** (`paste_bytes`): one line is typed;
  several lines are wrapped in paste mode so indentation survives.
- **Tk widget** (`gui/terminal_widget.py`): a read-only `Text` that captures keys, redraws from the screen every 30 ms
  when something changed, and draws the cursor as a highlighted cell.
- **Testing.** `FakeSerial` implements a friendly REPL: prompt, echo, backspace, mid-line edits, history, tab
  completion, `... ` continuation, paste mode, Ctrl-C/D/B, `input()` and the raw-mode switches. Tests drive a
  `TerminalSession` into an `AnsiScreen` and assert on what a user would see.
- **Known limits / to verify on hardware:** key mapping on different desktops; a program that never returns holds the
  line until Ctrl-C; USB re-enumeration after a hard reset ends the session (shown as "connection lost"); firmware
  differences in escape sequences; the fake cannot interrupt a running program with Ctrl-C.

## Testing against real MicroPython
Three layers, each catching different mistakes:
1. **Fake board** (`tests/fakedevice.py`): fast, deterministic, runs everywhere. Models the raw REPL, raw-paste, the
   friendly REPL and `input()`.
2. **Real MicroPython, unix port** (`tests/test_real_micropython.py`, CI job `micropython`): a real interpreter on a pty
   (`tests/ptyserial.py`, a serial-port look-alike whose tty turns Ctrl-C into SIGINT as a keyboard would).
   - The unix port ignores Ctrl-A, so there is no raw REPL to talk to. File, console and sync tests therefore run
     real MicroPython behind `tests/rawrepl_shim.py`, which provides the raw-REPL framing (and answers "not supported"
     to raw-paste). This verifies everything that executes *inside* the snippets: `os.ilistdir/stat/rename`, file
     I/O, `readinto` + `memoryview`, `binascii`, `hashlib`, `json`, tracebacks, `input()`, KeyboardInterrupt.
   - Terminal tests use the unix port's own friendly REPL, so line editing, history, Ctrl-C and paste mode are
     checked against genuine escape sequences.
3. **Hardware**: the only place the real raw-REPL framing, raw-paste, USB serial behaviour and board-specific
   modules are exercised. Not automated.

Findings so far from layer 2 (v1.26.0): `input()` is echoed by the board, so the console must not echo it again
(fixed, and the fake now echoes too); the REPL line-editor redraws are handled by `AnsiScreen`; tab completion is not
available in the unix port's REPL, so it is only tested on the fake. Older releases (v1.22.2) abort on exceptions when
compiled with the runner's GCC 15, so they need an older-toolchain container before they can be added to the matrix.

### Which MicroPython do the badges run?
- **2024 / 2026 badge:** the `badge-2024-software` firmware pins its `micropython` submodule at commit `e0e9fbb`,
  the upstream "Bump version to 1.28.0" commit, so it is **MicroPython 1.28.0** for the **ESP32-S3** port (ESP-IDF
  5.5.1). CI runs the real-MicroPython tests on v1.28.0 (and v1.26.0) for that reason. Still untested: the ESP32
  port itself (USB serial, its raw-paste behaviour, the badge's own modules).
- **2022 badge (TiLDA Mk6):** the `TiDAL-Firmware` repo is a MicroPython board definition for the **ESP32-S3** (not
  the RP2040 assumed earlier), built with ESP-IDF 4.4.1 (the build image is `esp_idf:4.4.1`) and a `micropython.diff` patch.
  Its `micropython` submodule is pinned at upstream commit `afceb56` ("esp32/boards: Add support for LilyGO LoRa32
  boards"), a master snapshot from 2022 that is not a release. The exact version is **not confirmed**: it is probably
  between v1.18 and v1.19.x, which would also fit IDF 4.4. Confirm with `import os; print(os.uname())` on the badge.
  Releases that old do not build cleanly with the runner's GCC 15, so adding one to CI needs an older-toolchain
  container (ubuntu:22.04).

## Folder sync
`sync.py` builds a `Plan` (mkdir / copy / delete actions) by walking both folders, then `apply_plan` runs it.
Size differs -> changed; same size -> compare SHA-256 (`Backend.checksum`, computed on the board with `hashlib`).
If a board has no `hashlib`, equal-size files are assumed identical and the plan says so. Deletes are always
planned but only run when the user ticks "mirror". A file/folder type clash aborts planning rather than guessing.

## Later
Persistent connection profiles; agent mode if measured speed demands it.
