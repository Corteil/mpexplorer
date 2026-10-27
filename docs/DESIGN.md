# Design

## Goals
- Run on a Raspberry Pi 5; targets are Pico-class boards (RP2040/RP2350) and the EMF Camp badges:
  2022 (RP2040), 2024 Tildagon and 2026 (the same hardware and firmware as 2024, new front plate; ESP32-S3). The raw REPL is chip-independent, so all
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

### Terminal mode (planned): the board's real REPL
Goal: an exact REPL feel - the board's own tab completion, history, auto-indent, paste mode (Ctrl-E), `_`, Ctrl-D and
`input()` - by passing keystrokes straight through instead of emulating.

1. **Connection ownership.** The raw REPL (files, sync, emulated console) and the friendly REPL cannot be used at the
   same time on one serial line. Add an explicit "device mode" to the app: `RAW` (default) or `TERMINAL`. Switching to
   terminal waits for the job queue to drain, sends Ctrl-B (`\x02`) to leave raw mode, and the file panes show
   "Terminal active - device operations paused". Switching back sends Ctrl-C, Ctrl-A and re-probes. Board state
   (variables, imports) survives because the interpreter is never reset by these switches.
2. **`TerminalSession`** (GUI-free): owns a reader thread that pushes received bytes into a queue, and
   `send(bytes)` for keystrokes. No `exec` framing, no `OK`/`\x04` parsing.
3. **`AnsiScreen`** (GUI-free, unit-tested): a small screen-buffer that applies what the MicroPython line editor
   emits: `\r`, `\n`, `\b`, `ESC[K` (erase to end of line), `ESC[nD`/`ESC[nC` (cursor left/right), `ESC[J`. It exposes
   lines plus cursor position for the Tk widget to draw. Scrollback is a plain list of finished lines.
4. **`TerminalWidget`** (Tk): a `Text` widget that captures `<Key>`, maps keys to bytes (Enter -> `\r`, Backspace ->
   `\x7f`, arrows -> `ESC[A/B/C/D`, Home/End, Tab -> `\t`, Ctrl-letter -> control byte; Ctrl-C/D/E/A/B pass through,
   copy/paste use Ctrl+Shift+C / Ctrl+Shift+V). Multi-line paste is wrapped in paste mode (`\x05 ... \x04`) so
   indentation is not mangled.
5. **Testing.** Extend `FakeSerial` with a friendly-REPL state: prompt `>>> `, echo, backspace, history arrows, tab
   completion, `ESC[K` redraws, Ctrl-C/D/E handling and the raw-mode switch (`\x01` / `\x02`). Unit-test `AnsiScreen`
   directly with recorded byte streams; test the mode switch (state preserved, queue drained, panes paused).
6. **Risks.** Key mapping differences between desktop environments; UTF-8 and non-printable bytes in output; a program
   that never returns blocks the switch back until Ctrl-C; firmware without tab completion; USB re-enumeration after a
   hard reset leaves the terminal disconnected (offer Reconnect).

Phases: (a) `AnsiScreen` + tests, (b) fake friendly REPL + `TerminalSession` + tests, (c) mode switching in the app,
(d) Tk widget and the console-window mode selector (currently shown disabled), (e) hardware shakedown.

## Folder sync
`sync.py` builds a `Plan` (mkdir / copy / delete actions) by walking both folders, then `apply_plan` runs it.
Size differs -> changed; same size -> compare SHA-256 (`Backend.checksum`, computed on the board with `hashlib`).
If a board has no `hashlib`, equal-size files are assumed identical and the plan says so. Deletes are always
planned but only run when the user ticks "mirror". A file/folder type clash aborts planning rather than guessing.

## Later
Persistent connection profiles; agent mode if measured speed demands it.
