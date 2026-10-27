# Design

## Goals
- Run on a Raspberry Pi 5; targets are Pico-class boards and the EMF 2022 badge (all MicroPython).
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

## Planned: raw-paste mode (still no installed code)
MicroPython >= 1.14 supports raw-paste (`Ctrl-E A Ctrl-A`), a flow-controlled way to send snippets. This removes the
per-256-byte pauses and should roughly double upload speed. Detected at connect, with fallback to plain raw REPL.

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

## Later
Sync a folder by hash, REPL console pane, persistent connection profiles.
