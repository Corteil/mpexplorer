# Tested hardware

| Board | MicroPython | Host | Result | Who |
|---|---|---|---|---|
| Tildagon (2024/2026 badge, ESP32-S3, `/dev/ttyACM0` "TiLDAGON - Board CDC") | not recorded (run `os.uname()`) | Raspberry Pi 5, Raspberry Pi OS, Python 3.11, Tk 8.6 | Connect, Disconnect and listing `/` work (needs DTR asserted). The REPL console opens with a visible, focused input box. Running code in it, terminal mode, transfers, editor and sync not tried. | Corteil |
| _add a row via pull request_ | | | | |

## Benchmarks (`python3 tools/bench.py`)
Paste the table and the "Decision" line from the script here, with the board, MicroPython version (`os.uname()`) and host.
