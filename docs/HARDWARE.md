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
uploaded slower than plain (6.9 vs 10.8 KB/s); downloads were equal. Measured later on the 2022 badge, see below.

### Pico, MicroPython 1.28.0, Raspberry Pi 5 (2026-10-30)
Raspberry Pi Pico (RP2040), `v1.28.0 on 2026-04-06`, about 1400 KB free flash. The 1024 KB step is skipped (not enough room).

| Mode | Test | Seconds | KB/s | Note |
|---|---|---|---|---|
| raw-paste | flash write (on board) | 0.71 | 90.1 |  |
| raw-paste | flash read (on board) | 0.03 | 2285.7 |  |
| raw-paste | upload 64 KB | 2.17 | 29.5 |  |
| raw-paste | download 64 KB | 1.00 | 63.8 |  |
| raw-paste | copy on board 64 KB | 0.79 | 80.8 |  |
| raw-paste | sha-256 on board 64 KB | 0.14 | 453.1 |  |
| raw-paste | upload 256 KB | 8.52 | 30.0 |  |
| raw-paste | download 256 KB | 3.84 | 66.7 |  |
| raw-paste | copy on board 256 KB | 2.87 | 89.3 |  |
| raw-paste | sha-256 on board 256 KB | 0.43 | 598.9 |  |
| raw-paste | list 200 files | 0.20 |  |  |
| raw-paste | stat x20 | 0.64 |  |  |
| plain | flash write (on board) | 0.70 | 90.8 |  |
| plain | flash read (on board) | 0.03 | 2206.9 |  |
| plain | upload 64 KB | 3.92 | 16.3 |  |
| plain | download 64 KB | 1.24 | 51.6 |  |
| plain | copy on board 64 KB | 0.79 | 81.2 |  |
| plain | sha-256 on board 64 KB | 0.16 | 398.0 |  |
| plain | upload 256 KB | 16.25 | 15.8 |  |
| plain | download 256 KB | 4.88 | 52.5 |  |
| plain | copy on board 256 KB | 2.93 | 87.4 |  |
| plain | sha-256 on board 256 KB | 0.45 | 575.0 |  |
| plain | list 200 files | 0.21 |  |  |
| plain | stat x20 | 0.77 |  |  |

upload 256 KB: 8.5 s in raw-paste mode -> ok; download 256 KB: 3.8 s -> ok; list 200 files: 0.20 s -> ok.
Decision: agent mode is NOT needed for these numbers.

Notes: raw-paste is the faster upload here (30 vs 16 KB/s), the opposite of the Tildagon. An earlier run filled the flash
(`OSError: 28`) and left a garbled directory entry; the flash was reformatted (`os.VfsLfs2.mkfs`) and `tools/bench.py` now skips
a size that does not fit.

### Pico 2, MicroPython 1.28.0, Raspberry Pi 5 (2026-10-30)
Raspberry Pi Pico 2 (RP2350), `v1.28.0 on 2026-04-06`.

| Mode | Test | Seconds | KB/s | Note |
|---|---|---|---|---|
| raw-paste | flash write (on board) | 0.19 | 340.4 |  |
| raw-paste | flash read (on board) | 0.01 | 5333.3 |  |
| raw-paste | upload 64 KB | 1.11 | 57.6 |  |
| raw-paste | download 64 KB | 0.66 | 97.3 |  |
| raw-paste | copy on board 64 KB | 0.23 | 282.8 |  |
| raw-paste | sha-256 on board 64 KB | 0.07 | 962.8 |  |
| raw-paste | upload 256 KB | 4.38 | 58.4 |  |
| raw-paste | download 256 KB | 2.52 | 101.5 |  |
| raw-paste | copy on board 256 KB | 0.81 | 316.2 |  |
| raw-paste | sha-256 on board 256 KB | 0.18 | 1416.0 |  |
| raw-paste | upload 1024 KB | 17.71 | 57.8 |  |
| raw-paste | download 1024 KB | 10.00 | 102.4 |  |
| raw-paste | copy on board 1024 KB | 3.14 | 326.5 |  |
| raw-paste | sha-256 on board 1024 KB | 0.64 | 1606.4 |  |
| raw-paste | list 200 files | 0.11 |  |  |
| raw-paste | stat x20 | 0.37 |  |  |
| plain | flash write (on board) | 0.19 | 338.6 |  |
| plain | flash read (on board) | 0.02 | 4000.0 |  |
| plain | upload 64 KB | 3.22 | 19.9 |  |
| plain | download 64 KB | 0.87 | 73.4 |  |
| plain | copy on board 64 KB | 0.82 | 78.3 |  |
| plain | sha-256 on board 64 KB | 0.08 | 768.3 |  |
| plain | upload 256 KB | 13.58 | 18.9 |  |
| plain | download 256 KB | 3.31 | 77.2 |  |
| plain | copy on board 256 KB | 3.01 | 85.1 |  |
| plain | sha-256 on board 256 KB | 0.20 | 1301.0 |  |
| plain | upload 1024 KB | 54.07 | 18.9 |  |
| plain | download 1024 KB | 13.09 | 78.3 |  |
| plain | copy on board 1024 KB | 11.75 | 87.1 |  |
| plain | sha-256 on board 1024 KB | 0.65 | 1570.8 |  |
| plain | list 200 files | 0.12 |  |  |
| plain | stat x20 | 0.49 |  |  |

upload 256 KB: 4.4 s in raw-paste mode -> ok; download 256 KB: 2.5 s -> ok; list 200 files: 0.11 s -> ok.
Board flash write 340 KB/s vs best upload 58 KB/s: the link is the bottleneck, an agent could help (uploads only).
Decision: agent mode is NOT needed for these numbers.

### 2022 badge (TiDAL), MicroPython 1.18.0, Raspberry Pi 5 (2026-10-31)
`machine='TiDAL with ESP32S3'`, `esp32 1.18.0`, build `afceb56-dirty on 2026-07-25`, USB `16d0:1114`, about 3.9 MB of flash free.
Run with `python3 tools/bench.py --port /dev/ttyACM1 --rts --no-reset`. **Raw-paste only**: the run stopped at the 200-file listing
(see below), so the plain-mode pass and the listing times were not measured.

| Test | Time | Speed |
|---|---|---|
| flash write (on board) | 1.04 s | 61.8 KB/s |
| flash read (on board) | 0.12 s | 524.6 KB/s |
| upload 64 / 256 / 1024 KB | 10.6 / 42.0 / 167.1 s | 6.0 / 6.1 / 6.1 KB/s |
| download 64 / 256 / 1024 KB | 7.2 / 26.4 / 103.1 s | 8.9 / 9.7 / 9.9 KB/s |
| copy on board 64 / 256 / 1024 KB | 1.6 / 5.4 / 20.2 s | 39.2 / 47.9 / 50.7 KB/s |
| SHA-256 on board 64 / 256 / 1024 KB | 0.55 / 0.90 / 2.29 s | 116.6 / 285.7 / 447.3 KB/s |

By the decision rule in `docs/AGENT_PLAN.md` (a 256 KB upload over 10 s) this is slow, like the Tildagon: the limit is the badge's USB
link, not flash.

What is different about this badge (all seen by hand or in the run above, 2026-10-31):
- **It stays silent unless RTS is asserted as well as DTR.** With RTS low (what the Tildagon needs) it never answers. mpexplorer now
  tries RTS low first and, if the board says nothing, RTS high, and remembers the result per board (`boards.rts`; `RawREPL.open_board`).
  Checked with the real badge (2026-10-31, the app's own `connect_device` run under Xvfb with a scratch settings file, no status
  screen, no clock set, no reset): the first connect took 6.9 s (RTS low tried, then high), listed the root, saved
  `boards.rts` for the board, and the second connect took 0.8 s. Disconnect left the badge on USB. The owner then connected in the GUI by hand and it worked; the pane showed the badge's real contents (`/apps` holds only `tidal_oscilloscope`, checked with a read-only script).
- **A soft reset (Ctrl-D) makes it drop off USB** and it does not come back until it is unplugged and re-plugged (seen three times:
  after a probe, after a clean-up, and during the benchmark's first run). So do not use "Soft reset" or "Restart the board when
  disconnecting" with it, and run the benchmark with `--no-reset`. Cause not known.
- **Connecting does not freeze its screen:** the owner saw the badge's own GUI keep working while the benchmark ran (the Tildagon's
  app freezes in raw REPL).
- **The 200-file listing test hung.** The benchmark waited with no traffic for over a minute inside `listdir`; after Ctrl-C the reply
  turned up complete in the next read, followed by a late raw-paste answer (`R\x01\x80\x00\x01`). So the reply did arrive, very
  late, and the reader was not confused by its size: on a second 2022 badge (2022-06-04 build, 72 KB free, so no files were created)
  a read-only check printed JSON listings of 27, 100, 200, 400 and 800 entries (up to 20 KB) three times each, all correct, in
  0.1 to 0.4 s. That left a slow board as the guess, so it was tried
  on the first badge (2026-10-31, 3.9 MB free, no soft reset): 200 empty files in a scratch folder
  (creating them took 64 s, 0.32 s per file), listed by the board's own `os.ilistdir` in 2.04 s and by the app's `listdir` in
  2.3 s (three times, steady); the same after writing a 256 KB file in that folder (2.07 / 2.3 s) and after removing 100 files
  (1.89 / 2.1 s for 101 entries). So a 200-file folder lists slowly on this badge (about 2 s, mostly the board's own
  `ilistdir`, not the link) but **the more-than-a-minute stall did not reproduce**. What differed in the benchmark: it listed the
  folder right after its 1 MB upload, download and copy tests in the same raw-REPL session. Cause still unknown. The scratch folder
  was removed and the badge's files and free space are as before.
- The benchmark's scratch folder `/_mpx_bench` was left behind and removed by hand afterwards; the badge's files are as they were.

### Upload mode on the Tildagon (2026-10-29)
The board's raw-paste window is 128 bytes, so raw-paste stops to wait for the device every 128 bytes (about 8.7 ms per
window over USB, about 15 KB/s of code text). Plain sending (256-byte blocks, 5 ms apart) moves about 35 KB/s. Uploading
128 KB, three runs each, interleaved: raw-paste only 16.0 / 16.6 / 17.5 s (about 7.7 KB/s), plain 9.8 / 10.4 / 11.1 s (about
12.3 KB/s), adaptive 10.8 s. `RawREPL` therefore times its first big sends in each mode and keeps plain only when it is
at least 1.25x faster (`PLAIN_GAIN`); a failed plain send puts the connection back on raw-paste. Data checked with an
on-board SHA-256. Measured later on the 2022 badge, see below. Downloads are limited by the board's output speed (about 11 KB/s of text,
independent of mode), not by this.
