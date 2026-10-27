"""One-way folder sync: make the destination match the source, copying only what changed."""
import fnmatch
from dataclasses import dataclass, field

from .errors import BackendError
from .ops import _copy_file

DEFAULT_EXCLUDE = ("__pycache__", ".git", "*.pyc", ".DS_Store")
TMP_SUFFIX = ".mpx-part"


@dataclass
class Action:
    kind: str                   # mkdir | copy | delete
    rel: str                    # path relative to the synced folder, "/" separated ("" = the folder itself)
    is_dir: bool = False
    size: int = 0
    reason: str = ""            # new | changed | extra


@dataclass
class Plan:
    src_be: object
    src: str
    dst_be: object
    dst: str
    actions: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    unchanged: int = 0

    def of(self, kind):
        return [a for a in self.actions if a.kind == kind]

    def bytes_to_copy(self):
        return sum(a.size for a in self.of("copy"))


def join_rel(be, root, rel):
    for part in filter(None, rel.split("/")):
        root = be.join(root, part)
    return root


def walk(be, root, exclude, prog=None):
    """{relative path: Entry} for everything under root, skipping excluded names."""
    out = {}

    def rec(path, rel):
        if prog:
            prog.check()
            prog.label = "Scanning " + (rel or be.basename(root) or "/")
        for e in be.listdir(path):
            if any(fnmatch.fnmatch(e.name, pat) for pat in exclude):
                continue
            r = rel + "/" + e.name if rel else e.name
            out[r] = e
            if e.is_dir:
                rec(be.join(path, e.name), r)
    if be.stat(root) is not None:
        rec(root, "")
    return out


def build_plan(src_be, src, dst_be, dst, prog=None, use_checksum=True, exclude=DEFAULT_EXCLUDE):
    plan = Plan(src_be, src, dst_be, dst)
    s_items = walk(src_be, src, exclude, prog)
    d_items = walk(dst_be, dst, exclude, prog)
    if dst_be.stat(dst) is None:
        plan.actions.append(Action("mkdir", "", True, reason="new"))
    no_hash = False
    for rel in sorted(s_items):
        se, de = s_items[rel], d_items.get(rel)
        if prog:
            prog.check()
            prog.label = "Comparing " + rel
        if de is not None and se.is_dir != de.is_dir:
            raise BackendError("'%s' is a %s in the source but a %s in the destination" % (
                rel, "folder" if se.is_dir else "file", "folder" if de.is_dir else "file"))
        if se.is_dir:
            if de is None:
                plan.actions.append(Action("mkdir", rel, True, reason="new"))
            continue
        if de is None:
            plan.actions.append(Action("copy", rel, False, se.size, "new"))
        elif se.size != de.size:
            plan.actions.append(Action("copy", rel, False, se.size, "changed"))
        elif use_checksum:
            a = src_be.checksum(join_rel(src_be, src, rel))
            b = dst_be.checksum(join_rel(dst_be, dst, rel))
            if a is None or b is None:
                no_hash = True
                plan.unchanged += 1
            elif a != b:
                plan.actions.append(Action("copy", rel, False, se.size, "changed"))
            else:
                plan.unchanged += 1
        else:
            plan.unchanged += 1
    if no_hash:
        plan.notes.append("A device has no hashlib: files with the same size are assumed identical.")
    for rel in sorted(set(d_items) - set(s_items), key=lambda r: (-r.count("/"), r)):
        e = d_items[rel]
        plan.actions.append(Action("delete", rel, e.is_dir, e.size, "extra"))
    return plan


def apply_plan(plan, prog, include_delete=False):
    """Run the plan: folders, then files (via a temp name so a cancel never loses the old copy), then deletes."""
    mkdirs = sorted(plan.of("mkdir"), key=lambda a: a.rel.count("/") if a.rel else -1)
    copies = plan.of("copy")
    prog.total = sum(a.size for a in copies)
    for a in mkdirs:
        prog.check()
        plan.dst_be.mkdir(join_rel(plan.dst_be, plan.dst, a.rel))
    for a in copies:
        prog.check()
        src = join_rel(plan.src_be, plan.src, a.rel)
        dst = join_rel(plan.dst_be, plan.dst, a.rel)
        tmp = dst + TMP_SUFFIX
        _copy_file(plan.src_be, src, plan.dst_be, tmp, prog)
        if plan.dst_be.stat(dst) is not None:
            plan.dst_be.remove(dst)             # FAT cannot rename over an existing file
        plan.dst_be.rename(tmp, dst)
    if include_delete:
        for a in plan.of("delete"):                  # already ordered deepest first
            prog.check()
            p = join_rel(plan.dst_be, plan.dst, a.rel)
            (plan.dst_be.rmdir if a.is_dir else plan.dst_be.remove)(p)
