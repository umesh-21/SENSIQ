"""Where things live, both when running from source and from the installed app."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def resource_dir() -> Path:
    """Read-only files shipped with the app (models, assets)."""
    frozen = getattr(sys, "_MEIPASS", None)
    if frozen:
        return Path(frozen)
    return Path(__file__).resolve().parent.parent


def models_dir() -> Path:
    override = os.environ.get("SENSIQ_MODELS")
    return Path(override) if override else resource_dir() / "models"


_custom_safe: "Path | None" = None   # folder the user chose for safe copies (None = default)
REG_KEY = r"Software\SENSIQ"
REG_SAFE = "SafeCopiesFolder"          # written by the installer's "Safe copies folder" page


def _local_root() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or str(Path.home() / ".cache")) / "SENSIQ"


def default_safe_copies_dir() -> Path:
    return _local_root() / "Safe copies"


def set_safe_copies_folder(folder) -> None:
    """Use this folder for safe copies; None/"" means the default one."""
    global _custom_safe
    if folder and Path(folder).expanduser() != default_safe_copies_dir():
        _custom_safe = Path(os.path.expandvars(str(folder))).expanduser()
    else:
        _custom_safe = None


def safe_copies_is_custom() -> bool:
    return _custom_safe is not None


def safe_copies_dir() -> Path:
    """Where safe (redacted) copies are written: the folder chosen during installation or in
    the tray menu, else %LOCALAPPDATA%/SENSIQ/Safe copies (cleared after a day)."""
    if _custom_safe is not None:
        try:
            _custom_safe.mkdir(parents=True, exist_ok=True)
            return _custom_safe
        except OSError:
            pass  # drive removed, no permission...: fall back to the default folder
    d = default_safe_copies_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def work_dir() -> Path:
    """Private scratch space (unpacked ZIPs, files received from web pages). Always in the
    app's own folder, never in the user's chosen safe-copies folder."""
    d = _local_root() / "work"
    d.mkdir(parents=True, exist_ok=True)
    return d


def registry_safe_copies_folder() -> str:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY) as k:
            return str(winreg.QueryValueEx(k, REG_SAFE)[0] or "")
    except Exception:
        return ""


def remember_safe_copies_folder(folder: str) -> None:
    """Keep the installer's copy of the choice in step, so an update keeps the same folder."""
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_KEY) as k:
            winreg.SetValueEx(k, REG_SAFE, 0, winreg.REG_SZ, folder or str(default_safe_copies_dir()))
    except Exception:
        pass


def data_dir() -> Path:
    """Per-user writable folder: settings, ignore list, log."""
    root = os.environ.get("APPDATA") or str(Path.home() / ".config")
    d = Path(root) / "SENSIQ"
    d.mkdir(parents=True, exist_ok=True)
    return d
