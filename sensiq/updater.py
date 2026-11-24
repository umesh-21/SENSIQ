"""Updates from GitHub Releases.

The only network use of the installed app. It asks GitHub's public API for the latest
release of the SENSIQ repository (a plain GET; nothing about you or your files is
sent) and, if it is newer, offers it with its release notes. On "Update now" the setup
file is downloaded, checked against the SHA-256 published with the release, and run
silently; it closes SENSIQ, installs over it and starts the new version.

Turn it off in the tray ("Check for updates automatically") or in settings
(updates.check = false).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple

from . import UPDATE_REPO, __version__
from .paths import data_dir

log = logging.getLogger(__name__)

API = "https://api.github.com/repos/{repo}/releases/latest"
USER_AGENT = "SENSIQ-updater"  # generic: no user or machine details
SETUP_NAME = re.compile(r"^SENSIQ-Setup-.*\.exe$", re.I)
OLD_SETUP_NAME = re.compile(r"^PIIGuard-Setup-.*\.exe$", re.I)  # releases from before the rename
FIRST_CHECK_S = 90            # after start-up
EVERY_S = 12 * 3600           # then twice a day


@dataclass
class UpdateInfo:
    version: str
    notes: str               # release notes (Markdown) - "what's new"
    page: str                # release page on GitHub
    setup_url: str
    setup_name: str
    setup_size: int
    sha256: Optional[str]    # from the release's .sha256 file


def parse_version(v: str) -> Tuple[int, ...]:
    """'v1.2.10' -> (1, 2, 10); anything after the numbers (-beta) is ignored."""
    m = re.match(r"^\s*v?(\d+(?:\.\d+)*)", v or "")
    return tuple(int(x) for x in m.group(1).split(".")) if m else (0,)


def is_newer(remote: str, local: str = __version__) -> bool:
    a, b = list(parse_version(remote)), list(parse_version(local))
    n = max(len(a), len(b))
    return a + [0] * (n - len(a)) > b + [0] * (n - len(b))


def _get(url: str, timeout: float = 15):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout)


def latest(repo: str = UPDATE_REPO) -> Optional[UpdateInfo]:
    """The latest published release (drafts and pre-releases are skipped by GitHub)."""
    with _get(API.format(repo=repo)) as r:
        data = json.loads(r.read().decode("utf-8"))
    assets = data.get("assets") or []
    setup = (next((a for a in assets if SETUP_NAME.match(a.get("name", ""))), None)
             or next((a for a in assets if OLD_SETUP_NAME.match(a.get("name", ""))), None))
    if setup is None:
        return None
    sha = None
    sha_asset = next((a for a in assets if a.get("name", "").lower() == setup["name"].lower() + ".sha256"), None)
    if sha_asset is not None:
        try:
            with _get(sha_asset["browser_download_url"]) as r:
                m = re.search(r"\b[0-9a-fA-F]{64}\b", r.read().decode("utf-8", "replace"))
                sha = m.group(0).lower() if m else None
        except Exception:
            log.warning("could not read the release checksum", exc_info=True)
    return UpdateInfo(version=str(data.get("tag_name", "")).lstrip("vV"), notes=data.get("body") or "",
                      page=data.get("html_url") or f"https://github.com/{repo}/releases",
                      setup_url=setup["browser_download_url"], setup_name=setup["name"],
                      setup_size=int(setup.get("size") or 0), sha256=sha)


def check(repo: str = UPDATE_REPO, skip_version: str = "") -> Optional[UpdateInfo]:
    """A newer release than this app, unless it is the one the user chose to skip."""
    info = latest(repo)
    if info is None or not is_newer(info.version):
        return None
    if skip_version and parse_version(skip_version) == parse_version(info.version):
        return None
    return info


def updates_dir() -> Path:
    d = Path(os.environ.get("LOCALAPPDATA") or data_dir()) / "SENSIQ" / "updates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def download(info: UpdateInfo, progress: Optional[Callable[[float], None]] = None) -> Path:
    """Download the setup file and check it. Raises on a failed or mismatching download."""
    if not info.sha256:
        raise RuntimeError("the release has no checksum file, so the download can't be verified")
    out = updates_dir() / info.setup_name
    part = out.with_suffix(".part")
    h = hashlib.sha256()
    done = 0
    with _get(info.setup_url, timeout=60) as r, open(part, "wb") as f:
        total = int(r.headers.get("Content-Length") or info.setup_size or 0)
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if progress and total:
                progress(min(1.0, done / total))
    if h.hexdigest().lower() != info.sha256:
        part.unlink(missing_ok=True)
        raise RuntimeError("the downloaded file doesn't match the release checksum")
    if out.exists():
        out.unlink()
    part.rename(out)
    return out


def install(setup: Path) -> None:
    """Run the setup silently; it closes SENSIQ, installs over it and starts it again."""
    log_path = updates_dir() / "setup.log"
    flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([str(setup), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/LOG={log_path}"],
                     creationflags=flags, close_fds=True)


def cleanup() -> None:
    """Remove setup files of earlier updates."""
    try:
        for p in updates_dir().glob("SENSIQ-Setup-*"):
            if time.time() - p.stat().st_mtime > 3600:
                p.unlink(missing_ok=True)
    except Exception:
        pass


class UpdateChecker(threading.Thread):
    """Checks in the background now and then; posts ("update_available", info) to the app."""

    def __init__(self, config, events, stop_event: threading.Event):
        super().__init__(name="updates", daemon=True)
        self.config, self.events, self.stop_event = config, events, stop_event

    def run(self) -> None:
        cleanup()
        if self.stop_event.wait(FIRST_CHECK_S):
            return
        while not self.stop_event.is_set():
            opts = self.config.data.get("updates", {})
            if opts.get("check", True):
                try:
                    info = check(opts.get("repo") or UPDATE_REPO, opts.get("skip_version", ""))
                    if info is not None:
                        self.events.put(("update_available", info, False))
                except Exception as exc:  # offline, GitHub down, rate limit: try again later
                    log.info("update check failed: %s", exc)
            if self.stop_event.wait(EVERY_S):
                return
