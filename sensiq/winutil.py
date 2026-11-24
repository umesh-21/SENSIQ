"""Small Win32 helpers (ctypes only, no extra dependencies).

Every function degrades to a harmless no-op on other platforms so the detector
and tests can run anywhere.
"""
from __future__ import annotations

import ctypes
import functools
import os
import sys
from pathlib import Path
from typing import Tuple

IS_WIN = sys.platform == "win32"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "SENSIQ"

if IS_WIN:
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int
    user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
    user32.MessageBoxW.restype = ctypes.c_int
    user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
    user32.SystemParametersInfoW.restype = wintypes.BOOL
    _GetLong = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    _SetLong = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
    _GetLong.argtypes = [wintypes.HWND, ctypes.c_int]
    _GetLong.restype = ctypes.c_ssize_t
    _SetLong.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    _SetLong.restype = ctypes.c_ssize_t

    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE

GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ERROR_ALREADY_EXISTS = 183
_mutex = None


def set_dpi_awareness() -> None:
    """Use physical pixels so UI Automation rectangles match our overlay coordinates."""
    if not IS_WIN:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass


def acquire_single_instance(name: str = "Local\\SENSIQ.SingleInstance") -> bool:
    global _mutex
    if not IS_WIN:
        return True
    _mutex = kernel32.CreateMutexW(None, False, name)
    return ctypes.get_last_error() != ERROR_ALREADY_EXISTS


@functools.lru_cache(maxsize=512)
def process_name(pid: int) -> str:
    if not IS_WIN or not pid:
        return ""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
        return ""
    finally:
        kernel32.CloseHandle(handle)


def foreground_window() -> int:
    if not IS_WIN:
        return 0
    return user32.GetForegroundWindow() or 0


def window_pid(hwnd: int) -> int:
    if not IS_WIN or not hwnd:
        return 0
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def foreground_pid() -> int:
    return window_pid(foreground_window())


def restore_foreground(prev_hwnd: int) -> None:
    """If showing an overlay pulled focus to us, hand it straight back."""
    if not IS_WIN or not prev_hwnd:
        return
    current = foreground_window()
    if current != prev_hwnd and window_pid(current) == os.getpid():
        user32.SetForegroundWindow(prev_hwnd)


def make_overlay(tk_window) -> None:
    """Turn a Tk toplevel into a non-activating, topmost tool window."""
    if not IS_WIN:
        return
    try:
        hwnd = user32.GetParent(tk_window.winfo_id()) or tk_window.winfo_id()
        style = _GetLong(hwnd, GWL_EXSTYLE)
        _SetLong(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST)
    except Exception:
        pass


def virtual_screen() -> Tuple[int, int, int, int]:
    if not IS_WIN:
        return (0, 0, 1920, 1080)
    return (user32.GetSystemMetrics(76), user32.GetSystemMetrics(77),
            user32.GetSystemMetrics(78), user32.GetSystemMetrics(79))


def work_area() -> Tuple[int, int, int, int]:
    """Primary monitor area excluding the taskbar: (left, top, right, bottom)."""
    if not IS_WIN:
        return (0, 0, 1920, 1040)
    rect = wintypes.RECT()
    if user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
        return (rect.left, rect.top, rect.right, rect.bottom)
    return (0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1) - 48)


def message_box(text: str, title: str = "SENSIQ", error: bool = False) -> None:
    if IS_WIN:
        user32.MessageBoxW(None, text, title, 0x10 if error else 0x40)
    else:
        print(f"{title}: {text}", file=sys.stderr)


def launch_command() -> str:
    """Command line used for 'Start with Windows'."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    pyw = Path(sys.executable).with_name("pythonw.exe")
    exe = pyw if pyw.exists() else Path(sys.executable)
    return f'"{exe}" -m sensiq'


def is_startup_enabled() -> bool:
    if not IS_WIN:
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_VALUE)
            return True
    except OSError:
        return False


def set_startup(enabled: bool) -> None:
    if not IS_WIN:
        return
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, launch_command())
        else:
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                pass


CF_DIB = 8
CF_HDROP = 15
GMEM_MOVEABLE = 0x0002


def _set_clipboard(fmt_data) -> bool:
    """Replace the clipboard with the given [(format, bytes)] entries."""
    if not IS_WIN:
        return False
    k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32
    k32.GlobalAlloc.restype = ctypes.c_void_p
    k32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [ctypes.c_void_p]
    k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    u32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    u32.SetClipboardData.restype = ctypes.c_void_p
    import time
    for _ in range(10):
        if u32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        return False
    try:
        u32.EmptyClipboard()
        for fmt, data in fmt_data:
            h = k32.GlobalAlloc(GMEM_MOVEABLE, len(data))
            p = k32.GlobalLock(h)
            ctypes.memmove(p, data, len(data))
            k32.GlobalUnlock(h)
            if not u32.SetClipboardData(fmt, h):
                return False
        return True
    finally:
        u32.CloseClipboard()


# Clipboard formats that are GDI handles (not memory blocks) or that Windows re-creates by
# itself from the others; they are skipped when saving and restoring the clipboard.
_SKIP_FORMATS = {2, 3, 9, 14, 0x80, 0x82, 0x83, 0x8E}


def _clip_api():
    k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32
    u32.GetClipboardData.restype = ctypes.c_void_p
    u32.EnumClipboardFormats.restype = ctypes.c_uint
    k32.GlobalSize.argtypes = [ctypes.c_void_p]
    k32.GlobalSize.restype = ctypes.c_size_t
    k32.GlobalLock.argtypes = [ctypes.c_void_p]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    return k32, u32


def save_clipboard():
    """Snapshot of every memory-based clipboard format, for restore_clipboard()."""
    if not IS_WIN:
        return []
    import time
    k32, u32 = _clip_api()
    for _ in range(10):
        if u32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        return None
    saved = []
    try:
        fmt = u32.EnumClipboardFormats(0)
        while fmt:
            if fmt not in _SKIP_FORMATS:
                h = u32.GetClipboardData(fmt)
                if h:
                    size = k32.GlobalSize(h)
                    p = k32.GlobalLock(h) if size else None
                    if p:
                        saved.append((fmt, ctypes.string_at(p, size)))
                        k32.GlobalUnlock(h)
            fmt = u32.EnumClipboardFormats(fmt)
    finally:
        u32.CloseClipboard()
    return saved


def restore_clipboard(saved) -> None:
    if saved is None or not IS_WIN:
        return
    if saved:
        _set_clipboard(saved)
    else:
        _set_clipboard([])  # it was empty


def find_child_window(hwnd: int, class_name: str) -> int:
    if not IS_WIN or not hwnd:
        return 0
    found = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(h, _):
        buf = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(h, buf, 64)
        if buf.value == class_name:
            found.append(h)
            return False
        return True
    user32.EnumChildWindows(hwnd, proc(cb), 0)
    return int(found[0]) if found else 0


def copy_selection_paths(dialog_hwnd: int):
    """Full paths of the items selected in a file dialog (or Explorer window), from any folder,
    library, OneDrive, Home or search view. Uses the shell view's own Copy command, reads the
    file list it puts on the clipboard, then restores the clipboard exactly as it was."""
    if not IS_WIN:
        return []
    view = find_child_window(dialog_hwnd, "SHELLDLL_DefView")
    if not view:
        return []
    import time
    k32, u32 = _clip_api()
    saved = save_clipboard()
    if saved is None:
        return []  # clipboard busy or locked: never overwrite what we couldn't save
    before = u32.GetClipboardSequenceNumber()
    u32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    u32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                                        wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
    res = ctypes.c_size_t()
    # WM_COMMAND, FCIDM_SHVIEW_COPY (0x7019). Only this command is ever sent.
    u32.SendMessageTimeoutW(view, 0x0111, 0x7019, 0, 0x0002, 2000, ctypes.byref(res))
    paths = []
    for _ in range(10):
        if u32.GetClipboardSequenceNumber() != before:
            break
        time.sleep(0.03)
    else:
        return []  # nothing selected / copy refused: clipboard untouched
    try:
        if u32.OpenClipboard(None):
            try:
                h = u32.GetClipboardData(CF_HDROP)
                if h:
                    shell32 = ctypes.windll.shell32
                    shell32.DragQueryFileW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_uint]
                    n = shell32.DragQueryFileW(h, 0xFFFFFFFF, None, 0)
                    for i in range(n):
                        buf = ctypes.create_unicode_buffer(32768)
                        shell32.DragQueryFileW(h, i, buf, 32768)
                        paths.append(buf.value)
            finally:
                u32.CloseClipboard()
    finally:
        restore_clipboard(saved)
    return paths


def set_clipboard_image(image) -> bool:
    import io
    buf = io.BytesIO()
    image.convert("RGB").save(buf, "BMP")
    return _set_clipboard([(CF_DIB, buf.getvalue()[14:])])  # DIB = BMP without its file header


def set_clipboard_files(paths) -> bool:
    import struct
    files = "".join(str(p) + "\0" for p in paths) + "\0"
    header = struct.pack("<IiiII", 20, 0, 0, 0, 1)  # DROPFILES: offset, pt, fNC, fWide=1
    return _set_clipboard([(CF_HDROP, header + files.encode("utf-16-le"))])


def open_path(path) -> None:
    if IS_WIN:
        os.startfile(str(path))  # type: ignore[attr-defined]


def open_in_notepad(path) -> None:
    if IS_WIN:
        import subprocess
        subprocess.Popen(["notepad.exe", str(path)])
