# Tested hardware

| Board | MicroPython | Host | Result | Who |
|---|---|---|---|---|
| Tildagon (2024/2026 badge, ESP32-S3, `/dev/ttyACM0` "TiLDAGON - Board CDC") | 1.28.0 (`v1.28.0-dirty on 2026-10-04`, ESP32-S3) | Raspberry Pi 5, Raspberry Pi OS, Python 3.11, Tk 8.6 | Connect, Disconnect and listing `/` work (needs DTR asserted). The REPL console opens with a visible, focused input box. Running code in it, terminal mode, transfers, editor and sync not tried. | Corteil |
| _add a row via pull request_ | | | | |

## Benchmarks (`python3 tools/bench.py`)
Paste the table and the "Decision" line from the script here, with the board, MicroPython version (`os.uname()`) and host.

### Tildagon, MicroPython 1.28.0, Raspberry Pi 5 (2026-10-29)
`python3 tools/bench.py --port /dev/ttyACM0 --sizes 64,256`, one run, the badge's own app interrupted. Free heap 1.7 MB,
about 2 MB free on the flash filesystem. The default 1024 KB size failed in "copy on board" (needs about 2 MB, ENOSPC);
the script now skips that step when there is no room.

| Mode | Test | Seconds | KB/s | Note |
|---|---|---|---|---|
| raw-paste | flash write (on board) | 0.63 | 102.2 |  |
| raw-paste | flash read (on board) | 0.13 | 492.3 |  |
| raw-paste | upload 64 KB | 9.50 | 6.7 |  |
| raw-paste | download 64 KB | 11.26 | 5.7 |  |
| raw-paste | copy on board 64 KB | 0.95 | 67.5 |  |
| raw-paste | sha-256 on board 64 KB | 0.33 | 194.5 |  |
| raw-paste | upload 256 KB | 36.99 | 6.9 |  |
| raw-paste | download 256 KB | 44.11 | 5.8 |  |
| raw-paste | copy on board 256 KB | 2.97 | 86.1 |  |
| raw-paste | sha-256 on board 256 KB | 0.59 | 436.3 |  |
| raw-paste | list 200 files | 9.60 |  |  |
| raw-paste | stat x20 | 2.59 |  |  |
| plain | flash write (on board) | 0.62 | 102.4 |  |
| plain | flash read (on board) | 0.09 | 727.3 |  |
| plain | upload 64 KB | 6.11 | 10.5 |  |
| plain | download 64 KB | 11.19 | 5.7 |  |
| plain | copy on board 64 KB | 0.90 | 71.4 |  |
| plain | sha-256 on board 64 KB | 0.33 | 194.6 |  |
| plain | upload 256 KB | 23.74 | 10.8 |  |
| plain | download 256 KB | 43.68 | 5.9 |  |
| plain | copy on board 256 KB | 2.94 | 87.0 |  |
| plain | sha-256 on board 256 KB | 0.58 | 442.0 |  |
| plain | list 200 files | 9.69 |  |  |
| plain | stat x20 | 2.67 |  |  |

upload 256 KB: 23.7 s (23.7 s per 256 KB) in plain mode -> SLOW
download 256 KB: 43.7 s (43.7 s per 256 KB) in plain mode -> SLOW
list 200 files: 9.60 s -> SLOW
Board flash write 102 KB/s vs best upload 10 KB/s: the link is the bottleneck, an agent could help
Decision: agent mode is worth building (a threshold was exceeded).

Notes: the link is the bottleneck (flash writes at about 100 KB/s, uploads arrive at 7-11 KB/s). On this board raw-paste
uploaded slower than plain (6.9 vs 10.8 KB/s); downloads were equal. Not measured: Pico, 2022 badge.
