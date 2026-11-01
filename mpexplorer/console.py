"""REPL console logic (GUI-free): runs typed lines on the device through the raw REPL."""
import codecs
import re

from .transport import DeviceError

_NOISE = re.compile(r'^\s*File "<(stdin|string)>".*\n?', re.M)
# Terminal codes the plain-text console cannot draw: CircuitPython sets the window title (OSC) and colours text (CSI).
_CODES = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"       # OSC ... BEL or ESC \ (the text is dropped too)
                    r"|\x1b\[[0-?]*[ -/]*[@-~]"                      # CSI
                    r"|\x1b[@-Z\\-_]"                               # two-character codes
                    r"|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")                # other control characters; \t \n \r stay
_PARTIAL = re.compile(r"\x1b(?:\](?:[^\x07\x1b]|\x1b(?!\\))*|\[[0-?]*[ -/]*)?$")      # a code cut off by the chunk end
MAX_CODE = 300


class CodeStripper:
    """Removes terminal codes from output that arrives in pieces; a code split between two pieces is held back."""

    def __init__(self):
        self.held = ""

    def feed(self, text):
        text = self.held + text
        self.held = ""
        m = _PARTIAL.search(text)
        if m and len(text) - m.start() <= MAX_CODE:
            text, self.held = text[:m.start()], text[m.start():]
        return _CODES.sub("", text)


def clean_traceback(tb):
    """Drop the frames that belong to the console plumbing, keep the user's."""
    text = _NOISE.sub("", tb.replace("\r", ""))
    return text.replace("Traceback (most recent call last):\n", "", 1) if "File " not in text else text


def build_code(line):
    """Expressions are evaluated and echoed like the real REPL; anything else runs as statements."""
    try:
        compile(line.strip(), "<console>", "eval")
    except SyntaxError:
        return line.rstrip() + "\n"
    return "_v=eval(%r)\nif _v is not None:\n print(repr(_v))\n _=_v\n" % line.strip()


class ConsoleSession:
    """State persists on the device between lines (the raw REPL keeps its globals)."""

    def __init__(self, repl):
        self.repl = repl
        self.history = []
        self._sent_input = False

    def run(self, line, on_output):
        """Run one line or block. on_output(text, is_error) is called as output arrives.
        Returns True on success, False if the device raised an exception."""
        if line.strip() and (not self.history or self.history[-1] != line):
            self.history.append(line)
        dec = codecs.getincrementaldecoder("utf-8")(errors="replace")
        strip = CodeStripper()

        def emit(chunk):
            text = strip.feed(dec.decode(chunk).replace("\r", ""))
            if text:
                on_output(text, False)
        self._sent_input = False
        try:
            self.repl.exec(build_code(line), None, emit)
        except DeviceError as e:
            on_output(clean_traceback(e.traceback).rstrip() + "\n", True)
            return False
        finally:
            if self._sent_input:
                self.repl.mark_dirty()      # anything the program did not read must not leak into the next run
        return True

    def interrupt(self):
        self.repl.interrupt()

    def send_input(self, text):
        """Answer an input() call in the running program."""
        self._sent_input = True
        self.repl.send_input(text)

    def soft_reset(self, on_output):
        """Like Ctrl-D at the REPL: restart the interpreter, losing all variables. Shows what the board prints while it
        starts (the banner, the output of main.py or code.py), without the prompt it ends on."""
        dec = codecs.getincrementaldecoder("utf-8")(errors="replace")
        strip = CodeStripper()
        shown = []

        def emit(chunk):
            text = strip.feed(dec.decode(chunk).replace("\r", ""))
            if text:
                shown.append(text)
        self.repl.soft_reset(emit)
        text = "".join(shown)
        if text.endswith(">>> "):
            text = text[:-4]
        text = text.strip("\n")
        on_output((text if text.strip() else "MPY: soft reboot") + "\n", False)
