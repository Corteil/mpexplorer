"""A stand-in for the badge's `display` module: records the text of every frame the status screen draws."""
import sys
import types


class Ctx:
    CENTER = MIDDLE = 1

    def __init__(self, rec):
        self.rec, self.texts = rec, []
        self.text_align = self.text_baseline = self.font_size = None

    def _chain(self, *a):
        return self

    rgb = rectangle = fill = arc = move_to = _chain

    def text(self, s):
        self.texts.append(s)
        return self


class Recorder:
    def __init__(self):
        self.frames = []

    @property
    def last(self):
        return self.frames[-1] if self.frames else []

    def all_text(self):
        return " | ".join(" ".join(f) for f in self.frames)


def install(monkeypatch):
    rec = Recorder()
    display = types.ModuleType("display")
    display.get_ctx = lambda: Ctx(rec)
    display.end_frame = lambda ctx: rec.frames.append(list(ctx.texts))
    monkeypatch.setitem(sys.modules, "display", display)
    return rec
