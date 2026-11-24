"""Linux side of the platform layer (Ubuntu 22.04 - 24.04, GNOME on Wayland or Xorg).

Same function names as sensiq/platform/windows.py. Uses only the standard library and
common desktop tools, each optional: xclip / wl-clipboard (clipboard), notify-send
(notifications), xdg-open and gdbus (open files and folders), zenity (message boxes).
Nothing here needs root.
"""
from __future__ import annotations

import fcntl
import functools
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import unquote, urlparse

IS_WIN = False
IS_LINUX = sys.platform.startswith("linux")
NAME = "linux"
STARTUP_LABEL = "Start when I sign in"
FILE_MANAGER = "Files"
IPC_FAMILY = "AF_UNIX"
AUTOSTART_NAME = "sensiq.desktop"

_tk = None          # the app's Tk root, for screen sizes
_lock_file = None   # keeps the single-instance lock while the app runs


def session_type() -> str:
    """'wayland', 'x11' or '' (no desktop, e.g. an SSH session)."""
    if os.environ.get("WAYLAND_DISPLAY") or os.environ.get("XDG_SESSION_TYPE") == "wayland":
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return ""


def has_display() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _quiet(cmd, **kw) -> subprocess.CompletedProcess:
    kw.setdefault("env", child_env())
    kw.setdefault("stdout", subprocess.PIPE)
    kw.setdefault("stderr", subprocess.DEVNULL)
    kw.setdefault("timeout", 5)
    return subprocess.run(cmd, **kw)


def _spawn(cmd) -> bool:
    """Start a program detached from SENSIQ (it keeps running if SENSIQ quits)."""
    try:
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True,
                         env=child_env())
        return True
    except OSError:
        return False


# set by the installed (PyInstaller) app for its bundled libraries; PyInstaller keeps the
# value from before in VAR_ORIG. System programs get the old value back, or none.
_BUNDLE_VARS = ("LD_LIBRARY_PATH", "GI_TYPELIB_PATH", "GTK_PATH", "GTK_DATA_PREFIX", "GTK_EXE_PREFIX",
                "GDK_PIXBUF_MODULE_FILE", "GIO_MODULE_DIR", "TCL_LIBRARY", "TK_LIBRARY")
_SHARED_VARS = ("XDG_DATA_DIRS", "GSETTINGS_SCHEMA_DIR", "FONTCONFIG_FILE", "FONTCONFIG_PATH", "SSL_CERT_FILE")


def child_env() -> dict:
    """Environment for system programs SENSIQ starts (xclip, tesseract, gdbus, pkexec...)."""
    env = dict(os.environ)
    if getattr(sys, "frozen", False):
        for var in _BUNDLE_VARS + _SHARED_VARS:
            orig = env.pop(var + "_ORIG", None)
            if orig is not None:
                env[var] = orig
            elif var in _BUNDLE_VARS:
                env.pop(var, None)
    return env


_child_env = child_env


# ---- start-up, single instance ----------------------------------------------------------
def set_dpi_awareness() -> None:
    """Nothing to do: Tk and AT-SPI both use the desktop's own pixels."""


def acquire_single_instance(name: str = "instance") -> bool:
    global _lock_file
    from ..paths import runtime_dir
    path = runtime_dir() / f"{name}.lock"
    f = open(path, "a+")
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return False
    f.seek(0)
    f.truncate()
    f.write(str(os.getpid()))
    f.flush()
    _lock_file = f
    return True


def control_address() -> str:
    from ..paths import runtime_dir
    return str(runtime_dir() / "control.sock")


# ---- processes and windows --------------------------------------------------------------
@functools.lru_cache(maxsize=512)
def _comm(pid: int, start: str) -> str:
    try:
        exe = os.readlink(f"/proc/{pid}/exe")
        return os.path.basename(exe).lower()
    except OSError:
        pass
    try:
        return Path(f"/proc/{pid}/comm").read_text().strip().lower()
    except OSError:
        return ""


def process_name(pid: int) -> str:
    """'firefox', 'chrome', 'keepassxc'... ('' if unknown)."""
    if not pid:
        return ""
    try:
        start = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return ""
    return _comm(pid, start)


def parent_pid(pid: int) -> int:
    try:
        return int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[1])
    except (OSError, IndexError, ValueError):
        return 0


def foreground_window() -> int:
    return 0


def window_pid(hwnd: int) -> int:
    return 0


def foreground_pid() -> int:
    return 0


def restore_foreground(prev_hwnd: int) -> None:
    """Tk's borderless windows don't take the keyboard focus on Linux, so nothing to give back."""


def attach_tk(root) -> None:
    global _tk
    _tk = root


def make_overlay(tk_window) -> None:
    """Cards are already borderless and on top (Tk overrideredirect + -topmost)."""
    try:
        tk_window.attributes("-type", "notification")
    except Exception:
        pass


def _screen() -> Tuple[int, int]:
    if _tk is not None:
        try:
            return int(_tk.winfo_screenwidth()), int(_tk.winfo_screenheight())
        except Exception:
            pass
    return 1920, 1080


def virtual_screen() -> Tuple[int, int, int, int]:
    w, h = _screen()
    return (0, 0, w, h)


_work_cache: Tuple[float, Optional[Tuple[int, int, int, int]]] = (0.0, None)


def _net_workarea() -> Optional[Tuple[int, int, int, int]]:
    """The desktop's free area (without the top bar and dock), from the window manager."""
    if not os.environ.get("DISPLAY") or not shutil.which("xprop"):
        return None
    try:
        out = _quiet(["xprop", "-root", "-notype", "_NET_WORKAREA"], timeout=2).stdout.decode()
        nums = [int(x) for x in out.split("=", 1)[1].replace(",", " ").split()[:4]]
        x, y, w, h = nums
        if w > 200 and h > 200:
            return (x, y, x + w, y + h)
    except Exception:
        pass
    return None


def work_area() -> Tuple[int, int, int, int]:
    """Screen area without the top bar and dock: (left, top, right, bottom)."""
    global _work_cache
    at, area = _work_cache
    if time.monotonic() - at > 30:
        area = _net_workarea()
        _work_cache = (time.monotonic(), area)
    sw, sh = _screen()
    if area is None or area[2] > sw + 1 or area[3] > sh + 1:
        return (0, 32, sw, sh)  # Ubuntu's top bar; the dock is on the left by default
    return area


# ---- messages and notifications ---------------------------------------------------------
def message_box(text: str, title: str = "SENSIQ", error: bool = False) -> None:
    if has_display() and shutil.which("zenity"):
        try:
            _quiet(["zenity", "--error" if error else "--info", "--no-wrap", f"--title={title}",
                    f"--text={text}"], timeout=600, env=child_env())
            return
        except Exception:
            pass
    if has_display():
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            (messagebox.showerror if error else messagebox.showinfo)(title, text, parent=root)
            root.destroy()
            return
        except Exception:
            pass
    print(f"{title}: {text}", file=sys.stderr)


def _icon_path() -> str:
    for p in ("/usr/share/icons/hicolor/256x256/apps/sensiq.png", "/opt/sensiq/sensiq.png"):
        if os.path.exists(p):
            return p
    from ..paths import resource_dir
    for name in ("assets/sensiq.png", "assets/icon.png"):
        p = resource_dir() / name
        if p.exists():
            return str(p)
    return "dialog-information"


def notify(message: str, title: str = "SENSIQ") -> bool:
    """A desktop notification (notify-send). False if none could be shown."""
    if not shutil.which("notify-send"):
        return False
    return _spawn(["notify-send", "--app-name=SENSIQ", f"--icon={_icon_path()}", title, message])


# ---- start when I sign in ---------------------------------------------------------------
def _autostart_file() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autostart" / AUTOSTART_NAME


def launch_command() -> str:
    """Command line that starts SENSIQ (for autostart and the Files script)."""
    if getattr(sys, "frozen", False):
        installed = Path("/usr/bin/sensiq")
        return str(installed) if installed.exists() else shlex.quote(sys.executable)
    root = Path(__file__).resolve().parent.parent.parent
    return f"env PYTHONPATH={shlex.quote(str(root))} {shlex.quote(sys.executable)} -m sensiq"


def is_startup_enabled() -> bool:
    f = _autostart_file()
    if not f.exists():
        return False
    try:
        text = f.read_text(encoding="utf-8")
    except OSError:
        return False
    return "Hidden=true" not in text and "X-GNOME-Autostart-enabled=false" not in text


def set_startup(enabled: bool) -> None:
    f = _autostart_file()
    if not enabled:
        try:
            f.unlink()
        except FileNotFoundError:
            pass
        return
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("[Desktop Entry]\nType=Application\nName=SENSIQ\n"
                 "Comment=Checks for private details before you share\n"
                 f"Exec={launch_command()}\nIcon=sensiq\nTerminal=false\n"
                 "X-GNOME-Autostart-enabled=true\nX-GNOME-Autostart-Delay=5\n", encoding="utf-8")


# ---- opening things ---------------------------------------------------------------------
def open_path(path) -> None:
    """Open a file, folder or web address with the desktop's default app."""
    target = str(path)
    if shutil.which("xdg-open"):
        _spawn(["xdg-open", target])
    elif shutil.which("gio"):
        _spawn(["gio", "open", target])


def open_in_notepad(path) -> None:
    open_text_file(path)


def open_text_file(path) -> None:
    for editor in ("gnome-text-editor", "gedit"):
        if shutil.which(editor):
            _spawn([editor, str(path)])
            return
    open_path(path)


def show_in_file_manager(path) -> None:
    """Open the folder in Files with this item selected (FileManager1 D-Bus call)."""
    uri = Path(path).resolve().as_uri()
    if shutil.which("gdbus"):
        try:
            r = _quiet(["gdbus", "call", "--session", "--dest", "org.freedesktop.FileManager1",
                        "--object-path", "/org/freedesktop/FileManager1",
                        "--method", "org.freedesktop.FileManager1.ShowItems", f"['{uri}']", ""],
                       timeout=5, env=child_env())
            if r.returncode == 0:
                return
        except Exception:
            pass
    open_path(Path(path).parent if Path(path).is_file() else path)


# ---- clipboard --------------------------------------------------------------------------
# Reading uses xclip when an X display is there (also in Wayland sessions, through XWayland,
# which GNOME keeps in step with the desktop clipboard). wl-paste would have to briefly
# take the keyboard focus on GNOME to read, so it is only the fallback. Writing prefers
# wl-copy on Wayland. xclip and wl-copy stay in the background to serve what they copied.
def _reader() -> str:
    if os.environ.get("DISPLAY") and shutil.which("xclip"):
        return "xclip"
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-paste"):
        return "wl"
    return ""


def _writer() -> str:
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy"):
        return "wl"
    if os.environ.get("DISPLAY") and shutil.which("xclip"):
        return "xclip"
    return ""


def clipboard_available() -> bool:
    return bool(_reader())


def clipboard_targets() -> List[str]:
    """Formats on the clipboard right now (e.g. image/png, text/uri-list)."""
    tool = _reader()
    try:
        if tool == "xclip":
            r = _quiet(["xclip", "-selection", "clipboard", "-o", "-t", "TARGETS"], timeout=2)
        elif tool == "wl":
            r = _quiet(["wl-paste", "--list-types"], timeout=2)
        else:
            return []
    except Exception:
        return []
    if r.returncode != 0:
        return []
    return [t.strip() for t in r.stdout.decode("utf-8", "replace").splitlines() if t.strip()]


def clipboard_stamp() -> str:
    """Cheap 'has the clipboard changed?' value: the owner's TIMESTAMP when it gives one."""
    if _reader() != "xclip":
        return ""
    try:
        r = _quiet(["xclip", "-selection", "clipboard", "-o", "-t", "TIMESTAMP"], timeout=2)
        return r.stdout.decode("ascii", "replace").strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def clipboard_bytes(target: str, limit: int = 200 * 1024 * 1024) -> bytes:
    tool = _reader()
    try:
        if tool == "xclip":
            r = _quiet(["xclip", "-selection", "clipboard", "-o", "-t", target], timeout=10)
        elif tool == "wl":
            r = _quiet(["wl-paste", "--no-newline", "--type", target], timeout=10)
        else:
            return b""
    except Exception:
        return b""
    return r.stdout[:limit] if r.returncode == 0 else b""


IMAGE_TYPES = ("image/png", "image/jpeg", "image/bmp", "image/webp", "image/tiff", "image/gif")
FILE_TYPES = ("x-special/gnome-copied-files", "text/uri-list")


def uris_to_paths(text: str) -> List[str]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line in ("copy", "cut"):
            continue
        u = urlparse(line)
        if u.scheme == "file" and u.path:
            out.append(unquote(u.path))
    return out


def read_clipboard():
    """What was copied: ("files", [paths]), ("image", PIL image) or None (text, nothing)."""
    targets = clipboard_targets()
    if not targets:
        return None
    for t in FILE_TYPES:
        if t in targets:
            paths = [p for p in uris_to_paths(clipboard_bytes(t).decode("utf-8", "replace")) if os.path.exists(p)]
            if paths:
                return ("files", paths)
    for t in IMAGE_TYPES:
        if t in targets:
            data = clipboard_bytes(t)
            if data:
                try:
                    import io
                    from PIL import Image
                    img = Image.open(io.BytesIO(data))
                    img.load()
                    return ("image", img)
                except Exception:
                    return None
    return None


def _copy(data: bytes, target: str) -> bool:
    tool = _writer()
    if tool == "wl":
        cmd = ["wl-copy", "--type", target]
    elif tool == "xclip":
        cmd = ["xclip", "-selection", "clipboard", "-t", target, "-i"]
    else:
        return False
    try:
        # the tool forks to keep serving the clipboard: never wait on its output pipes
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True, env=child_env())
        p.communicate(data, timeout=10)
        return p.returncode == 0
    except Exception:
        return False


def set_clipboard_image(image) -> bool:
    import io
    buf = io.BytesIO()
    image.convert("RGB").save(buf, "PNG")
    return _copy(buf.getvalue(), "image/png")


def set_clipboard_text(text: str) -> bool:
    tool = _writer()
    return _copy(text.encode("utf-8"), "text/plain;charset=utf-8" if tool == "wl" else "UTF8_STRING")


def set_clipboard_files(paths) -> bool:
    """Copied files: pasting in Files, a browser or a chat app gets these."""
    uris = "\r\n".join(Path(p).resolve().as_uri() for p in paths) + "\r\n"
    return _copy(uris.encode("utf-8"), "text/uri-list")


def save_clipboard():
    return []


def restore_clipboard(saved) -> None:
    pass


def copy_selection_paths(dialog_hwnd: int):
    return []
