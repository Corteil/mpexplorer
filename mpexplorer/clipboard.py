class Clipboard:
    """Cut/copy buffer shared by both panes. Items are (backend, path)."""

    def __init__(self):
        self.mode = "copy"
        self.items = []

    def set(self, mode, items):
        self.mode, self.items = mode, list(items)

    def clear(self):
        self.items = []
