"""Link between the PII Guard browser extension and this app.

* register_host(): tells Chrome, Edge, Brave and Chromium (current user only, no admin) where
  the native-messaging host is, so the extension can reach this app.
* BrowserLink: a thread that accepts connections from the host over a private named pipe,
  collects the files a page is about to receive, has the scanner check them and sends back
  the page's answer: the original, the safe copy, or nothing (cancelled).
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Dict, Set

from .nativehost import link_key, pipe_address
from .paths import data_dir, resource_dir, work_dir

log = logging.getLogger(__name__)

HOST_NAME = "com.piiguard.host"
EXTENSION_ID = "anojebieecillflfojodicejpjkeimlo"
BROWSER_KEYS = [
    r"Software\Google\Chrome\NativeMessagingHosts",
    r"Software\Microsoft\Edge\NativeMessagingHosts",
    r"Software\BraveSoftware\Brave-Browser\NativeMessagingHosts",
    r"Software\Chromium\NativeMessagingHosts",
]
CHUNK_TO_BROWSER = 600 * 1024   # bytes per reply chunk (stays under the browser's 1 MB limit once base64'd)


def extension_dir() -> Path:
    return resource_dir() / "browser_extension"


def _host_command() -> Path:
    """What the browser runs: the app itself when installed, a small launcher when run from source."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    launcher = data_dir() / "native-host.bat"
    py = Path(sys.executable)
    root = Path(__file__).resolve().parent.parent
    launcher.write_text(f'@echo off\r\nset "PYTHONPATH={root}"\r\n"{py}" -m piiguard %*\r\n', encoding="ascii")
    return launcher


def register_host() -> bool:
    """Native-messaging registration for the current user (HKCU). Safe to repeat."""
    try:
        import winreg
        manifest = data_dir() / "native-host.json"
        manifest.write_text(json.dumps({
            "name": HOST_NAME,
            "description": "PII Guard - checks files you upload for personal details",
            "path": str(_host_command()),
            "type": "stdio",
            "allowed_origins": [f"chrome-extension://{EXTENSION_ID}/"],
        }, indent=2), encoding="utf-8")
        for key in BROWSER_KEYS:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{key}\{HOST_NAME}") as k:
                winreg.SetValueEx(k, "", 0, winreg.REG_SZ, str(manifest))
        return True
    except Exception:
        log.exception("couldn't register the browser link")
        return False


def unregister_host() -> None:
    import winreg
    for key in BROWSER_KEYS:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, rf"{key}\{HOST_NAME}")
        except OSError:
            pass


class _Request:
    def __init__(self, rid: str, meta: list, folder: Path):
        self.id = rid
        self.meta = meta
        self.folder = folder
        self.parts: Dict[int, Dict[int, bytes]] = {}

    def add(self, file: int, index: int, data: str) -> None:
        self.parts.setdefault(file, {})[index] = base64.b64decode(data)

    def write(self) -> list:
        self.folder.mkdir(parents=True, exist_ok=True)
        paths = []
        for i, m in enumerate(self.meta):
            name = Path(str(m.get("name") or f"file{i}")).name or f"file{i}"
            target = self.folder / name
            n = 1
            while target in paths:
                n += 1
                target = self.folder / f"{Path(name).stem} ({n}){Path(name).suffix}"
            chunks = self.parts.get(i, {})
            with open(target, "wb") as out:
                for k in sorted(chunks):
                    out.write(chunks[k])
            paths.append(target)
        return paths


class BrowserLink(threading.Thread):
    """Accepts the extension's host connections and serves its checks."""

    def __init__(self, scanner, stop_event: threading.Event):
        super().__init__(name="BrowserLink", daemon=True)
        self.scanner = scanner
        self.stop_event = stop_event
        self.browsers: Dict[int, str] = {}   # connection id -> browser exe
        self._lock = threading.Lock()

    def active_browsers(self) -> Set[str]:
        """Browsers whose pages are protected by the extension right now."""
        with self._lock:
            return {b for b in self.browsers.values() if b}

    def run(self) -> None:
        from multiprocessing.connection import Listener
        register_host()
        try:
            listener = Listener(pipe_address(), family="AF_PIPE", authkey=link_key(create=True))
        except Exception:
            log.exception("browser link unavailable")
            return
        log.info("BrowserLink started")
        while not self.stop_event.is_set():
            try:
                conn = listener.accept()
            except Exception:
                log.debug("browser link: refused a connection", exc_info=True)
                continue
            threading.Thread(target=self._serve, args=(conn,), daemon=True, name="BrowserConn").start()

    # ---- one browser connection -------------------------------------------------------------
    def _serve(self, conn) -> None:
        cid = id(conn)
        send_lock = threading.Lock()
        requests: Dict[str, _Request] = {}

        def send(msg: dict) -> None:
            with send_lock:
                conn.send(msg)

        try:
            while True:
                msg = conn.recv()
                op = msg.get("op")
                if op == "hello":
                    if msg.get("browser"):  # from the host; the extension's own hello carries no name
                        with self._lock:
                            self.browsers[cid] = msg["browser"].lower()
                        log.info("browser link: %s connected", msg["browser"])
                elif op == "check":
                    folder = work_dir() / ("web-" + hashlib.sha1(
                        f"{msg['id']}{time.time()}".encode()).hexdigest()[:12])
                    requests[msg["id"]] = _Request(msg["id"], msg.get("files") or [], folder)
                elif op == "chunk":
                    req = requests.get(msg["id"])
                    if req:
                        req.add(int(msg["file"]), int(msg["index"]), msg["data"])
                elif op == "checkend":
                    req = requests.pop(msg["id"], None)
                    if req is None:
                        continue
                    paths = req.write()
                    source = {"upload": "File added to a web page", "drop": "File dropped on a web page",
                              "paste": "File pasted into a web page", "picker": "File opened by a web page"}
                    label = source.get(msg.get("source") or "", "File added to a web page")
                    site = msg.get("site") or ""
                    self.scanner.submit_browser(f"{label}{' (' + site + ')' if site else ''}", paths,
                                                lambda action, files=None, rid=req.id, folder=req.folder:
                                                self._answer(send, rid, action, files or [], folder))
        except (EOFError, OSError):
            pass
        except Exception:
            log.exception("browser link connection failed")
        finally:
            with self._lock:
                self.browsers.pop(cid, None)
            try:
                conn.close()
            except Exception:
                pass

    def _answer(self, send, rid: str, action: str, files, folder: Path) -> None:
        """Send the page's answer: 'original', 'safe' (with the safe files) or 'cancel'."""
        try:
            files = [Path(f) for f in files] if action == "safe" else []
            meta = [{"name": f.name, "type": _mime(f), "size": f.stat().st_size} for f in files]
            send({"op": "result", "id": rid, "action": action, "files": meta})
            for i, f in enumerate(files):
                with open(f, "rb") as src:
                    k = 0
                    while True:
                        block = src.read(CHUNK_TO_BROWSER)
                        if not block and k:
                            break
                        send({"op": "filechunk", "id": rid, "file": i, "index": k,
                              "data": base64.b64encode(block).decode("ascii")})
                        k += 1
                        if not block:
                            break
            send({"op": "end", "id": rid})
        except Exception:
            log.debug("couldn't answer the page", exc_info=True)
        finally:
            shutil.rmtree(folder, ignore_errors=True)  # the page's copies of the files


def _mime(path: Path) -> str:
    import mimetypes
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"
