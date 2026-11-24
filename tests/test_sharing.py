"""Tests for date-of-birth variants, Presidio, re-cased names and file/image checks.
Every value is fake."""
import zipfile
from pathlib import Path

import sys

import pytest

from sensiq import filescan
from sensiq.config import Config, IgnoreList
from sensiq.detector.engine import resolve_overlaps
from sensiq.detector.rules import RuleDetector
from sensiq.paths import models_dir

MODELS = models_dir()
HAS_NER = (MODELS / "ner" / "model.onnx").exists()


def kinds(text):
    return {f.kind: f.text for f in resolve_overlaps(RuleDetector().detect(text))}


@pytest.mark.parametrize("text,value", [
    ("my dob is 14/08/1996", "14/08/1996"),
    ("DOB: 14-08-1996", "14-08-1996"),
    ("D.O.B. : 01.01.1990", "01.01.1990"),
    ("D0B 01/01/1990", "01/01/1990"),            # OCR reads O as 0
    ("Date Of Birth 3 March 1992", "3 March 1992"),
    ("जन्म तिथि/DOB: 12/05/1988", "12/05/1988"),
    ("Year of Birth : 1985", "1985"),
])
def test_dob_variants(text, value):
    assert kinds(text).get("DOB") == value


def test_plain_year_and_date_are_not_dob():
    assert "DOB" not in kinds("Released in 1985. Meeting on 14/08/2024.")


@pytest.fixture(scope="module")
def presidio():
    pytest.importorskip("presidio_analyzer")
    from sensiq.detector.presidio_layer import PresidioDetector
    try:
        return PresidioDetector()
    except RuntimeError as exc:
        pytest.skip(str(exc))


def test_presidio_adds_indian_ids(presidio):
    found = {f.kind: f.text for f in presidio.detect("GSTIN 27AAPFU0939F1ZV, car MH12AB1234")}
    assert found.get("GSTIN") == "27AAPFU0939F1ZV"
    assert found.get("VEHICLE_REG") == "MH12AB1234"


def test_presidio_quiet_on_clean_text(presidio):
    assert presidio.detect("The meeting moved to Thursday at 4 pm. Order 20231105 shipped.") == []


@pytest.fixture(scope="module")
def ner():
    if not HAS_NER:
        pytest.skip("name model not downloaded")
    from sensiq.detector.ner import NerModel
    return NerModel(MODELS / "ner", threshold=0.6, labels={"PER": True, "LOC": True})


@pytest.mark.parametrize("text,name", [
    ("my name is rahul sharma and i live in mumbai", "rahul sharma"),
    ("PRIYA PATEL", "PRIYA PATEL"),
    ("hi this is umesh from raipur", "umesh"),
    ("INCOME TAX DEPARTMENT\nPRIYA PATEL\nDate of Birth 01/01/1990\nPermanent Account Number", "PRIYA PATEL"),
    ("Dear team,\nplease meet rahul sharma tomorrow.\nRegards", "rahul sharma"),
])
def test_names_without_normal_capitals(ner, text, name):
    assert name in [f.text for f in ner.detect(text) if f.kind == "PERSON"]


@pytest.mark.parametrize("text,name", [
    ("im rahul", "rahul"),
    ("Im Rahul", "Rahul"),
    ("hi im priya", "priya"),
    ("iam rahul sharma", "rahul sharma"),
    ("I'm Rahul Sharma", "Rahul Sharma"),
    ("Mr. Rahul Sharma", "Rahul Sharma"),
])
def test_chat_words_not_part_of_name(ner, text, name):
    assert [f.text for f in ner.detect(text) if f.kind == "PERSON"] == [name]


@pytest.mark.parametrize("text", [
    "im fine",
    "please send the report by friday",
    "the meeting is in the main hall at noon",
    "turn left at the big temple near the station",
    "INCOME TAX DEPARTMENT",
    "DOB: 14/08/1996",
    "MALE  Mobile: 9876543210",
])
def test_recasing_no_false_alarms(ner, text):
    assert ner.detect(text) == []


def test_docx_text(tmp_path):
    doc = ('<w:document xmlns:w="w"><w:body><w:p><w:r><w:t>Aadhaar 4918 3205 7615</w:t></w:r></w:p>'
           '<w:p><w:r><w:t>dob 12-05-1988</w:t></w:r></w:p></w:body></w:document>')
    p = tmp_path / "a.docx"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("word/document.xml", doc)
    ex = filescan.extract_file(p)
    found = kinds(ex.text)
    assert found.get("AADHAAR") == "4918 3205 7615" and found.get("DOB") == "12-05-1988"


def test_text_file_and_limits(tmp_path):
    p = tmp_path / "n.txt"
    p.write_text("mobile 9876543210", encoding="utf-8")
    assert kinds(filescan.extract_file(p).text).get("PHONE") == "9876543210"
    assert filescan.extract_file(p, max_mb=0.000001).note.startswith("skipped")
    assert filescan.extract_file(tmp_path / "missing.png").note


def _ocr_ok():
    try:
        from sensiq import ocr
        return ocr.status()["ocr_loaded"]
    except Exception:
        return False


@pytest.mark.skipif(not _ocr_ok(), reason="Windows OCR not available")
def test_image_and_scanned_pdf(tmp_path):
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 26)
    img = Image.new("RGB", (640, 220), "white")
    d = ImageDraw.Draw(img)
    d.text((20, 20), "DOB: 14/08/1996", fill="black", font=font)
    d.text((20, 80), "Mobile: 9876543210", fill="black", font=font)
    d.text((20, 140), "4918 3205 7615", fill="black", font=font)
    img.save(tmp_path / "card.png")
    img.save(tmp_path / "card.pdf")
    for name in ("card.png", "card.pdf"):
        ex = filescan.extract_file(tmp_path / name)
        assert ex.method == "OCR"
        found = kinds(ex.text)
        assert found.get("AADHAAR") == "4918 3205 7615", (name, ex.text)
        assert found.get("PHONE") == "9876543210" and found.get("DOB") == "14/08/1996"


def test_dialog_folder_display_names(tmp_path, monkeypatch):
    """Browser upload dialogs show special folders by name, not path."""
    from sensiq import sharewatch
    (tmp_path / "Downloads" / "forms").mkdir(parents=True)
    monkeypatch.setattr(sharewatch, "_folders_cache",
                        {"priya sharma": tmp_path, "downloads": tmp_path / "Downloads"})
    assert sharewatch._dialog_folder("Address: Priya Sharma") == tmp_path
    assert sharewatch._dialog_folder("Address: Downloads") == tmp_path / "Downloads"
    assert sharewatch._dialog_folder("Address: Priya Sharma > Downloads > forms") == tmp_path / "Downloads" / "forms"
    assert sharewatch._dialog_folder("Address: This PC") is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_real_known_folders():
    from sensiq import sharewatch
    downloads = sharewatch._dialog_folder("Address: Downloads")
    assert downloads is not None and downloads.is_dir()


def test_dialog_folder_and_resolve(tmp_path):
    from sensiq.sharewatch import _dialog_folder, _resolve
    (tmp_path / "card.png").write_bytes(b"x")
    assert _dialog_folder(f"Address: {tmp_path}") == tmp_path
    assert _resolve(tmp_path, "card") == [tmp_path / "card.png"]
    assert _resolve(tmp_path, "card.png") == [tmp_path / "card.png"]
    assert _resolve(tmp_path, "nothing") == []
