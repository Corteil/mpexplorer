"""Ask the window manager to activate a window (what a taskbar click does), via X11 through ctypes.

Tk's deiconify() doesn't bring back a minimised window under labwc/XWayland. Best effort: returns False when libX11
or the display isn't there (other platforms), and the caller falls back to Tk.
"""
import ctypes
import ctypes.util


class _Data(ctypes.Union):
    _fields_ = [("l", ctypes.c_long * 5)]


class _ClientMessage(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong), ("send_event", ctypes.c_int),
                ("display", ctypes.c_void_p), ("window", ctypes.c_ulong), ("message_type", ctypes.c_ulong),
                ("format", ctypes.c_int), ("data", _Data)]


class _Event(ctypes.Union):
    _fields_ = [("xclient", _ClientMessage), ("pad", ctypes.c_long * 24)]


def activate(window_id):
    """Send _NET_ACTIVE_WINDOW for a Tk window's id; True if the request was sent.

    The window manager manages Tk's wrapper window (the parent of winfo_id()), not the id Tk reports.
    """
    name = ctypes.util.find_library("X11")
    if not name:
        return False
    try:
        x = ctypes.CDLL(name)
        x.XOpenDisplay.restype = ctypes.c_void_p
        x.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x.XDefaultRootWindow.restype = ctypes.c_ulong
        x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x.XInternAtom.restype = ctypes.c_ulong
        x.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        x.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_long, ctypes.c_void_p]
        x.XFlush.argtypes = [ctypes.c_void_p]
        x.XQueryTree.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
                                 ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_void_p),
                                 ctypes.POINTER(ctypes.c_uint)]
        x.XFree.argtypes = [ctypes.c_void_p]
        x.XCloseDisplay.argtypes = [ctypes.c_void_p]
        dpy = x.XOpenDisplay(None)
        if not dpy:
            return False
        try:
            root, parent, kids, n = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_void_p(), ctypes.c_uint()
            if x.XQueryTree(dpy, window_id, ctypes.byref(root), ctypes.byref(parent), ctypes.byref(kids),
                            ctypes.byref(n)):
                if kids:
                    x.XFree(kids)
                if parent.value and parent.value != root.value:
                    window_id = parent.value
            ev = _Event()
            m = ev.xclient
            m.type, m.send_event, m.display, m.window, m.format = 33, 1, dpy, window_id, 32     # ClientMessage
            m.message_type = x.XInternAtom(dpy, b"_NET_ACTIVE_WINDOW", 0)
            m.data.l[0] = 2                                  # source indication: a pager/taskbar
            root = x.XDefaultRootWindow(dpy)
            mask = (1 << 20) | (1 << 19)                    # SubstructureRedirect | SubstructureNotify
            x.XSendEvent(dpy, root, 0, mask, ctypes.byref(ev))
            x.XFlush(dpy)
            return True
        finally:
            x.XCloseDisplay(dpy)
    except (OSError, AttributeError):
        return False
