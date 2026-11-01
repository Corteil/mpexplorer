"""How much memory a board has: flash chip, the file system on it, and RAM. Read-only; the same code runs on MicroPython
and CircuitPython. Pure functions, so it is tested without a window (the window just shows `describe`)."""
import json

# Printed as one JSON line. Each part is optional: a board that cannot tell leaves it out.
# The RP2 flash size is found where the flash contents repeat in the XIP address space (what the backup tool does);
# that read is only ever made on an RP2, because the address means nothing (or faults) on other chips.
MEMORY_CODE = """
import os,gc,json
r={}
m=os.uname().machine.lower()
if 'rp2' in m:
    try:
        import uctypes
        b=0x10000000
        s=bytes(uctypes.bytearray_at(b,256))
        for n in (1,2,4,8,16):
            if bytes(uctypes.bytearray_at(b+n*1048576,256))==s:
                r['flash']=n*1048576
                break
    except Exception:
        pass
else:
    try:
        import esp
        r['flash']=esp.flash_size()
    except Exception:
        pass
try:
    st=os.statvfs('/')
    r['fs_total']=st[0]*st[2]
    r['fs_free']=st[0]*st[3]
except Exception:
    pass
try:
    gc.collect()
    r['ram_free']=gc.mem_free()
    r['ram_used']=gc.mem_alloc()
except Exception:
    pass
r['machine']=os.uname().machine
print(json.dumps(r))
"""


def parse(output):
    """The board's JSON line as a dict of ints (and 'machine'); {} if there was none."""
    text = output.decode(errors="replace") if isinstance(output, bytes) else output
    for line in reversed(text.strip().splitlines()):
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if k == "machine" or isinstance(v, int)}
    return {}


def size(n):
    if n >= 1024 * 1024:
        return "%.1f MB" % (n / 1048576) if n % 1048576 else "%d MB" % (n // 1048576)
    return "%.1f KB" % (n / 1024) if n >= 1024 else "%d bytes" % n


def describe(info):
    """Lines for the window: flash, file system, RAM; what the board could not say is named, not hidden."""
    lines = [info["machine"]] if info.get("machine") else []
    lines.append("Flash chip: " + (size(info["flash"]) if info.get("flash") else "not reported by this board"))
    if "fs_total" in info:
        used = info["fs_total"] - info.get("fs_free", 0)
        free = info.get("fs_free", 0)
        lines.append("File system: %s, %s used, %s free" % (size(info["fs_total"]), size(used), size(free)))
    else:
        lines.append("File system: not reported")
    if "ram_free" in info and "ram_used" in info:
        lines.append("RAM (heap): %s, %s used, %s free" % (
            size(info["ram_free"] + info["ram_used"]), size(info["ram_used"]), size(info["ram_free"])))
    else:
        lines.append("RAM: not reported")
    return "\n".join(lines)


def read(run):
    """`run(code)` -> bytes from the board."""
    return parse(run(MEMORY_CODE))
