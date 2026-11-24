"""Linux desktop integration that SENSIQ keeps up to date by itself (no root needed):
the Files (Nautilus) right-click script "Check with SENSIQ".

Linux has no global hooks for drags and Open/Upload dialogs, so this script, the tray's
"Check a file…" and the browser extension are how files get checked before they're shared.
"""
from __future__ import annotations

import os
import shlex
import shutil
from pathlib import Path

SCRIPT_NAME = "Check with SENSIQ"
MARK = "# Added by SENSIQ"


def files_scripts_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "nautilus" / "scripts"


def files_script_text(command: str) -> str:
    return f"""#!/bin/sh
{MARK}: checks the selected files for private details before you share them.
# Files passes the selection in NAUTILUS_SCRIPT_SELECTED_FILE_PATHS (one per line).
set -f
if [ -n "$NAUTILUS_SCRIPT_SELECTED_FILE_PATHS" ]; then
  set --
  IFS='
'
  for f in $NAUTILUS_SCRIPT_SELECTED_FILE_PATHS; do [ -n "$f" ] && set -- "$@" "$f"; done
fi
[ "$#" -gt 0 ] || exit 0
command -v {shlex.quote(shlex.split(command)[0])} >/dev/null 2>&1 || exit 0   # SENSIQ was removed
exec {command} --check "$@"
"""


APPINDICATOR_EXTENSIONS = ("ubuntu-appindicators@ubuntu.com", "appindicatorsupport@rgcjonas.gmail.com")


def tray_host_present() -> bool:
    """Is something on this desktop showing tray icons (a StatusNotifierWatcher on D-Bus)?
    True when it can't be told, so SENSIQ never complains by mistake."""
    from .platform import child_env
    if not shutil.which("gdbus"):
        return True
    import subprocess
    try:
        r = subprocess.run(["gdbus", "call", "--session", "--dest", "org.freedesktop.DBus",
                            "--object-path", "/org/freedesktop/DBus", "--method",
                            "org.freedesktop.DBus.NameHasOwner", "org.kde.StatusNotifierWatcher"],
                           capture_output=True, text=True, timeout=5, env=child_env())
    except Exception:
        return True
    if r.returncode != 0:
        return True
    return "true" in r.stdout


def enable_appindicator_extension() -> bool:
    """Switch on GNOME's tray-icon extension for this user (no root). True if a command worked."""
    from .platform import child_env
    if not shutil.which("gnome-extensions"):
        return False
    import subprocess
    ok = False
    for ext in APPINDICATOR_EXTENSIONS:
        try:
            r = subprocess.run(["gnome-extensions", "enable", ext], capture_output=True, timeout=4, env=child_env())
            ok = ok or r.returncode == 0
        except Exception:
            pass
    return ok


def install_files_script(force: bool = False) -> bool:
    """Write (or refresh) the script when Files is installed. Leaves a file the user made alone."""
    from .platform import launch_command
    folder = files_scripts_dir()
    if not force and not (shutil.which("nautilus") or folder.is_dir()):
        return False
    target = folder / SCRIPT_NAME
    text = files_script_text(launch_command())
    if target.exists():
        try:
            old = target.read_text(encoding="utf-8")
        except OSError:
            return False
        if MARK not in old:
            return False          # not ours
        if old == text:
            return True
    folder.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    target.chmod(0o755)
    return True
