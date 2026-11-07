"""Run this Python on a hidden Windows desktop, so the GUI tests' windows do not appear on the owner's screen (what
xvfb-run does on Linux). The arguments go to the same Python; its output still comes here and its exit code is
passed on.

    python packaging/windows/hidden_desktop.py -m pytest -q
    python packaging/windows/hidden_desktop.py D:\\build\\mpexplorer\\mpexplorer-console.exe --self-test
"""
import ctypes
import subprocess
import sys
from ctypes import wintypes

DESKTOP_ALL = 0x01FF                    # every DESKTOP_* access right
STARTF_USESTDHANDLES = 0x100
INFINITE = 0xFFFFFFFF


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR),
                ("lpTitle", wintypes.LPWSTR), ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD), ("dwXCountChars", wintypes.DWORD),
                ("dwYCountChars", wintypes.DWORD), ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD), ("lpReserved2", ctypes.c_void_p),
                ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE), ("dwProcessId", wintypes.DWORD),
                ("dwThreadId", wintypes.DWORD)]


def _std_handle(stream, which):
    """An inheritable handle for one of our std streams (a pipe under MSYS2's bash, or a console)."""
    import msvcrt
    try:
        handle = msvcrt.get_osfhandle(stream.fileno())
    except (OSError, ValueError, AttributeError):
        handle = ctypes.windll.kernel32.GetStdHandle(which)
    ctypes.windll.kernel32.SetHandleInformation(wintypes.HANDLE(handle), 1, 1)      # HANDLE_FLAG_INHERIT
    return handle


def run(argv, name="mpexplorer-tests"):
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    user32.CreateDesktopW.restype = wintypes.HANDLE
    desk = user32.CreateDesktopW(name, None, None, 0, DESKTOP_ALL, None)
    if not desk:
        raise OSError("CreateDesktop failed: %d" % kernel32.GetLastError())
    try:
        si = STARTUPINFOW()
        si.cb = ctypes.sizeof(si)
        si.lpDesktop = name
        si.dwFlags = STARTF_USESTDHANDLES
        si.hStdInput = _std_handle(sys.stdin, -10)
        si.hStdOutput = _std_handle(sys.stdout, -11)
        si.hStdError = _std_handle(sys.stderr, -12)
        pi = PROCESS_INFORMATION()
        cmd = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
        if not kernel32.CreateProcessW(None, cmd, None, None, True, 0, None, None, ctypes.byref(si),
                                       ctypes.byref(pi)):
            raise OSError("CreateProcess failed: %d" % kernel32.GetLastError())
        kernel32.WaitForSingleObject(pi.hProcess, INFINITE)
        code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(pi.hProcess, ctypes.byref(code))
        kernel32.CloseHandle(pi.hThread)
        kernel32.CloseHandle(pi.hProcess)
        return code.value
    finally:
        user32.CloseDesktop(desk)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    sys.stdout.flush()
    args = sys.argv[1:]
    exe = args[0].lower().endswith(".exe")          # a program (the built mpexplorer-console.exe), else Python args
    sys.exit(run(args if exe else [sys.executable] + args))
