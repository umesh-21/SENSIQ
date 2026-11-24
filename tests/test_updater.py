"""Updates from GitHub Releases (network calls are faked)."""
import hashlib
import io
import json

import pytest

from piiguard import updater
from piiguard.overlay import Overlay


@pytest.mark.parametrize("remote, local, newer", [
    ("v1.2.0", "1.1.0", True), ("1.10.0", "1.9.3", True), ("v1.2", "1.2.0", False),
    ("1.2.0", "1.2.0", False), ("v1.1.9", "1.2.0", False), ("v2.0.0-beta", "1.9.0", True),
])
def test_version_compare(remote, local, newer):
    assert updater.is_newer(remote, local) is newer


class _Resp(io.BytesIO):
    def __init__(self, data: bytes):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_github(monkeypatch, setup_bytes: bytes, sha: str, version="v9.0.0", with_sha=True):
    release = {
        "tag_name": version, "body": "## New\n- **Faster** scans\n- Fixed [a bug](https://x)",
        "html_url": "https://github.com/o/PIIGuard/releases/tag/" + version,
        "assets": [{"name": f"PIIGuard-Setup-{version[1:]}.exe", "size": len(setup_bytes),
                    "browser_download_url": "https://dl/setup.exe"}],
    }
    if with_sha:
        release["assets"].append({"name": f"PIIGuard-Setup-{version[1:]}.exe.sha256", "size": 80,
                                  "browser_download_url": "https://dl/setup.sha256"})

    def fake_get(url, timeout=15):
        if url.startswith("https://api.github.com/"):
            assert url == "https://api.github.com/repos/o/PIIGuard/releases/latest"
            return _Resp(json.dumps(release).encode())
        if url.endswith(".sha256"):
            return _Resp(f"{sha}  PIIGuard-Setup.exe".encode())
        return _Resp(setup_bytes)
    monkeypatch.setattr(updater, "_get", fake_get)


def test_check_finds_newer_release_and_download_is_verified(monkeypatch, tmp_path):
    data = b"MZ fake setup" * 1000
    _fake_github(monkeypatch, data, hashlib.sha256(data).hexdigest())
    monkeypatch.setattr(updater, "updates_dir", lambda: tmp_path)
    info = updater.check("o/PIIGuard")
    assert info.version == "9.0.0" and "Faster" in info.notes
    seen = []
    path = updater.download(info, seen.append)
    assert path.read_bytes() == data and seen[-1] == 1.0


def test_skipped_version_is_not_offered_again(monkeypatch):
    _fake_github(monkeypatch, b"x", hashlib.sha256(b"x").hexdigest())
    assert updater.check("o/PIIGuard", skip_version="9.0.0") is None
    assert updater.check("o/PIIGuard", skip_version="8.0.0") is not None


def test_same_or_older_release_is_not_offered(monkeypatch):
    _fake_github(monkeypatch, b"x", hashlib.sha256(b"x").hexdigest(), version="v0.0.1")
    assert updater.check("o/PIIGuard") is None


def test_tampered_download_is_rejected(monkeypatch, tmp_path):
    _fake_github(monkeypatch, b"evil", hashlib.sha256(b"good").hexdigest())
    monkeypatch.setattr(updater, "updates_dir", lambda: tmp_path)
    info = updater.check("o/PIIGuard")
    with pytest.raises(RuntimeError, match="checksum"):
        updater.download(info)
    assert not list(tmp_path.glob("PIIGuard-Setup-*.exe"))


def test_release_without_checksum_is_not_installed(monkeypatch, tmp_path):
    _fake_github(monkeypatch, b"x", "", with_sha=False)
    monkeypatch.setattr(updater, "updates_dir", lambda: tmp_path)
    info = updater.check("o/PIIGuard")
    with pytest.raises(RuntimeError, match="checksum"):
        updater.download(info)


def test_release_notes_for_the_card():
    lines = Overlay.notes_text("## New\n- **Faster** scans\n- Fixed [a bug](https://x)\n\nThanks `all`")
    assert lines == [("New", "h"), ("• Faster scans", "b"), ("• Fixed a bug", "b"), ("", ""), ("Thanks all", "")]
