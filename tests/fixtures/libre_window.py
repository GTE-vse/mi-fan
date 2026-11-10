"""Invisible Win32 window used instead of Libre; never reads hardware."""
import ctypes
from ctypes import wintypes as wt
from pathlib import Path
import sys
import time

user = ctypes.WinDLL('user32', use_last_error=True)
user.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                wt.HWND, wt.HMENU, wt.HINSTANCE, ctypes.c_void_p]
user.CreateWindowExW.restype = wt.HWND
user.PeekMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT, wt.UINT]
user.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
user.DispatchMessageW.restype = wt.LPARAM
user.IsWindow.argtypes = [wt.HWND]
hwnd = user.CreateWindowExW(0, 'STATIC', 'AirLink lifecycle test', 0,
                            0, 0, 1, 1, None, None, None, None)
if not hwnd:
    raise ctypes.WinError(ctypes.get_last_error())
Path(sys.argv[1]).write_text('ready')
message = wt.MSG()
deadline = time.monotonic() + 20
while user.IsWindow(hwnd) and time.monotonic() < deadline:
    while user.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
        user.DispatchMessageW(ctypes.byref(message))
    time.sleep(0.01)
if not user.IsWindow(hwnd):
    Path(sys.argv[2]).write_text('graceful')
else:
    raise SystemExit(2)
