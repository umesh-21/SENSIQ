"""PII Guard (<= 1.3.0) is now SENSIQ: data moves over, old releases' file names still work."""
import hashlib
import sys

import pytest

from sensiq import migrate, updater
from sensiq.config import Config


def test_old_settings_folder_is_moved(tmp_path, monkeypatch):
    old = tmp_path / "PIIGuard"
    old.mkdir()
    (old / "settings.json").write_text('{"mask_style": "placeholder"}', encoding="utf-8")
    (old / "ignored.json").write_text("[]", encoding="utf-8")
    new = tmp_path / "SENSIQ"
    new.mkdir()
    (new / "sensiq.log").write_text("already here", encoding="utf-8")   # created by the new app first
    assert migrate._move_dir(old, new) is True
    assert (new / "settings.json").read_text(encoding="utf-8") == '{"mask_style": "placeholder"}'
    assert (new / "ignored.json").exists() and (new / "sensiq.log").read_text(encoding="utf-8") == "already here"
    assert not old.exists()
    assert migrate._move_dir(old, new) is False        # nothing left: no "renamed" note next time


def test_saved_deny_list_gets_new_exe_name():
    cfg = Config.defaults()
    cfg.save = lambda: None
    cfg.data["deny_processes"] = ["keepass.exe", "piiguard.exe"]
    migrate.after_settings(cfg)
    assert cfg.data["deny_processes"] == ["keepass.exe", "sensiq.exe"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_release_with_old_file_name_is_still_found(monkeypatch):
    import json
    from tests.test_updater import _Resp
    data = b"setup"
    sha = hashlib.sha256(data).hexdigest()
    release = {"tag_name": "v9.0.0", "body": "", "html_url": "u", "assets": [
        {"name": "PIIGuard-Setup-9.0.0.exe", "size": 5, "browser_download_url": "https://dl/a.exe"},
        {"name": "PIIGuard-Setup-9.0.0.exe.sha256", "size": 90, "browser_download_url": "https://dl/a.sha256"}]}

    def fake_get(url, timeout=15):
        if "api.github.com" in url:
            return _Resp(json.dumps(release).encode())
        return _Resp(f"{sha}  x".encode() if url.endswith(".sha256") else data)
    monkeypatch.setattr(updater, "_get", fake_get)
    info = updater.latest("o/SENSIQ")
    assert info.setup_name == "PIIGuard-Setup-9.0.0.exe" and info.sha256 == sha


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_new_name_preferred_when_both_are_published(monkeypatch):
    import json
    from tests.test_updater import _Resp
    release = {"tag_name": "v9.0.0", "body": "", "html_url": "u", "assets": [
        {"name": "PIIGuard-Setup-9.0.0.exe", "size": 5, "browser_download_url": "https://dl/old.exe"},
        {"name": "SENSIQ-Setup-9.0.0.exe", "size": 5, "browser_download_url": "https://dl/new.exe"}]}
    monkeypatch.setattr(updater, "_get", lambda url, timeout=15: _Resp(json.dumps(release).encode()))
    assert updater.latest("o/SENSIQ").setup_name == "SENSIQ-Setup-9.0.0.exe"


def test_duplicate_throwaway_files_do_not_keep_old_folder(tmp_path):
    old, new = tmp_path / "PIIGuard", tmp_path / "SENSIQ"
    old.mkdir()
    new.mkdir()
    (old / "selftest.txt").write_text("old")
    (new / "selftest.txt").write_text("new")
    (old / "settings.json").write_text("old settings")
    (new / "settings.json").write_text("new settings")
    migrate._move_dir(old, new)
    assert (new / "selftest.txt").read_text() == "new" and (new / "settings.json").read_text() == "new settings"
    assert (old / "settings.json").exists() and not (old / "selftest.txt").exists()  # settings never deleted
