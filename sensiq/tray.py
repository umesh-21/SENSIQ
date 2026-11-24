"""System tray icon. Menu clicks are posted to the app's event queue so all UI
work stays on the Tk thread."""
from __future__ import annotations

import logging
import os
import sys


def _linux_backend() -> None:
    """pystray gives up if the AppIndicator library is missing instead of trying its other
    backends, so pick one: AppIndicator (Ubuntu's top bar) > GTK status icon > plain X11."""
    if not sys.platform.startswith("linux") or os.environ.get("PYSTRAY_BACKEND"):
        return
    try:
        import gi
        for ns in ("AyatanaAppIndicator3", "AppIndicator3"):
            try:
                gi.require_version(ns, "0.1")
                os.environ["PYSTRAY_BACKEND"] = "appindicator"
                return
            except ValueError:
                continue
        gi.require_version("Gtk", "3.0")
        os.environ["PYSTRAY_BACKEND"] = "gtk"
    except Exception:
        os.environ["PYSTRAY_BACKEND"] = "xorg"


_linux_backend()

import pystray  # noqa: E402
from pystray import Menu, MenuItem as Item  # noqa: E402

from . import APP_NAME, platform as plat  # noqa: E402
from .icon import make_icon_image  # noqa: E402

log = logging.getLogger(__name__)


class Tray:
    def __init__(self, app):
        self.app = app
        self.icon = pystray.Icon("SENSIQ", make_icon_image(64, active=self._enabled()),
                                 self._title(), menu=self._menu())

    def _enabled(self) -> bool:
        return bool(self.app.config.data.get("enabled", True))

    @staticmethod
    def _has_hindi() -> bool:
        try:
            from . import ocr
            return any(t.lower().startswith("hi") for t in ocr.installed_languages())
        except Exception:
            return True

    def _typing_ready(self) -> bool:
        try:
            from .capture_linux import accessibility_enabled
            return accessibility_enabled()
        except Exception:
            return True

    def _update_version(self) -> str:
        info = getattr(self.app, "update_info", None)
        return info.version if info is not None else ""

    def _title(self) -> str:
        return f"{APP_NAME}: {'on' if self._enabled() else 'PAUSED (right-click to resume)'}"

    def _post(self, action: str):
        return lambda icon, item: self.app.events.put(("tray", action))

    def _menu(self) -> Menu:
        return Menu(
            # left-click on the shield only shows the status; it never pauses protection
            Item("Show status", self._post("status"), default=True, visible=False),
            Item(lambda item: "Pause for 1 hour" if self._enabled() else "Resume protection",
                 self._post("toggle")),
            Item("Replace with labels like [Aadhaar]", self._post("style"),
                 checked=lambda item: self.app.config.data.get("mask_style") == "placeholder"),
            Menu.SEPARATOR,
            Item("Check a file…", self._post("check_file")),
            Item("Typing checks: turn on (Linux, test version)…", self._post("typing_setup"),
                 visible=lambda item: plat.IS_LINUX and not self._typing_ready()),
            Item(plat.STARTUP_LABEL, self._post("startup"), checked=lambda item: plat.is_startup_enabled()),
            Item("Check uploads in my browser (set up)…", self._post("browser_setup")),
            Item("Safe copies folder", Menu(
                Item("Open safe copies folder", self._post("safe_open")),
                Item("Change folder…", self._post("safe_change")),
                Item("Use the default folder (deleted after 24 hours)", self._post("safe_default"),
                     checked=lambda item: not self.app.config.data.get("file_scan", {}).get("safe_copies_folder")),
            )),
            Item("Offer a check when I open a file", self._post("opened_files"),
                 checked=lambda item: bool(self.app.config.data.get("file_scan", {}).get("opened_files", False))),
            Item("Read Hindi in images (install)…", self._post("add_hindi"),
                 visible=lambda item: not self._has_hindi()),
            Item(lambda item: f"Update available: SENSIQ {self._update_version()}… (see what's new)",
                 self._post("show_update"), visible=lambda item: bool(self._update_version())),
            Item("Check for updates now", self._post("check_updates")),
            Item("Check for updates automatically", self._post("auto_updates"),
                 checked=lambda item: bool(self.app.config.data.get("updates", {}).get("check", True))),
            Item("Run self-test", self._post("selftest")),
            Item("Open settings file", self._post("settings")),
            Item("Reload settings", self._post("reload")),
            Item("Not-private items…", self._post("not_private_list")),
            Item("Forget all ignored items", self._post("clear_ignore")),
            Item("Open log folder", self._post("logs")),
            Menu.SEPARATOR,
            Item("Quit SENSIQ", self._post("quit")),
        )

    def start(self) -> None:
        try:
            if plat.IS_LINUX:
                # GTK backends (AppIndicator) need GTK's main loop running: with run_detached()
                # the icon is created but never registers with the top bar, so it stays invisible
                import threading
                threading.Thread(target=self._run_linux, name="Tray", daemon=True).start()
            else:
                self.icon.run_detached()
            log.info("tray icon started (%s)", type(self.icon).__module__)
        except Exception:   # no tray on this desktop: SENSIQ keeps working without the icon
            log.exception("tray icon couldn't start")

    def _run_linux(self) -> None:
        try:
            self.icon.run()
        except Exception:
            log.exception("tray icon stopped because of an error")

    def _in_tray_thread(self, fn) -> None:
        """GTK must only be used from its own thread on Linux: hand the work to its loop."""
        if plat.IS_LINUX and type(self.icon).__module__ != "pystray._xorg":
            try:
                from gi.repository import GLib
                GLib.idle_add(lambda: (fn(), False)[1])
                return
            except Exception:
                pass
        fn()

    def refresh(self) -> None:
        def work():
            try:
                self.icon.icon = make_icon_image(64, active=self._enabled())
                self.icon.title = self._title()
                self.icon.update_menu()
            except Exception:
                log.debug("tray refresh failed", exc_info=True)
        self._in_tray_thread(work)

    def notify(self, message: str) -> None:
        if plat.notify(message, APP_NAME):   # Linux: notify-send (AppIndicator has no balloons)
            return
        try:
            self.icon.notify(message, APP_NAME)
        except Exception:
            log.debug("tray notify failed", exc_info=True)

    def stop(self) -> None:
        def work():
            try:
                self.icon.stop()
            except Exception:
                pass
        self._in_tray_thread(work)
