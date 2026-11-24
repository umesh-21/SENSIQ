"""A second start of SENSIQ hands its work to the one already running.

`SENSIQ --check FILE...` (used by the Files right-click script "Check with SENSIQ" on Linux)
sends the file paths to the running app over a private local channel: a Unix socket in
$XDG_RUNTIME_DIR/sensiq (0600) on Linux, a named pipe on Windows. Both ends prove they know
the random key in the settings folder, like the browser link. Only paths travel, never contents.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Callable, List

from . import platform as plat

log = logging.getLogger(__name__)


def _key(create: bool = False) -> bytes:
    from .nativehost import link_key
    return link_key(create=create)


def send_check(paths: List[str], timeout: float = 5.0) -> bool:
    """Give these files to the running SENSIQ to check. False if it isn't running."""
    from multiprocessing.connection import Client
    key = _key()
    if not key:
        return False
    try:
        conn = Client(plat.control_address(), family=plat.IPC_FAMILY, authkey=key)
    except Exception:
        return False
    try:
        conn.send({"op": "check", "paths": [str(Path(p).resolve()) for p in paths]})
        return bool(conn.poll(timeout) and conn.recv() == "ok")
    except Exception:
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


class ControlServer(threading.Thread):
    """Accepts check requests from later starts and passes them to on_check(paths)."""

    def __init__(self, on_check: Callable[[List[str]], None], stop_event: threading.Event):
        super().__init__(name="ControlServer", daemon=True)
        self.on_check = on_check
        self.stop_event = stop_event

    def run(self) -> None:
        from multiprocessing.connection import Listener
        address = plat.control_address()
        if plat.IPC_FAMILY == "AF_UNIX":
            try:  # left over from a crash; we hold the single-instance lock, so it isn't in use
                os.unlink(address)
            except FileNotFoundError:
                pass
            except OSError:
                log.warning("couldn't remove an old control socket")
        try:
            listener = Listener(address, family=plat.IPC_FAMILY, authkey=_key(create=True))
            if plat.IPC_FAMILY == "AF_UNIX":
                os.chmod(address, 0o600)
        except Exception:
            log.exception("control channel unavailable")
            return
        log.info("ControlServer started")
        while not self.stop_event.is_set():
            try:
                conn = listener.accept()
            except Exception:
                log.debug("control: refused a connection", exc_info=True)
                continue
            try:
                if conn.poll(5):
                    msg = conn.recv()
                    if isinstance(msg, dict) and msg.get("op") == "check":
                        paths = [str(p) for p in msg.get("paths") or [] if isinstance(p, str)]
                        if paths:
                            self.on_check(paths)
                        conn.send("ok")
            except Exception:
                log.debug("control request failed", exc_info=True)
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
