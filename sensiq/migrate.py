"""One-time move from the app's old name (PII Guard, up to version 1.3.0) to SENSIQ.

Runs at every start but only acts while traces of the old name exist:
  %APPDATA%\\PIIGuard              -> %APPDATA%\\SENSIQ (settings, ignore list, browser-link key)
  HKCU\\Software\\PIIGuard          -> HKCU\\Software\\SENSIQ (safe-copies folder chosen in setup)
  Run value "PIIGuard"            -> Run value "SENSIQ" (start with Windows)
  browser-link registrations com.piiguard.host -> removed (SENSIQ registers com.sensiq.host)
  %LOCALAPPDATA%\\PIIGuard         -> temporary files only (old safe copies, downloads): removed
Returns True the first time something was moved, so the app can say the name changed.
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

OLD = "PIIGuard"
NEW = "SENSIQ"
OLD_HOST = "com.piiguard.host"
_HOST_PARENTS = [r"Software\Google\Chrome\NativeMessagingHosts", r"Software\Microsoft\Edge\NativeMessagingHosts",
                 r"Software\BraveSoftware\Brave-Browser\NativeMessagingHosts", r"Software\Chromium\NativeMessagingHosts"]


def _disposable(name: str) -> bool:
    """Files the app recreates by itself (never settings, ignore list or keys)."""
    n = name.lower()
    return (n in ("selftest.txt", "native-host.json", "native-host.bat", "browser-setup.html")
            or ".log" in n)


def _move_dir(old: Path, new: Path) -> bool:
    """Move a folder's contents into the new one (files already there win)."""
    if not old.is_dir():
        return False
    new.mkdir(parents=True, exist_ok=True)
    moved = False
    for item in old.iterdir():
        dest = new / item.name
        if dest.exists():
            if item.is_file() and _disposable(item.name):  # new app already made its own: drop the old one
                try:
                    item.unlink()
                except OSError:
                    pass
            continue
        try:
            shutil.move(str(item), str(dest))
            moved = True
        except OSError:
            log.debug("could not move %s", item, exc_info=True)
    try:
        old.rmdir()  # only if now empty
    except OSError:
        pass
    return moved


def _registry() -> bool:
    try:
        import winreg
    except ImportError:
        return False
    changed = False
    hkcu = winreg.HKEY_CURRENT_USER
    # safe-copies folder chosen on the old installer's page
    try:
        with winreg.OpenKey(hkcu, rf"Software\{OLD}") as k:
            value = winreg.QueryValueEx(k, "SafeCopiesFolder")[0]
        local = os.environ.get("LOCALAPPDATA") or ""
        if os.path.normcase(str(value)) == os.path.normcase(os.path.join(local, OLD, "Safe copies")):
            value = os.path.join(local, NEW, "Safe copies")   # the old default -> the new default
        try:
            with winreg.OpenKey(hkcu, rf"Software\{NEW}") as k:
                winreg.QueryValueEx(k, "SafeCopiesFolder")
        except OSError:
            with winreg.CreateKey(hkcu, rf"Software\{NEW}") as k:
                winreg.SetValueEx(k, "SafeCopiesFolder", 0, winreg.REG_SZ, value)
        winreg.DeleteKey(hkcu, rf"Software\{OLD}")
        changed = True
    except OSError:
        pass
    # start with Windows
    run = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(hkcu, run, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as k:
            winreg.QueryValueEx(k, OLD)
            winreg.DeleteValue(k, OLD)
            changed = True
            from . import winutil
            winutil.set_startup(True)
    except OSError:
        pass
    # old browser-link registrations
    for parent in _HOST_PARENTS:
        try:
            winreg.DeleteKey(hkcu, parent + "\\" + OLD_HOST)
            changed = True
        except OSError:
            pass
    return changed


def _settings(config) -> None:
    """Saved lists replace the defaults wholesale: make sure SENSIQ never reads its own windows."""
    deny = config.data.get("deny_processes")
    if isinstance(deny, list) and "sensiq.exe" not in [str(x).lower() for x in deny]:
        config.data["deny_processes"] = [x for x in deny if str(x).lower() != "piiguard.exe"] + ["sensiq.exe"]
        try:
            config.save()
        except Exception:
            pass


def _old_temp() -> None:
    local = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    old = local / OLD
    for sub in ("Safe copies", "work", "updates"):
        shutil.rmtree(old / sub, ignore_errors=True)  # temporary files only; in use -> next start
    try:
        old.rmdir()
    except OSError:
        pass


def run() -> bool:
    """Move what the old name left behind. Call before the settings are loaded."""
    moved = False
    try:
        appdata = Path(os.environ.get("APPDATA") or Path.home())
        moved = _move_dir(appdata / OLD, appdata / NEW)
        moved = _registry() or moved
        _old_temp()
    except Exception:
        log.debug("migration from the old name failed", exc_info=True)
    return moved


def after_settings(config) -> None:
    try:
        _settings(config)
    except Exception:
        log.debug("settings migration failed", exc_info=True)
