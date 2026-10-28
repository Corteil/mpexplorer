# Plan: agent mode (opt-in, RAM-only)

Status: **planned, not started.** The short design in `docs/DESIGN.md` ("Planned: agent mode") is the summary; this is the
detailed plan. Decision rule: build it only if hardware numbers say the snippet approach is too slow (phase 0).

## Why, and the honest ceiling
Today every operation is a Python snippet sent through the raw REPL; file data travels as base64 in 2 KB chunks, one
round trip per chunk. The agent replaces that with a small binary protocol served by a loop running on the board.
- Expected gain: fewer bytes (no base64, no per-chunk source), no per-chunk compile, pipelining hides USB latency, and one
  round trip per folder listing instead of one per directory plus a `stat` per entry.
- **Ceiling:** USB full-speed CDC is roughly 0.5-1 MB/s, but on these boards the flash filesystem write speed
  (often 50-300 KB/s) is usually the limit, so uploads may only improve a few times over. Downloads and listings gain more.
  Measure before promising numbers.

## Non-negotiables
- **Nothing is written to the board.** The host sends the agent source over the raw REPL (`exec`); it lives in RAM and is
  gone after `Q`, a reset or an idle timeout. No `main.py`, no flash file, ever.
- **Opt-in per connection**, default off until proven on hardware. Every operation keeps working without it (fallback to
  the snippet backend, automatically, with a message).
- The user is told it runs a helper on the board while enabled (status bar indicator).

## Phase 0 - measure first (needs hardware) -> decision gate
**Built:** `tools/bench.py` (tested on the fake board and real MicroPython in CI). Run it on each board:
`python3 tools/bench.py --port /dev/ttyACM0` (close mpexplorer first). It works only in `/_mpx_bench` on the board, removes
it, soft-resets the board at the end, prints a table and applies the rule below. The original spec follows.

`tools/bench.py` (new): connect, then time (a) upload and download of 64 KB, 256 KB and 1 MB random files, (b) listing a
folder of 200 files, (c) `copy_file` on device, each with raw-paste on and off; print KB/s and seconds as a table to paste
into `docs/HARDWARE.md`. Run on the Tildagon, a Pico and the 2022 badge.
Build the agent only if, on at least one target, 256 KB upload/download takes longer than ~10 s or listing 200 files
takes longer than ~2 s. Also record the board's own flash write speed (write 256 KB with plain `open().write` on the
board) to know the ceiling.

## Loading and running
1. Host sends the agent source with `repl.exec(...)` (raw-paste if available). Source is hand-minified, about 3-4 KB,
   MicroPython/CPython-compatible subset. Compiling it needs a few times its size in heap (fine on ESP32-S3 and RP2).
2. The agent's code ends in `serve()`: it never returns until told to, so the raw-REPL `exec` stays "running" and the
   same serial line now carries binary frames. On `Q` (or idle timeout, or fatal error) `serve()` returns, the REPL
   prints its normal `0x04` terminators, and the connection is back in ordinary raw-REPL state.
3. At start the agent: `gc.collect()`, allocates one 4 KB receive buffer and a `memoryview`, and calls
   `micropython.kbd_intr(-1)` (guarded by `try`) because **0x03 inside binary data would otherwise raise
   KeyboardInterrupt**. It restores `kbd_intr(3)` on exit. An idle timeout (default 30 s with no byte) makes it exit
   so a crashed host cannot leave the board deaf to Ctrl-C.
4. I/O uses `sys.stdin.buffer` / `sys.stdout.buffer` (uncooked). **To verify on each port:** no CR/LF translation, no
   7-bit mangling, no 256-byte corruption (the Tildagon needs DTR high). A self-test frame at start sends all 256 byte
   values and a CRC; if it fails the host falls back to **text-safe mode** (same protocol, base64-wrapped frames, still
   far fewer round trips than snippets) or to snippets.

## Wire protocol (v1)
Frame: `0xA5 0x5A | seq:1 | cmd:1 | len:2 LE | payload | crc32:4 LE` over `seq..payload`. Replies use the same shape
with `cmd` = `0x00` (ok) or an error code (payload = errno + short text). Max payload 4096.
Garbage between frames is skipped by hunting for the magic bytes, so a corrupt or truncated frame costs one retry,
not the connection.

| cmd | operation | payload -> reply |
|---|---|---|
| `I` | hello/info | -> protocol version, platform, free heap, max payload, flags (has crc32, has hashlib) |
| `L` | list dir | path -> entries `[type, size, mtime, name]`, split over several frames if long |
| `S` | stat | path -> type, size, mtime |
| `R` | read | path, offset, count -> bytes (host loops, optionally pipelined) |
| `W` | write | path, offset, flags (truncate/append/sync), bytes |
| `C` | copy on device | src, dst (device-side loop, progress frames optional) |
| `M` `D` `K` | rename, delete (recursive option), mkdir (parents option) | paths |
| `H` | SHA-256 | path -> digest (for sync/verify), or "unavailable" |
| `F` | free space | path -> bytes |
| `X` | exec a snippet | source -> stdout (lets the emulated console reuse the same link) |
| `Q` | quit | -> ok, then leave `serve()` |

Atomic writes: `W` with the temp-name-then-rename policy stays host-side (same as `sync.py` today), so semantics do
not change. Errors map to the exceptions the GUI already shows (`BackendError`).

## Host side
- `transport.py` gains `AgentLink`: framing, CRC, sequence numbers, retry (3 tries), resync, idle keepalive, trace
  events (so "Show activity" shows agent frames too). Reuses `RawREPL.ser`, lock and `terminal` ownership rules.
- New `AgentBackend` implementing the same interface as `DeviceBackend` (`listdir stat mkdir remove rmdir rename
  open_read open_write free_space checksum copy_file run soft_reset close`). `ops.py`, `sync.py`, the job runner and both
  GUIs do not change.
- `DeviceBackend.start_agent()` loads and handshakes; any failure at any step leaves the plain backend working.
- Ownership: the agent holds the serial line like terminal mode does. `RawREPL.exec()` while the agent is active either
  stops it (`Q`) and runs the snippet, or the console routes through the `X` command. Chosen: auto-stop, restart lazily
  on the next file operation (cheap), so the console and terminal need no special cases.
- Pipelining (phase 4): send up to N read/write requests before reading replies (N=4 with 4 KB frames), matched by
  `seq`. This hides USB latency, the main cost on many small frames.

## GUI
- Device menu / connection setting: "Fast mode (RAM helper)" checkbox, remembered per port; default off.
- Status bar shows `fast mode` while active; a failed start shows why and continues in normal mode.
- Settings text states plainly: runs a temporary helper in the board's RAM, removed on disconnect or reset.

## Tests (fits the three existing layers)
1. **Agent logic under CPython**: the agent module is importable and driven in-process through paired in-memory streams
   (no serial). Cover every command, all 256 byte values, empty and 4 KB+1 payloads, corrupt CRC, truncated frame, duplicate
   `seq`, garbage prefix, idle timeout, errno mapping. Property test: random sequences of operations compared with the
   local backend.
2. **Fake board**: extend `tests/fakedevice.py` so `exec`ing the agent source runs the real agent code against the fake
   serial line, then run the existing backend/ops/sync tests with `AgentBackend` too (same assertions, new backend).
3. **Real MicroPython** (CI layer 2): run the agent on the unix port via `rawrepl_shim.py` (stdin/stdout pty), same
   backend tests. Catches MicroPython-specific mistakes (`readinto`, `memoryview`, `struct`, `binascii.crc32`,
   `kbd_intr` availability). v1.26.0 and v1.28.0 legs.
4. **Hardware** (manual, `docs/HARDWARE.md` rows): soak test of a few hundred MB with random data and CRC compare; pull the
   cable mid-transfer; Ctrl-C from a second terminal; reset during a write.

## Phases
0. Benchmark + decision gate (above).
1. `agent_src.py` + `AgentLink` + CPython tests (no GUI).
2. `AgentBackend`, fake-board and real-MicroPython tests, automatic fallback.
3. GUI toggle, status indicator, docs.
4. Pipelining; re-measure.
5. Hardware shakedown on Tildagon, Pico, 2022 badge; fix; add numbers to `docs/HARDWARE.md`.

## Risks
- Port differences: stdin/stdout buffering and CDC behaviour (RP2040 vs RP2350 vs ESP32-S3 USB-Serial-JTAG/TinyUSB);
  `kbd_intr` or `crc32` missing on a port (guarded, with fallbacks); RAM on small builds.
- A running app on the badge must be interrupted first (same as raw REPL today) and its screen stays frozen while
  connected; the agent does not change that.
- Lost bytes on a flaky cable: handled by CRC + retry, but a long stall ends in the idle timeout and a clean fallback.
- Complexity budget: keep the agent under ~150 lines; if it grows past that, simplify the protocol instead.

## Success criteria
At least 3x on 256 KB transfers and under 1 s to list 200 files on a target where phase 0 showed a problem; no data
corruption in the soak test; recovery after cable pull without restarting the GUI; all existing tests pass unchanged for
the plain backend.
