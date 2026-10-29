"""A status screen for badges, drawn while mpexplorer is connected (GUI-free).

Connecting interrupts the badge OS (Ctrl-C), so its screen would stay frozen on the last frame. This draws a small
status page instead: that mpexplorer is connected, to which computer, the folder being browsed, the free space and what
is happening. Like every other operation it is a snippet sent through the raw REPL and run in RAM: nothing is installed.
Boards without the Tildagon `display.get_ctx()` API (a Pico, the 2022 badge) are detected on the first attempt and left
alone.
"""
import socket
import time

LINE = 20       # characters that fit the 240 px round screen at font size 17
GREEN, AMBER, RED, GREY = (0.2, 0.9, 0.5), (1.0, 0.8, 0.3), (1.0, 0.35, 0.3), (0.75, 0.8, 0.9)

# kind -> (dot colour, headline, headline colour, status colour, show the folder and free space)
KINDS = {
    "idle": (GREEN, "CONNECTED", GREEN, GREY, True),
    "busy": (AMBER, "CONNECTED", GREEN, AMBER, True),
    "gone": (RED, "DISCONNECTED", RED, (1.0, 1.0, 1.0), False),
}


def fit(text, n=LINE, tail=False):
    """Printable text shortened to n characters with '..' (the end of a path is the useful part, so tail=True)."""
    text = "".join(c for c in str(text) if c >= " ")
    if len(text) <= n:
        return text
    return ".." + text[-(n - 2):] if tail else text[:n - 2] + ".."


def screen_code(kind, host, folder, status, since):
    dot, head, head_col, status_col, details = KINDS[kind]
    return (
        "try:\n import display\n ctx=display.get_ctx()\nexcept Exception:\n ctx=None\n"
        "if ctx is None:\n print('nodisplay')\nelse:\n"
        " free=''\n"
        " if %(details)r:\n"
        "  try:\n   import os\n   s=os.statvfs(%(folder)r)\n   free='%%.1f MB free'%%(s[0]*s[3]/1048576)\n"
        "  except Exception:\n   pass\n"
        " ctx.rgb(0.04,0.05,0.12).rectangle(-120,-120,240,240).fill()\n"
        " ctx.text_align=ctx.CENTER\n ctx.text_baseline=ctx.MIDDLE\n"
        " ctx.rgb(*%(dot)r).arc(0,-78,9,0,6.2832,0).fill()\n"
        " def t(y,size,col,s):\n  ctx.rgb(*col)\n  ctx.font_size=size\n  ctx.move_to(0,y).text(s)\n"
        " t(-48,26,(1,1,1),'mpexplorer')\n"
        " t(-22,20,%(head_col)r,%(head)r)\n"
        " t(6,17,%(grey)r,%(host)r)\n"
        " if %(details)r:\n"
        "  t(28,17,%(grey)r,%(folder_text)r)\n"
        "  t(50,17,%(grey)r,free)\n"
        " t(80,18,%(status_col)r,%(status)r)\n"
        " t(102,14,%(grey)r,%(since)r)\n"
        " display.end_frame(ctx)\n print('ok')\n"
    ) % {"details": details, "folder": folder, "dot": dot, "head": head, "head_col": head_col, "grey": GREY,
         "host": host, "folder_text": fit(folder, LINE, tail=True), "status_col": status_col,
         "status": fit(status), "since": since}


class BadgeScreen:
    """Draws the status page through `repl`. Call from whoever owns the serial line (the job runner's thread)."""

    def __init__(self, repl, host=None):
        self.repl = repl
        self.host = fit(host or socket.gethostname())
        self.since = "since " + time.strftime("%H:%M")
        self.folder = "/"
        self.status = "Ready"
        self.available = None       # None = not tried yet, False = this board has no usable screen
        self._last = None

    def show(self, status=None, folder=None, kind="idle"):
        """Redraw if anything changed. Returns True when the screen was drawn."""
        if status is not None:
            self.status = status
        if folder is not None:
            self.folder = folder
        state = (kind, self.status, self.folder)
        if self.available is False or state == self._last:
            return False
        try:
            out = self.repl.exec(screen_code(kind, self.host, self.folder, self.status, self.since), timeout=10)
        except Exception:
            return False            # line busy (terminal mode), board gone, ...: try again next time
        self.available = out.strip() == b"ok"
        self._last = state if self.available else None
        return self.available
