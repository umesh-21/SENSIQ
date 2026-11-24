"""1.6.0: names on ID cards are found despite OCR noise from the card's background art, the
noise itself isn't flagged, and updates are only ever offered. Every value is fake."""
import pytest

from sensiq.config import Config, IgnoreList
from sensiq.detector.ner import NerModel
from sensiq.paths import models_dir

# OCR text of an Aadhaar card photo (made-up person); the junk lines come from the patterned background
CARD = """Governme
Ramesh Kumar N P
DOB: 21/05/1997
4918 3205 7615
OhiqueJdentifidation AUthOtityofTndia
Address: C/O: Suresh Kumar, KOTTARAM,
PALLIPPURAM, Cherthala
South, Alappuzha, Kerala, 688525
4918 3205 7615
-eri8Tt
2ss4
3.-TTtTR,
AADHAAR
1947
help@uidai.gov.in www.uidai.gov.in
A"""


@pytest.fixture(scope="module")
def engine():
    from sensiq.detector.engine import DetectionEngine
    eng = DetectionEngine(Config.defaults(), IgnoreList(), models_dir())
    if eng.ner is None:
        pytest.skip("name model not available")
    return eng


def test_name_found_despite_background_noise(engine):
    names = [f.text for f in engine.detect_all(CARD) if f.kind == "PERSON"]
    assert "Ramesh Kumar N P" in names


def test_background_noise_is_not_a_name(engine):
    texts = [f.text for f in engine.detect_all(CARD)]
    assert not any("TTtTR" in t or "eri8Tt" in t for t in texts)


@pytest.mark.parametrize("word, ok", [("Sarathkumar", True), ("PERUNTHURUTH", True), ("McDonald", True),
                                      ("DeSouza", True), ("N P", True), ("TTtTR", False), ("Hrmz", False)])
def test_word_shapes(word, ok):
    assert NerModel._wordlike(word) is ok


def test_windows_only_for_short_texts():
    m = NerModel.__new__(NerModel)
    assert len(list(m._line_windows("a\nb\nc\nd"))) == 4
    assert len("x\n" * 2000) > NerModel.WINDOW_TEXT_MAX   # long documents skip the extra pass


def test_update_is_offered_not_installed(monkeypatch):
    """An update found in the background only notifies and shows the card."""
    import types
    from sensiq import updater
    from sensiq.app import App
    calls = []
    monkeypatch.setattr(updater, "install", lambda p: calls.append(("install", p)))
    monkeypatch.setattr(updater, "download", lambda *a, **k: calls.append(("download",)))
    shown = []
    fake = types.SimpleNamespace(updating=False, update_info=None, tray=None,
                                 overlay=types.SimpleNamespace(update_card=lambda *a, **k: shown.append(a[0].version)),
                                 _notify=lambda m: calls.append(("notify", m)))
    fake._show_update_card = lambda: App._show_update_card(fake)
    info = updater.UpdateInfo("9.9.9", "notes", "page", "url", "SENSIQ-Setup-9.9.9.exe", 1, "x" * 64)
    App._handle(fake, ("update_available", info, False))
    assert shown == ["9.9.9"] and [c[0] for c in calls] == ["notify"]
    assert fake.update_info is info   # tray shows "Update available: SENSIQ 9.9.9…"
