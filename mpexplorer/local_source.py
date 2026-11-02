"""Libraries from a folder or a .zip on this computer (the owner's own code). No network, nothing runs on the host.

Same shape as `libraries.Catalog` (libraries / resolve / fetch), so `libraries.install`, `remove` and `compare` work
with it. Each top-level `.py` / `.mpy` file is one library, and each top-level folder is a package with everything
under it (hidden files and `__pycache__` are left out). A zip whose files all sit in one folder is read as that folder.
"""
import os
import posixpath
import zipfile

from .libraries import Library, LibraryError

KEEP = (".py", ".mpy")
MAX_FILE = 2 << 20
MAX_FILES = 200
SKIP = ("__pycache__", "__MACOSX")


def _skipped(parts):
    return any(p.startswith(".") or p in SKIP for p in parts)


class LocalFolder:
    """`source` is {name, path}: a folder, or a .zip file."""

    def __init__(self, source, cache_dir=None, opener=None):
        self.source = source
        self.title = source["name"]
        self.path = os.path.expanduser(source["path"])
        self.name = self.title
        self.external = []
        self.is_zip = self.path.lower().endswith(".zip")

    # -- reading what is there ------------------------------------------------------------------------------
    def _files(self):
        """{relative path: size} of every library file."""
        try:
            if self.is_zip:
                with zipfile.ZipFile(self.path) as z:
                    names = [(i.filename, i.file_size) for i in z.infolist() if not i.is_dir()]
                tops = {n.split("/")[0] for n, _ in names}
                strip = len(tops) == 1 and all("/" in n for n, _ in names)
                found = {}
                for name, size in names:
                    rel = name.split("/", 1)[1] if strip else name
                    if rel.endswith(KEEP) and not _skipped(rel.split("/")):
                        found[rel] = size
                return found
            if not os.path.isdir(self.path):
                raise LibraryError("%s is not a folder or a .zip file." % self.path)
            found = {}
            for root, dirs, files in os.walk(self.path):
                dirs[:] = [d for d in dirs if not _skipped([d])]
                for f in files:
                    if f.endswith(KEEP) and not _skipped([f]):
                        full = os.path.join(root, f)
                        found[os.path.relpath(full, self.path).replace(os.sep, "/")] = os.path.getsize(full)
            return found
        except (OSError, zipfile.BadZipFile) as e:
            raise LibraryError("Cannot read %s: %s" % (self.path, e))

    def _by_name(self):
        """{library name: [relative paths]}."""
        out = {}
        for rel in sorted(self._files()):
            top = rel.split("/")[0]
            name = top if "/" in rel else os.path.splitext(top)[0]
            out.setdefault(name, []).append(rel)
        return out

    # -- the catalog interface ------------------------------------------------------------------------------
    def libraries(self):
        libs = [Library(name, "", "%d file%s from %s" % (len(files), "" if len(files) == 1 else "s",
                                                         os.path.basename(self.path.rstrip(os.sep))))
                for name, files in self._by_name().items()]
        return libs, None

    def resolve(self, names, mpy=None):
        """[(name, '', 'py', [(path under /lib, key)])]; the key is the path inside the folder or zip."""
        have = self._by_name()
        out = []
        for n in names:
            if n not in have:
                raise LibraryError("%s is not in %s." % (n, self.title))
            files = have[n]
            if len(files) > MAX_FILES:
                raise LibraryError("%s has %d files; the limit is %d." % (n, len(files), MAX_FILES))
            out.append((n, "", "py", [(rel, rel) for rel in files]))
        return out

    def fetch(self, key):
        if key.startswith("/") or ".." in key.split("/") or posixpath.normpath(key) != key:
            raise LibraryError("Bad file path: %r" % key)
        size = self._files().get(key)
        if size is None:
            raise LibraryError("%s is no longer in %s." % (key, self.title))
        if size > MAX_FILE:
            raise LibraryError("%s is larger than %d MB." % (key, MAX_FILE >> 20))
        try:
            if self.is_zip:
                with zipfile.ZipFile(self.path) as z:
                    names = [i.filename for i in z.infolist() if not i.is_dir()]
                    tops = {n.split("/")[0] for n in names}
                    strip = len(tops) == 1 and all("/" in n for n in names)
                    member = (names[0].split("/")[0] + "/" + key) if strip else key
                    return z.read(member)
            with open(os.path.join(self.path, *key.split("/")), "rb") as f:
                return f.read()
        except (OSError, KeyError, zipfile.BadZipFile) as e:
            raise LibraryError("Cannot read %s: %s" % (key, e))
