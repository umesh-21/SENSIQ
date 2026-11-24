"""Native-messaging host for the SENSIQ browser extension.

The browser starts `SENSIQ.exe chrome-extension://<id>/` with stdin/stdout connected to the
extension. This process only relays: browser messages go to the running SENSIQ app over a
private named pipe (per Windows user, protected by a random key in %APPDATA%), and the app's
answers go back to the browser. It never reads files or goes online itself.

Message framing (Chrome/Edge native messaging): 4-byte little-endian length + UTF-8 JSON.
"""
from __future__ import annotations

import ctypes
import json
import os
import struct
import sys
import threading
from ctypes import wintypes

from .paths import data_dir

MAX_TO_BROWSER = 1024 * 1024  # the browser refuses host messages over 1 MB


def pipe_address() -> str:
    user = os.environ.get("USERNAME", "user").replace("\\", "_")
    return rf"\\.\pipe\SENSIQ-{user}-link"


def link_key(create: bool = False) -> bytes:
    path = data_dir() / "link.key"
    if not path.exists() and create:
        path.write_bytes(os.urandom(32))
    return path.read_bytes() if path.exists() else b""


class _Std:
    """stdin/stdout through Win32 handles (works in a windowed exe with no console)."""

    def __init__(self):
        k32 = ctypes.windll.kernel32
        k32.GetStdHandle.restype = wintypes.HANDLE
        self.k32 = k32
        self.inp = k32.GetStdHandle(-10)
        self.out = k32.GetStdHandle(-11)
        self.lock = threading.Lock()

    def read_exact(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = ctypes.create_string_buffer(n - len(buf))
            got = wintypes.DWORD()
            if not self.k32.ReadFile(self.inp, chunk, n - len(buf), ctypes.byref(got), None) or got.value == 0:
                raise EOFError
            buf += chunk.raw[: got.value]
        return bytes(buf)

    def read_message(self) -> dict:
        (length,) = struct.unpack("<I", self.read_exact(4))
        return json.loads(self.read_exact(length).decode("utf-8"))

    def write_message(self, msg: dict) -> None:
        data = json.dumps(msg, separators=(",", ":")).encode("utf-8")
        if len(data) > MAX_TO_BROWSER:
            raise ValueError("message too large for the browser")
        payload = struct.pack("<I", len(data)) + data
        with self.lock:
            done = 0
            while done < len(payload):
                wrote = wintypes.DWORD()
                part = payload[done:]
                if not self.k32.WriteFile(self.out, part, len(part), ctypes.byref(wrote), None):
                    raise EOFError
                done += wrote.value


def _parent_exe() -> str:
    """Name of the browser that started us (chrome.exe, msedge.exe, brave.exe...)."""
    try:
        from . import winutil
        import ctypes.wintypes as wt

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                        ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wt.DWORD),
                        ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD),
                        ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                        ("szExeFile", ctypes.c_wchar * 260)]
        k32 = ctypes.windll.kernel32
        k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
        snap = k32.CreateToolhelp32Snapshot(2, 0)
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        parents = {}
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            parents[entry.th32ProcessID] = (entry.th32ParentProcessID, entry.szExeFile.lower())
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
        k32.CloseHandle(snap)
        browsers = {"chrome.exe", "msedge.exe", "brave.exe", "vivaldi.exe", "opera.exe", "chromium.exe"}
        pid = parents.get(os.getpid(), (0, ""))[0]
        for _ in range(5):  # the browser may start us through a launcher (cmd.exe when run from source)
            ppid, exe = parents.get(pid, (0, ""))
            if exe in browsers:
                return exe
            if not ppid:
                break
            pid = ppid
        return winutil.process_name(parents.get(os.getpid(), (0, ""))[0])
    except Exception:
        return ""


def main() -> int:
    from multiprocessing.connection import Client
    std = _Std()
    browser = _parent_exe()
    try:
        conn = Client(pipe_address(), family="AF_PIPE", authkey=link_key())
    except Exception:
        conn = None

    if conn is None:  # SENSIQ isn't running: answer every check with "go ahead"
        try:
            while True:
                msg = std.read_message()
                if msg.get("op") == "checkend":
                    std.write_message({"op": "result", "id": msg["id"], "action": "original",
                                       "reason": "SENSIQ isn't running", "files": []})
                    std.write_message({"op": "end", "id": msg["id"]})
        except (EOFError, OSError):
            return 0

    conn.send({"op": "hello", "browser": browser})

    def from_app():
        try:
            while True:
                std.write_message(conn.recv())
        except (EOFError, OSError):
            os._exit(0)

    threading.Thread(target=from_app, daemon=True).start()
    try:
        while True:
            conn.send(std.read_message())
    except (EOFError, OSError):
        pass
    try:
        conn.close()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
