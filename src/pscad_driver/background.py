"""Launch the automation-owned PSCAD process on a private Windows desktop.

Never switches the user's input desktop. PSCAD and its child windows cannot
activate on the user's desktop, even when the application calls SetForegroundWindow.
"""
import ctypes
from ctypes import wintypes
import os
import subprocess
import time

import win32api
import win32con
import win32event
import win32process

from mhrc.automation.pscad import PSCAD

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.CreateDesktopW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p,
                                  wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
_user32.CreateDesktopW.restype = wintypes.HANDLE
_user32.CloseDesktop.argtypes = [wintypes.HANDLE]
_user32.CloseDesktop.restype = wintypes.BOOL


class _NativeProcess:
    """The three process operations used by mhrc's PSCAD lifecycle."""
    def __init__(self, args, startup):
        self.args = args
        self.handle, thread, self.pid, _ = win32process.CreateProcess(
            None, subprocess.list2cmdline(args), None, None, False,
            win32con.CREATE_NO_WINDOW, None, None, startup)
        thread.Close()

    def wait(self, timeout=None):
        milliseconds = win32event.INFINITE if timeout is None else int(timeout * 1000)
        if win32event.WaitForSingleObject(self.handle, milliseconds) == win32event.WAIT_TIMEOUT:
            raise subprocess.TimeoutExpired(self.args, timeout)
        return win32process.GetExitCodeProcess(self.handle)

    def terminate(self):
        win32api.TerminateProcess(self.handle, 1)

    def communicate(self):
        self.wait()
        return None, None


class BackgroundPSCAD(PSCAD):
    def _launch(self, port, options):
        self._desktop_name = "PSCAD_AI_%d_%d" % (os.getpid(), time.monotonic_ns())
        # Standard desktop rights, scoped to this newly created desktop.
        self._desktop_handle = _user32.CreateDesktopW(self._desktop_name, None, None, 0, 0x000F01FF, None)
        if not self._desktop_handle:
            raise ctypes.WinError(ctypes.get_last_error())
        args = [options["path"], "/startup:au", "/host:localhost", "/port:" + str(port), "/nologo"]
        if "/silence" in options:
            args.append("/silence:{}".format(options["/silence"]))
        # Python subprocess.STARTUPINFO silently ignores an assigned lpDesktop.
        # Use the native pywin32 structure and CreateProcess so Windows receives it.
        startup = win32process.STARTUPINFO()
        startup.lpDesktop = "WinSta0\\" + self._desktop_name
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0  # SW_HIDE
        try:
            self._proc = _NativeProcess(args, startup)
        except Exception:
            self.close_desktop()
            raise

    def close_desktop(self):
        handle = getattr(self, "_desktop_handle", None)
        if handle:
            if not _user32.CloseDesktop(handle):
                raise ctypes.WinError(ctypes.get_last_error())
            self._desktop_handle = None
