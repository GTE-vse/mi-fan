"""Check prerequisites before importing pythonnet; bootstrap only with user consent."""
import ctypes
from ctypes import wintypes as wt
import hashlib
from pathlib import Path
import sys
import tempfile
import winreg

from libre_process import ShellExecuteInfo, kernel

INSTALLERS = {
    'net': ('ndp48-web.exe', '0bba3094588c4bfec301939985222a20b340bf03431563dec8b2b4478b06fffa', '.NET Framework 4.8'),
    'webview': ('MicrosoftEdgeWebview2Setup.exe', '81c01751c8cc385a5991abb104205d42ac70094350ee8fb9e8ea580b51bb9554', 'Microsoft Edge WebView2'),
}


def registry_value(hive, path, name, view):
    try:
        with winreg.OpenKey(hive, path, 0, winreg.KEY_READ | view) as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def prerequisites():
    net = False
    webview = False
    for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
        release = registry_value(winreg.HKEY_LOCAL_MACHINE,
            r'SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full', 'Release', view)
        net |= isinstance(release, int) and release >= 461808
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            version = registry_value(hive,
                r'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', view)
            try:
                webview |= int(str(version).split('.')[0]) >= 86
            except ValueError:
                pass
    return {'net': net, 'webview': webview}


def message(text, question=False):
    return ctypes.windll.user32.MessageBoxW(None, text, 'Mi Fan 首次启动', 0x24 if question else 0x40)


def run_installer(path):
    info = ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x00000040 | 0x00000100 | 0x00000400
    info.lpVerb = 'open'
    info.lpFile = str(path)
    info.lpDirectory = str(path.parent)
    info.nShow = 1  # Interactive prerequisite installation for the recipient.
    shell = ctypes.WinDLL('shell32', use_last_error=True).ShellExecuteExW
    shell.argtypes, shell.restype = [ctypes.POINTER(ShellExecuteInfo)], wt.BOOL
    if not shell(ctypes.byref(info)) or not info.hProcess:
        raise ctypes.WinError(ctypes.get_last_error())
    api = kernel()
    try:
        if api.WaitForSingleObject(info.hProcess, 600000) != 0:
            raise RuntimeError('安装尚未结束，请完成后重新打开 Mi Fan。')
        code = wt.DWORD()
        if not api.GetExitCodeProcess(info.hProcess, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return code.value
    finally:
        api.CloseHandle(info.hProcess)


def ensure_runtime(app_dir):
    if sys.getwindowsversion().build < 17763:
        message('此版本需要 Windows 10 1809 或更新版本，或 Windows 11（64 位）。')
        return False
    try:
        with tempfile.TemporaryFile(dir=app_dir):
            pass
    except OSError:
        message('此文件夹无法保存设置。请完整解压 ZIP 到桌面、文档或其他有写入权限的文件夹，再打开 Mi Fan。')
        return False
    for key, (filename, expected_hash, name) in INSTALLERS.items():
        if prerequisites()[key]:
            continue
        if message('电脑缺少 ' + name + '。\n\n是否打开附带的微软安装程序？'
                   '\n安装需要联网，可能出现管理员权限提示。安装完成后将继续启动。', question=True) != 6:
            return False
        installer = Path(app_dir) / '运行库' / filename
        if not installer.is_file() or hashlib.sha256(installer.read_bytes()).hexdigest() != expected_hash:
            message('运行库安装文件缺失或不完整，请重新完整解压分享包。')
            return False
        try:
            result = run_installer(installer)
        except Exception as exc:
            message('运行库未完成安装：' + str(exc))
            return False
        if result in (3010, 1641):
            message('运行库已安装，请重启电脑后再打开 Mi Fan。')
            return False
        if not prerequisites()[key]:
            message(name + ' 尚未安装成功。请完成安装后重新打开 Mi Fan。')
            return False
    return True
