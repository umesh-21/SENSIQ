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
    override = os.environ.get("PIIGUARD_MODELS")
    return Path(override) if override else resource_dir() / "models"


def safe_copies_dir() -> Path:
    """Where redacted copies are written (cleared after a day)."""
    root = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".cache")
    d = Path(root) / "PIIGuard" / "Safe copies"
    d.mkdir(parents=True, exist_ok=True)
    return d


def data_dir() -> Path:
    """Per-user writable folder: settings, ignore list, log."""
    root = os.environ.get("APPDATA") or str(Path.home() / ".config")
    d = Path(root) / "PIIGuard"
    d.mkdir(parents=True, exist_ok=True)
    return d
