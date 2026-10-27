"""A minimal terminal screen: applies what the MicroPython REPL sends (GUI-free, unit-tested).

The REPL's line editor only needs a handful of controls: CR, LF, backspace, and the CSI sequences for erasing and
moving the cursor. Lines are unbounded (the board does not know the window width), so this is a scrollback of
logical lines plus a cursor, not a fixed grid. Colours and other formatting are accepted and ignored.
"""
import codecs

_PARAM_CHARS = "0123456789;?<=>! "


class AnsiScreen:
    def __init__(self, max_lines=1000):
        self.max_lines = max_lines
        self.lines = [""]
        self.row = 0
        self.col = 0
        self.dirty = True
        self._state = "text"            # text | esc | csi | osc | osc_esc | ss3
        self._params = ""
        self._dec = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def text(self):
        return "\n".join(self.lines)

    def feed(self, data):
        for ch in self._dec.decode(data):
            self._char(ch)
        self.dirty = True

    # -- character handling ------------------------------------------------
    def _char(self, ch):
        st = self._state
        if st == "text":
            if ch == "\x1b":
                self._state = "esc"
            elif ch == "\r":
                self.col = 0
            elif ch == "\n":
                self._newline()
            elif ch == "\b":
                self.col = max(0, self.col - 1)
            elif ch == "\t":
                self.col = (self.col // 8 + 1) * 8
            elif ch >= " " and ch != "\x7f":
                self._put(ch)
        elif st == "esc":
            self._state = {"[": "csi", "]": "osc", "O": "ss3", "P": "osc", "_": "osc", "^": "osc"}.get(ch, "text")
            self._params = ""
        elif st == "csi":
            if ch in _PARAM_CHARS:
                self._params += ch
            else:
                self._state = "text"
                if "@" <= ch <= "~":
                    self._csi(self._params, ch)
        elif st == "osc":                   # operating system command: skip until BEL or ESC \
            if ch == "\x07":
                self._state = "text"
            elif ch == "\x1b":
                self._state = "osc_esc"
        elif st == "osc_esc":
            self._state = "text"
        elif st == "ss3":                   # ESC O x (keypad/function keys): one more byte
            self._state = "text"

    def _newline(self):
        self.row += 1
        if self.row >= len(self.lines):
            self.lines.append("")
        if len(self.lines) > self.max_lines:
            del self.lines[0]
            self.row -= 1

    def _put(self, ch):
        line = self.lines[self.row]
        if self.col > len(line):
            line = line.ljust(self.col)
        self.lines[self.row] = line[:self.col] + ch + line[self.col + 1:]
        self.col += 1

    # -- CSI sequences -----------------------------------------------------
    def _csi(self, params, final):
        if params.startswith(("?", "<", "=", ">")):
            return                          # private modes (cursor visibility, bracketed paste...)
        nums = [int(p) if p.strip().isdigit() else None for p in params.split(";")]
        n = nums[0] if nums and nums[0] is not None else None
        line = self.lines[self.row]
        if final == "K":
            if n in (None, 0):
                self.lines[self.row] = line[:self.col]
            elif n == 1:
                self.lines[self.row] = " " * min(self.col + 1, len(line)) + line[self.col + 1:]
            elif n == 2:
                self.lines[self.row] = ""
        elif final == "J":
            if n in (None, 0):
                self.lines[self.row] = line[:self.col]
                del self.lines[self.row + 1:]
            elif n in (2, 3):
                self.lines, self.row, self.col = [""], 0, 0
        elif final == "A":
            self.row = max(0, self.row - (n or 1))
        elif final == "B":
            self.row = min(len(self.lines) - 1, self.row + (n or 1))
        elif final == "C":
            self.col += n or 1
        elif final == "D":
            self.col = max(0, self.col - (n or 1))
        elif final in ("H", "f"):
            col = nums[1] if len(nums) > 1 and nums[1] is not None else 1
            self.col = max(0, col - 1)      # rows are not addressable in a scrollback; column only
        elif final == "G":
            self.col = max(0, (n or 1) - 1)
        elif final == "P":
            self.lines[self.row] = line[:self.col] + line[self.col + (n or 1):]
        elif final == "@":
            self.lines[self.row] = line[:self.col] + " " * (n or 1) + line[self.col:]
        elif final == "X":
            k = n or 1
            self.lines[self.row] = line[:self.col] + " " * min(k, max(0, len(line) - self.col)) + line[self.col + k:]
        # everything else (SGR colours 'm', scroll regions, modes...) is ignored
