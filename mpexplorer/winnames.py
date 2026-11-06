"""Names and drives that only matter on Windows (docs/WINDOWS_PLAN.md). Pure functions, so Linux tests can run them."""
import ntpath
import re
import sys

RESERVED = {"CON", "PRN", "AUX", "NUL"} | {"COM%d" % i for i in range(1, 10)} | {"LPT%d" % i for i in range(1, 10)}
BAD_CHARS = re.compile(r'[<>:"/\|?*\x00-\x1f]')
MAX_PATH = 260
LONG_PREFIX = "\\\\?\\"


def is_windows(platform=None):
    return (platform or sys.platform).startswith("win")


def name_problem(name):
    """Why Windows would refuse this file or folder name, as a short sentence, or None when it is fine."""
    if not name or name in (".", ".."):
        return None
    m = BAD_CHARS.search(name)
    if m:
        ch = m.group()
        return "the name contains %s, which Windows does not allow" % (repr(ch) if ord(ch) < 32 else "'%s'" % ch)
    if name.endswith((".", " ")):
        return "Windows does not allow a name that ends with a dot or a space"
    if name.split(".")[0].rstrip(" ").upper() in RESERVED:
        return "'%s' is a reserved device name on Windows" % name.split(".")[0]
    return None


def path_problem(path):
    """Why a whole path will not work on Windows (a bad part, or too long without the long-path prefix), or None."""
    drive, rest = ntpath.splitdrive(path)
    for part in rest.replace("/", "\\").split("\\"):
        why = name_problem(part)
        if why:
            return why + " ('%s')" % part
    return None


def case_clashes(names):
    """Groups of names that differ only in letter case: Windows would treat them as one file."""
    seen = {}
    for n in names:
        seen.setdefault(n.lower(), []).append(n)
    return [group for group in seen.values() if len(group) > 1]


def long_path(path):
    """The long-path form (prefix backslash backslash ? backslash) of an absolute Windows path, which lifts the
    260 character limit. Short paths are returned as they are."""
    if path.startswith(LONG_PREFIX) or len(path) < MAX_PATH - 12:
        return path
    full = ntpath.abspath(path)
    if full.startswith("\\\\"):
        return LONG_PREFIX + "UNC\\" + full[2:]
    return LONG_PREFIX + full


def parent_path(p, pathmod=None):
    """The folder above p; a root (slash, or a drive letter root) is its own parent."""
    import os
    pm = pathmod or os.path
    drive, rest = pm.splitdrive(p)
    stripped = rest.rstrip(pm.sep + (pm.altsep or ""))
    if not stripped:
        return drive + pm.sep
    return pm.dirname(drive + stripped) or drive + pm.sep


def list_drives(bitmask=None, label_of=None):
    """[(root, volume label)] for each drive letter in use, e.g. [('C:\\', 'Windows'), ('E:\\', 'CIRCUITPY')].
    bitmask and label_of are for tests; by default they ask Windows."""
    if bitmask is None:
        import ctypes
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    if label_of is None:
        label_of = volume_label
    roots = ["%s:\\" % chr(ord("A") + i) for i in range(26) if bitmask & (1 << i)]
    return [(r, label_of(r)) for r in roots]


DRIVE_REMOVABLE = 2     # GetDriveTypeW: USB sticks, and the UF2 / CIRCUITPY drives boards show


def drive_type(root):
    try:
        import ctypes
        return ctypes.windll.kernel32.GetDriveTypeW(root)
    except (AttributeError, OSError):
        return 0


def removable_drives(drives=None, type_of=None):
    """[(root, label)] of removable drives (where UF2 bootloaders and CIRCUITPY appear). A: and B: are never asked,
    so an old floppy drive cannot stall a scan. drives and type_of are for tests."""
    type_of = type_of or drive_type
    drives = list_drives() if drives is None else drives
    return [(r, label) for r, label in drives if r[0].upper() not in "AB" and type_of(r) == DRIVE_REMOVABLE]


def volume_label(root):
    """The volume label of a drive root, or an empty string."""
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(261)
        ok = ctypes.windll.kernel32.GetVolumeInformationW(root, buf, 261, None, None, None, None, 0)
        return buf.value if ok else ""
    except (AttributeError, OSError):
        return ""
