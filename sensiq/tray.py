"""System tray icon. Menu clicks are posted to the app's event queue so all UI
work stays on the Tk thread."""
from __future__ import annotations

import logging

import pystray
from pystray import Menu, MenuItem as Item

from . import APP_NAME, winutil
from .icon import make_icon_image

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
            Item("Start with Windows", self._post("startup"), checked=lambda item: winutil.is_startup_enabled()),
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
        self.icon.run_detached()

    def refresh(self) -> None:
        try:
            self.icon.icon = make_icon_image(64, active=self._enabled())
            self.icon.title = self._title()
            self.icon.update_menu()
        except Exception:
            log.debug("tray refresh failed", exc_info=True)

    def notify(self, message: str) -> None:
        try:
            self.icon.notify(message, APP_NAME)
        except Exception:
            log.debug("tray notify failed", exc_info=True)

    def stop(self) -> None:
        try:
            self.icon.stop()
        except Exception:
            pass
