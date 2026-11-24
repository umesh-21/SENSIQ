"""User settings and the ignore list, stored in %APPDATA%\\SENSIQ."""
from __future__ import annotations

import copy
import json
import logging
import sys
from pathlib import Path
from typing import Iterable, Optional

from .paths import data_dir

log = logging.getLogger(__name__)

DETECTOR_KINDS = [
    "PRIVATE_KEY", "API_KEY", "PASSWORD", "CARD", "AADHAAR", "PAN", "SSN", "PASSPORT",
    "BANK_ACCOUNT", "VOTER_ID", "DRIVING_LICENCE", "EMAIL", "UPI", "IFSC", "PHONE",
    "DOB", "IP_ADDRESS", "PINCODE", "GSTIN", "CRYPTO_WALLET", "VEHICLE_REG",
]

DEFAULTS = {
    "enabled": True,
    "paused_until": 0,                # pauses end by themselves after an hour
    "poll_interval_ms": 300,
    "debounce_ms": 500,
    "max_chars": 20000,
    "mask_style": "partial",          # "partial" (XXXX 1234) or "placeholder" ([Aadhaar])
    "show_alert_for_high_risk": True,
    "ner": {
        "enabled": True,
        "threshold": 0.60,
        "labels": {"PER": True, "LOC": True, "ORG": False, "MISC": False},
    },
    # Words that are never flagged as a name or place (e.g. a product or team name).
    "never_flag_words": [],
    # Pictures: find QR codes/barcodes, faces and signatures in images and PDFs.
    "visual": {"codes": True, "faces": True, "signatures": True},
    # Extra OCR languages to use when Windows has them installed (e.g. "hi" for Hindi).
    "ocr_languages": ["en", "hi", "mr", "bn", "ta", "te", "gu", "kn", "ml", "pa", "ur"],
    # Presidio's recognizers (phone numbers, Indian IDs, cards, IBAN...) on top of the rules.
    "presidio": {"enabled": True, "spacy_model": "en_core_web_sm"},
    # Checks images, PDFs and documents you are about to share. Report only; files are never changed.
    "file_scan": {
        "enabled": True,
        "clipboard": True,        # copied images/screenshots and copied files
        "drag_drop": True,        # files dragged out of File Explorer or the desktop
        "file_dialogs": True,     # files picked in an Open / Upload / Attach dialog
        "browser_extension": True,  # files web pages receive, via the SENSIQ browser extension
        "opened_files": False,    # offer a check when you open a document/picture (off by default)
        "max_file_mb": 200,
        "max_pdf_pages": 0,       # 0 = every page of a PDF / multi-page image
        "safe_copies_folder": "",  # where safe copies are saved; "" = default (cleared after 24 h)
    },
    # New versions from GitHub Releases (the only time the app goes online). repo "" = built in.
    "updates": {"check": True, "repo": "", "skip_version": ""},
    "settings_version": 2,
    "detectors": {k: (k != "IP_ADDRESS") for k in DETECTOR_KINDS},
    # Apps where SENSIQ never reads text.
    "deny_processes": [
        "keepass.exe", "keepassxc.exe", "1password.exe", "bitwarden.exe",
        "mstsc.exe", "vmconnect.exe", "sensiq.exe",
    ],
    # Apps whose main document area is editable even without a writable ValuePattern.
    "document_editor_processes": [
        "winword.exe", "outlook.exe", "olk.exe", "onenote.exe", "wordpad.exe",
        "notepad.exe", "powerpnt.exe",
    ],
}


if sys.platform.startswith("linux"):   # the same apps' Linux program names
    DEFAULTS["deny_processes"] += ["keepassxc", "keepass2", "keepass", "1password", "bitwarden",
                                   "remmina", "sensiq", "gnome-keyring-daemon", "seahorse"]


def _deep_merge(base: dict, override: dict) -> None:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def _migrate(saved: dict) -> None:
    """Bring settings saved by older versions up to date (only values that were old defaults)."""
    if int(saved.get("settings_version", 1)) < 2:
        fs = saved.get("file_scan")
        if isinstance(fs, dict):
            if fs.get("max_pdf_pages") == 30:   # old default: only the first 30 pages were checked
                fs["max_pdf_pages"] = 0
            if fs.get("max_file_mb") == 50:
                fs["max_file_mb"] = 200
        saved["settings_version"] = 2


class Config:
    def __init__(self, data: Optional[dict] = None, path: Optional[Path] = None):
        self.data = data if data is not None else copy.deepcopy(DEFAULTS)
        self.path = path

    @classmethod
    def defaults(cls) -> "Config":
        return cls(copy.deepcopy(DEFAULTS), None)

    @classmethod
    def load(cls) -> "Config":
        path = data_dir() / "settings.json"
        cfg = cls(copy.deepcopy(DEFAULTS), path)
        cfg.reload()
        return cfg

    def reload(self) -> None:
        data = copy.deepcopy(DEFAULTS)
        if self.path is not None:
            if self.path.exists():
                try:
                    saved = json.loads(self.path.read_text(encoding="utf-8-sig"))
                    _migrate(saved)
                    _deep_merge(data, saved)
                except Exception as exc:
                    log.warning("Could not read %s (%s); using defaults", self.path, exc)
            else:
                self.data = data
                self.save()
        self.data = data

    def save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        except OSError as exc:
            log.warning("Could not save settings: %s", exc)

    @property
    def denied(self) -> set:
        return {p.lower() for p in self.data.get("deny_processes", [])}

    @property
    def document_editors(self) -> set:
        return {p.lower() for p in self.data.get("document_editor_processes", [])}


class IgnoreList:
    """Values the user marked "not private". Stored as SHA-256 fingerprints, each with a
    masked hint (e.g. "Aadhaar number: XXXX XXXX 2997") so the list can be reviewed in the
    tray; the real value is never stored."""

    def __init__(self, persisted: Iterable = (), path: Optional[Path] = None):
        if isinstance(persisted, dict):
            self._persisted = {str(k): str(v or "") for k, v in persisted.items()}
        else:                       # list of fingerprints (before 1.5.0)
            self._persisted = {str(k): "" for k in persisted}
        self._session: set = set()
        self.path = path

    @classmethod
    def load(cls) -> "IgnoreList":
        path = data_dir() / "ignored.json"
        items: object = []
        if path.exists():
            try:
                items = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                log.warning("Ignore list unreadable; starting empty")
        return cls(items if isinstance(items, (list, dict)) else [], path)

    def contains(self, fp: str) -> bool:
        return fp in self._session or fp in self._persisted

    def add(self, fp: str, persist: bool = False, hint: str = "") -> None:
        self._session.add(fp)
        if persist:
            self._persisted[fp] = hint or self._persisted.get(fp, "")
            self.save()

    def remove(self, fp: str) -> None:
        """Undo add(): the value is flagged again."""
        self._session.discard(fp)
        if fp in self._persisted:
            del self._persisted[fp]
            self.save()

    def entries(self) -> list:
        """[(fingerprint, hint)] of the saved items, for the tray's "Not-private items" list."""
        return sorted(self._persisted.items(), key=lambda kv: (kv[1] == "", kv[1].lower()))

    def clear(self) -> None:
        self._session.clear()
        self._persisted.clear()
        self.save()

    def save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.write_text(json.dumps(dict(sorted(self._persisted.items())), ensure_ascii=False, indent=0),
                                 encoding="utf-8")
        except OSError as exc:
            log.warning("Could not save ignore list: %s", exc)
