import os
import shutil
import tempfile

from . import paths
from .ops import copy_tree, total_size


def copied_dir():
    return os.path.join(paths.cache_dir(), "copied")


class Clipboard:
    """Cut/copy buffer shared by both panes. Items are (backend, path)."""

    def __init__(self):
        self.mode = "copy"
        self.items = []
        self.folder = None      # holds copies of items from a board that has since been disconnected

    def set(self, mode, items):
        self._forget_copies()
        self.mode, self.items = mode, list(items)

    def clear(self):
        self._forget_copies()
        self.items = []

    def holds(self, backend):
        return any(be is backend for be, _path in self.items)

    def keep_copies(self, backend, local, root, prog, policy):
        """Download what was copied from a board about to be disconnected, into a new private folder under root.
        Returns (items, folder) to give to use_copies. Runs on the worker: it does not touch the clipboard."""
        items = list(self.items)
        os.makedirs(root, exist_ok=True)
        folder = tempfile.mkdtemp(prefix="copied-", dir=root)
        try:
            prog.total = sum(total_size(be, p) for be, p in items if be is backend)
            kept = []
            for i, (be, path) in enumerate(items):
                if be is not backend:
                    kept.append((be, path))
                    continue
                place = os.path.join(folder, str(i))    # two items can have one name, from different folders
                os.mkdir(place)
                dst = local.join(place, be.basename(path))
                copy_tree(be, path, local, dst, prog, policy)
                kept.append((local, dst))
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        return kept, folder

    def use_copies(self, items, folder):
        """The board is gone, so a cut can no longer remove the originals: from here it is a copy."""
        self._forget_copies()
        self.mode, self.items, self.folder = "copy", items, folder

    def _forget_copies(self):
        if self.folder:
            shutil.rmtree(self.folder, ignore_errors=True)
            self.folder = None
