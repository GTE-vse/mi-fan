"""Own Libre's lifetime, including its elevated process, without touching external instances."""
import ctypes
from ctypes import wintypes as wt
import os
from pathlib import Path
import subprocess
import sys
import uuid


class ShellExecuteInfo(ctypes.Structure):
    _fields_ = [('cbSize', wt.DWORD), ('fMask', wt.ULONG), ('hwnd', wt.HWND),
                ('lpVerb', wt.LPCWSTR), ('lpFile', wt.LPCWSTR),
                ('lpParameters', wt.LPCWSTR), ('lpDirectory', wt.LPCWSTR),
                ('nShow', ctypes.c_int), ('hInstApp', wt.HINSTANCE),
                ('lpIDList', ctypes.c_void_p), ('lpClass', wt.LPCWSTR),
                ('hkeyClass', wt.HKEY), ('dwHotKey', wt.DWORD),
                ('hIcon', wt.HANDLE), ('hProcess', wt.HANDLE)]


def kernel():
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    signatures = {
        'CreateEventW': ([ctypes.c_void_p, wt.BOOL, wt.BOOL, wt.LPCWSTR], wt.HANDLE),
        'OpenEventW': ([wt.DWORD, wt.BOOL, wt.LPCWSTR], wt.HANDLE),
        'SetEvent': ([wt.HANDLE], wt.BOOL),
        'CloseHandle': ([wt.HANDLE], wt.BOOL),
        'OpenProcess': ([wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE),
        'WaitForSingleObject': ([wt.HANDLE, wt.DWORD], wt.DWORD),
        'WaitForMultipleObjects': ([wt.DWORD, ctypes.POINTER(wt.HANDLE), wt.BOOL, wt.DWORD], wt.DWORD),
        'GetExitCodeProcess': ([wt.HANDLE, ctypes.POINTER(wt.DWORD)], wt.BOOL),
        'GetProcessTimes': ([wt.HANDLE] + [ctypes.POINTER(wt.FILETIME)] * 4, wt.BOOL),
    }
    for name, (args, result) in signatures.items():
        function = getattr(api, name)
        function.argtypes, function.restype = args, result
    return api


def creation_time(api, handle):
    times = [wt.FILETIME() for _ in range(4)]
    if not api.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
        raise ctypes.WinError(ctypes.get_last_error())
    return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime


def request_window_close(pid):
    """WM_CLOSE also reaches tray-hidden forms; only address this owned PID."""
    user = ctypes.WinDLL('user32', use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    user.EnumWindows.argtypes = [callback_type, wt.LPARAM]
    user.EnumWindows.restype = wt.BOOL
    user.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
    user.GetWindowThreadProcessId.restype = wt.DWORD
    user.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user.PostMessageW.restype = wt.BOOL

    @callback_type
    def visit(hwnd, unused):
        owner = wt.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            user.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE, not minimize-to-tray SC_CLOSE.
        return True

    user.EnumWindows(visit, 0)


def stop_child(child):
    if child.poll() is not None:
        return
    request_window_close(child.pid)
    try:
        child.wait(timeout=6)
    except subprocess.TimeoutExpired:
        # Popen retains the exact process handle, so a reused PID cannot be killed.
        child.terminate()
        child.wait(timeout=3)


def supervise(parent_pid, parent_created, event_name, executable, spawn=subprocess.Popen):
    """Run at Libre's privilege level so UIPI cannot block graceful shutdown."""
    api = kernel()
    event = api.OpenEventW(0x00100000, False, event_name)  # SYNCHRONIZE
    if not event:
        return 0  # Mi Fan already exited, e.g. while the UAC prompt was open.
    parent = None
    child = None
    try:
        parent = api.OpenProcess(0x00101000, False, parent_pid)
        if not parent or creation_time(api, parent) != parent_created:
            return 0
        handles = (wt.HANDLE * 2)(event, parent)
        if api.WaitForMultipleObjects(2, handles, False, 0) != 258:
            return 0
        # The helper is already elevated. No second UAC prompt is needed for Libre.
        child = spawn([str(executable)], cwd=str(Path(executable).parent),
                      creationflags=subprocess.CREATE_NO_WINDOW)
        handles = (wt.HANDLE * 3)(event, parent, int(child._handle))
        result = api.WaitForMultipleObjects(3, handles, False, 0xFFFFFFFF)
        if result == 0xFFFFFFFF:
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        try:
            if child is not None:
                stop_child(child)
        finally:
            if parent:
                api.CloseHandle(parent)
            api.CloseHandle(event)
    return 0


class ManagedLibre:
    def __init__(self, api, event, process):
        self.api, self.event, self.process = api, event, process

    @classmethod
    def launch(cls, executable):
        api = kernel()
        event_name = 'Local\\MiFanLibre-' + uuid.uuid4().hex
        event = api.CreateEventW(None, True, False, event_name)
        if not event:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            parent = api.OpenProcess(0x1000, False, os.getpid())
            if not parent:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                created = creation_time(api, parent)
            finally:
                api.CloseHandle(parent)
            main = Path(__file__).with_name('main.py')
            args = [] if getattr(sys, 'frozen', False) else [str(main)]
            args += ['--manage-libre', str(os.getpid()), str(created), event_name,
                     str(Path(executable).resolve())]
            info = ShellExecuteInfo()
            info.cbSize = ctypes.sizeof(info)
            info.fMask = 0x00000040 | 0x00000100 | 0x00000400  # process handle, no async, no error UI
            info.lpVerb = 'runas'
            info.lpFile = sys.executable
            info.lpParameters = subprocess.list2cmdline(args)
            info.lpDirectory = str(Path(executable).resolve().parent)
            info.nShow = 0
            shell = ctypes.WinDLL('shell32', use_last_error=True).ShellExecuteExW
            shell.argtypes, shell.restype = [ctypes.POINTER(ShellExecuteInfo)], wt.BOOL
            if not shell(ctypes.byref(info)) or not info.hProcess:
                error = ctypes.get_last_error()
                if error == 1223:
                    raise RuntimeError('已取消 Libre 启动授权，可点击“重连温度”重试。')
                raise ctypes.WinError(error)
            return cls(api, event, info.hProcess)
        except Exception:
            api.CloseHandle(event)
            raise

    def close(self):
        if self.process is None:
            return
        if not self.api.SetEvent(self.event):
            raise ctypes.WinError(ctypes.get_last_error())
        result = self.api.WaitForSingleObject(self.process, 12000)
        if result == 258:
            raise RuntimeError('正在等待 Libre 退出，请稍后再关闭 Mi Fan。')
        if result != 0:
            raise ctypes.WinError(ctypes.get_last_error())
        code = wt.DWORD()
        if not self.api.GetExitCodeProcess(self.process, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        self.api.CloseHandle(self.process)
        self.api.CloseHandle(self.event)
        self.process = self.event = None
        if code.value:
            raise RuntimeError('Libre 管理进程异常退出，请检查 Libre 是否仍在托盘运行。')
