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
