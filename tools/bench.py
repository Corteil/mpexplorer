#!/usr/bin/env python3
"""Benchmark transfers to a board: decides whether the opt-in agent mode (docs/AGENT_PLAN.md) is worth building.

    python3 tools/bench.py                       # first serial port, 64/256/1024 KB, 200 files, both raw-paste modes
    python3 tools/bench.py --port /dev/ttyACM0 --sizes 64,256 --files 100

Close mpexplorer first (one program per serial port). Everything happens in /_mpx_bench on the board, which is
removed at the end. The board is soft-reset when done (use --no-reset to skip) because the raw REPL freezes a badge's
own app. Paste the printed table into docs/HARDWARE.md.
"""
import argparse
import hashlib
import os
import platform
import random
import shutil
import statistics
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from mpexplorer.backends import DeviceBackend, LocalBackend  # noqa: E402
from mpexplorer.errors import BackendError  # noqa: E402
from mpexplorer.ops import ConflictPolicy, Progress, copy_tree, delete_tree  # noqa: E402
from mpexplorer.transport import RawREPL  # noqa: E402

BENCH_DIR = "/_mpx_bench"
# The decision rule from docs/AGENT_PLAN.md: build the agent only if one of these is exceeded.
SLOW_256KB_SECONDS = 10.0
SLOW_LISTING_SECONDS = 2.0

FLASH_SNIPPET = (
    "import time\n"
    "try:\n ticks=time.ticks_ms;diff=time.ticks_diff\n"
    "except AttributeError:\n ticks=lambda:int(time.time()*1000);diff=lambda a,b:a-b\n"
    "buf=bytes(range(256))*4\n"
    "t=ticks()\n"
    "with open(%(p)r,'wb') as f:\n for i in range(%(n)d):\n  f.write(buf)\n"
    "w=diff(ticks(),t)\n"
    "t=ticks()\n"
    "with open(%(p)r,'rb') as f:\n while f.read(1024):\n  pass\n"
    "r=diff(ticks(),t)\n"
    "print(w,r)")

MAKE_FILES_SNIPPET = (
    "for i in range(%(n)d):\n"
    " with open(%(d)r+'/f%%04d.txt'%%i,'w') as f:\n  f.write('x'*(i%%50))\n")

INFO_SNIPPET = (
    "import os,gc,sys\n"
    "try:\n u=os.uname();f=[u[0],u[2],u[3],u[4]]\n"      # the unix build of MicroPython has no os.uname
    "except AttributeError:\n i=sys.implementation;f=[i[0],'.'.join(str(n) for n in i[1]),'',sys.platform]\n"
    "gc.collect()\n"
    "print(';'.join(f+[sys.platform,str(getattr(gc,'mem_free',lambda:0)())]))")


def timed(fn):
    t = time.perf_counter()
    result = fn()
    return time.perf_counter() - t, result


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def board_info(repl):
    parts = repl.exec(INFO_SNIPPET).decode().strip().split(";")
    keys = ("system", "release", "version", "machine", "platform", "free_heap")
    return dict(zip(keys, parts))


def run_mode(repl, label, sizes_kb, nfiles, root, workdir, log):
    """Time every operation on one connection; returns a list of result rows."""
    dev, local = DeviceBackend(repl), LocalBackend()
    rows = []

    def row(test, seconds, kb=None, note=""):
        rate = (kb / seconds) if (kb and seconds > 0) else None
        rows.append({"mode": label, "test": test, "seconds": round(seconds, 3),
                     "kb_per_s": round(rate, 1) if rate else None, "note": note})
        log("  %-26s %8.2f s %s" % (test, seconds, "%8.1f KB/s" % rate if rate else ""))

    # the ceiling: how fast the board's own filesystem can write and read, with no serial link involved
    kb = max(sizes_kb[0], 64)
    out = repl.exec(FLASH_SNIPPET % {"p": root + "/flash.bin", "n": kb}, timeout=120).decode().split()
    w_ms, r_ms = max(int(out[0]), 1), max(int(out[1]), 1)
    row("flash write (on board)", w_ms / 1000, kb)
    row("flash read (on board)", r_ms / 1000, kb)
    dev.remove(root + "/flash.bin")

    rng = random.Random(1234)
    for size in sizes_kb:
        free_kb = int(repl.exec("import os;s=os.statvfs('/');print(s[0]*s[3]//1024)").decode().strip())
        if free_kb < size + 64:
            log("  %d KB skipped: only %d KB free on the board" % (size, free_kb))
            continue
        src = os.path.join(workdir, "up%d.bin" % size)
        with open(src, "wb") as f:
            f.write(rng.randbytes(size * 1024))
        remote = root + "/up%d.bin" % size
        policy = ConflictPolicy(lambda name: ("overwrite", True))
        secs, _ = timed(lambda: copy_tree(local, src, dev, remote, Progress(), policy))
        row("upload %d KB" % size, secs, size)
        back = os.path.join(workdir, "down%d.bin" % size)
        secs, _ = timed(lambda: copy_tree(dev, remote, local, back, Progress(), policy))
        row("download %d KB" % size, secs, size)
        if sha(src) != sha(back):
            raise BackendError("%d KB round trip came back different: data corruption" % size)
        free_kb -= size
        if free_kb > size + 64:
            secs, _ = timed(lambda: dev.copy_file(remote, remote + ".copy"))
            row("copy on board %d KB" % size, secs, size)
        else:
            log("  copy on board %d KB skipped: only %d KB free on the board" % (size, free_kb))
        secs, digest = timed(lambda: dev.checksum(remote))
        row("sha-256 on board %d KB" % size, secs, size, "" if digest else "no hashlib")

    folder = root + "/many"
    dev.mkdir(folder)
    repl.exec(MAKE_FILES_SNIPPET % {"n": nfiles, "d": folder}, timeout=120)
    times = [timed(lambda: dev.listdir(folder))[0] for _ in range(3)]
    row("list %d files" % nfiles, statistics.median(times))
    secs, _ = timed(lambda: [dev.stat(folder + "/f%04d.txt" % i) for i in range(0, nfiles, max(1, nfiles // 20))])
    row("stat x%d" % len(range(0, nfiles, max(1, nfiles // 20))), secs)
    return rows


def run_benchmark(make_repl, sizes_kb, nfiles, root=BENCH_DIR, log=print, force=False, reset=True):
    """make_repl(paste) -> RawREPL (paste None = use raw-paste if offered, False = never). Returns (info, rows)."""
    workdir = tempfile.mkdtemp(prefix="mpx-bench-")
    all_rows, info = [], {}
    try:
        for want_paste in (None, False):
            repl = make_repl(want_paste)
            try:
                repl.enter_raw()
                dev = DeviceBackend(repl)
                if want_paste is None:
                    info = board_info(repl)
                    log("Board: %(system)s %(release)s, %(machine)s, free heap %(free_heap)s" % info)
                    log("Version: %(version)s" % info)
                elif repl.paste is False and all_rows and all_rows[0]["mode"] == "plain":
                    break                               # the board has no raw-paste: nothing new to measure
                if dev.stat(root) is not None and not force:
                    raise BackendError("%s already exists on the board; remove it or use --force" % root)
                if dev.stat(root) is None:
                    dev.mkdir(root)
                try:
                    repl.exec("pass")                   # the first exec negotiates raw-paste
                    label = "raw-paste" if repl.paste else "plain"
                    log("\nMode: %s%s" % (label, "" if repl.paste or want_paste is False else " (raw-paste not offered)"))
                    all_rows += run_mode(repl, label, sizes_kb, nfiles, root, workdir, log)
                finally:
                    try:
                        delete_tree(dev, root, Progress())
                    except Exception as e:              # never hide the real error, but say the cleanup failed
                        log("WARNING: could not remove %s on the board: %s" % (root, e))
                    if reset:                           # also after an error: raw REPL freezes a badge's screen
                        try:
                            dev.soft_reset()
                        except Exception as e:
                            log("WARNING: could not soft-reset the board: %s" % e)
            finally:
                try:
                    repl.close()
                except Exception:
                    pass
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return info, all_rows


def verdict(rows, sizes_kb):
    """Apply the decision rule from docs/AGENT_PLAN.md to the best (fastest) mode measured."""
    lines = []
    size = 256 if 256 in sizes_kb else sizes_kb[-1]
    for test in ("upload %d KB" % size, "download %d KB" % size):
        best = min((r for r in rows if r["test"] == test), key=lambda r: r["seconds"], default=None)
        if best:
            scaled = best["seconds"] * 256 / size
            lines.append("%s: %.1f s (%.1f s per 256 KB) in %s mode -> %s" % (
                test, best["seconds"], scaled, best["mode"], "SLOW" if scaled > SLOW_256KB_SECONDS else "ok"))
    listing = min((r for r in rows if r["test"].startswith("list ")), key=lambda r: r["seconds"], default=None)
    if listing:
        lines.append("%s: %.2f s -> %s" % (listing["test"], listing["seconds"],
                                           "SLOW" if listing["seconds"] > SLOW_LISTING_SECONDS else "ok"))
    flash = next((r for r in rows if r["test"].startswith("flash write")), None)
    up = min((r for r in rows if r["test"].startswith("upload ")), key=lambda r: r["seconds"], default=None)
    if flash and up and flash["kb_per_s"] and up["kb_per_s"]:
        lines.append("Board flash write %.0f KB/s vs best upload %.0f KB/s: %s" % (
            flash["kb_per_s"], up["kb_per_s"],
            "the link is the bottleneck, an agent could help" if up["kb_per_s"] < 0.5 * flash["kb_per_s"]
            else "the filesystem is already the bottleneck, an agent will not help uploads much"))
    slow = any(line.endswith("SLOW") for line in lines)
    lines.append("Decision: " + ("agent mode is worth building (a threshold was exceeded)." if slow
                                 else "agent mode is NOT needed for these numbers."))
    return lines


def markdown(info, rows):
    out = ["| Mode | Test | Seconds | KB/s | Note |", "|---|---|---|---|---|"]
    for r in rows:
        out.append("| %s | %s | %.2f | %s | %s |" % (r["mode"], r["test"], r["seconds"],
                                                  "" if r["kb_per_s"] is None else r["kb_per_s"], r["note"]))
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", help="serial port (default: first port found, Pico boards first)")
    ap.add_argument("--sizes", default="64,256,1024", help="comma separated file sizes in KB")
    ap.add_argument("--files", type=int, default=200, help="files in the listing test")
    ap.add_argument("--force", action="store_true", help="reuse %s if it already exists" % BENCH_DIR)
    ap.add_argument("--no-reset", action="store_true", help="do not soft-reset the board at the end")
    args = ap.parse_args()
    sizes = [int(s) for s in args.sizes.split(",") if s.strip()]
    port = args.port
    if not port:
        from mpexplorer.ports import list_ports
        ports = list_ports()
        if not ports:
            sys.exit("No serial port found. Plug the board in or pass --port.")
        port = ports[0].split("  ")[0]
    print("Host: %s, Python %s; port %s" % (platform.platform(), platform.python_version(), port))
    try:
        info, rows = run_benchmark(lambda paste: RawREPL.open(port, paste=paste, adaptive=False), sizes, args.files,
                                   force=args.force, reset=not args.no_reset)
    except BackendError as e:
        sys.exit("Benchmark stopped: %s" % e)
    print("\n" + markdown(info, rows) + "\n")
    print("\n".join(verdict(rows, sizes)))
    print("\nPaste the table and decision into docs/HARDWARE.md with the board and MicroPython version above.")


if __name__ == "__main__":
    main()
