import json

from mpexplorer import meminfo


def test_describe_names_what_the_board_cannot_say():
    full = {"machine": "Raspberry Pi Pico W with RP2040", "flash": 2097152, "fs_total": 868352, "fs_free": 860160,
            "ram_free": 180000, "ram_used": 82000}
    text = meminfo.describe(full)
    assert "Flash chip: 2 MB" in text and "File system: 848.0 KB, 8.0 KB used, 840.0 KB free" in text
    assert "RAM (heap): 255.9 KB, 80.1 KB used, 175.8 KB free" in text
    bare = meminfo.describe({"machine": "x"})
    assert "Flash chip: not reported by this board" in bare and "File system: not reported" in bare
    assert "RAM: not reported" in bare


def test_parse_takes_the_last_json_line_and_ignores_noise():
    line = json.dumps({"flash": 16777216, "fs_free": 5, "junk": "x", "machine": "m"})
    out = b"some banner\r\n" + line.encode() + b"\r\n"
    assert meminfo.parse(out) == {"flash": 16777216, "fs_free": 5, "machine": "m"}
    assert meminfo.parse(b"nothing here") == {}


def test_the_snippet_runs_and_never_probes_flash_off_an_rp2():
    # the fake board runs the code in CPython: not an RP2, so no flash probe, but the file system is read
    info = meminfo.parse(_run_here(meminfo.MEMORY_CODE))
    assert "flash" not in info and info["fs_total"] > 0 and info["fs_free"] >= 0


def _run_here(code):
    import contextlib
    import io
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exec(code, {})
    return out.getvalue()
