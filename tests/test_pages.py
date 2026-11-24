"""Whole-document checks: every PDF page, pictures on text pages, every page of a
multi-page image, very tall images, and old settings being upgraded. Every value is fake."""
import ctypes
import json

import pytest

from piiguard import filescan, redact
from piiguard.detector.engine import resolve_overlaps
from piiguard.detector.rules import RuleDetector


def detect(text):
    return resolve_overlaps(RuleDetector().detect(text))


def kinds(text):
    return [f.kind for f in detect(text)]


def _ocr_ok():
    try:
        from piiguard import ocr
        return ocr.status()["ocr_loaded"]
    except Exception:
        return False


needs_ocr = pytest.mark.skipif(not _ocr_ok(), reason="Windows OCR not available")


def _card(text_lines, size=(700, 260)):
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype("arial.ttf", 28)
    for i, t in enumerate(text_lines):
        d.text((24, 24 + i * 60), t, fill="black", font=font)
    return img


def _text_obj(pdfium, raw, doc, page, text, x, y, size=12):
    obj = raw.FPDFPageObj_NewTextObj(doc.raw, b"Helvetica", ctypes.c_float(size))
    buf = ctypes.create_unicode_buffer(text)
    raw.FPDFText_SetText(obj, ctypes.cast(buf, ctypes.POINTER(ctypes.c_ushort)))
    raw.FPDFPageObj_Transform(obj, 1, 0, 0, 1, x, y)
    raw.FPDFPage_InsertObject(page.raw, obj)


def test_every_pdf_page_is_checked(tmp_path):
    pdfium = pytest.importorskip("pypdfium2")
    import pypdfium2.raw as raw
    doc = pdfium.PdfDocument.new()
    for i in range(40):
        page = doc.new_page(595, 842)
        _text_obj(pdfium, raw, doc, page, f"Page {i + 1} of the report. Contact mobile 98765432{i:02d} today.", 50, 700)
        raw.FPDFPage_GenerateContent(page.raw)
    src = tmp_path / "long.pdf"
    doc.save(str(src))
    ex = filescan.extract_file(src)                 # default: no page limit
    assert ex.page_count == 40 and len(ex.pages) == 40 and ex.note is None
    found = detect(ex.text)
    assert sum(f.kind == "PHONE" for f in found) == 40
    out = tmp_path / "safe.pdf"
    res = redact.redact_file(src, ex, found, out)
    assert res.replaced == 40 and res.missed == 0
    assert kinds(filescan.extract_file(out).text) == []


def test_page_limit_still_possible(tmp_path):
    pdfium = pytest.importorskip("pypdfium2")
    import pypdfium2.raw as raw
    doc = pdfium.PdfDocument.new()
    for i in range(5):
        page = doc.new_page(595, 842)
        _text_obj(pdfium, raw, doc, page, f"Page {i + 1} has some ordinary text on it for the test.", 50, 700)
        raw.FPDFPage_GenerateContent(page.raw)
    src = tmp_path / "five.pdf"
    doc.save(str(src))
    ex = filescan.extract_file(src, max_pdf_pages=2)
    assert len(ex.pages) == 2 and "first 2 of 5" in ex.note


@needs_ocr
def test_picture_on_a_text_page_is_read(tmp_path):
    """A typed form with a scanned ID pasted in: the ID in the picture must be found and covered."""
    pdfium = pytest.importorskip("pypdfium2")
    import pypdfium2.raw as raw
    doc = pdfium.PdfDocument.new()
    page = doc.new_page(595, 842)
    _text_obj(pdfium, raw, doc, page, "Application form for the hostel room. Attached proof below.", 50, 780)
    pic = _card(["Aadhaar 4918 3205 7615", "Mobile 9876543210"])
    image = pdfium.PdfImage.new(doc)
    image.set_bitmap(pdfium.PdfBitmap.from_pil(pic))
    image.set_matrix(pdfium.PdfMatrix().scale(350, 130).translate(50, 500))
    page.insert_obj(image)
    page.gen_content()
    src = tmp_path / "form.pdf"
    doc.save(str(src))
    ex = filescan.extract_file(src)
    assert "pictures OCR" in ex.method, ex.method
    found = detect(ex.text)
    assert {"AADHAAR", "PHONE"} <= set(kinds(ex.text)), ex.text
    out = tmp_path / "safe.pdf"
    res = redact.redact_file(src, ex, found, out)
    assert res.missed == 0 and res.replaced == len(found)
    assert "AADHAAR" not in kinds(filescan.extract_file(out).text)


@needs_ocr
def test_every_page_of_a_tiff_scan(tmp_path):
    pages = [_card(["Page one, nothing private"]), _card(["Aadhaar 4918 3205 7615"]), _card(["Mobile 9876543210"])]
    src = tmp_path / "scan.tif"
    pages[0].save(src, save_all=True, append_images=pages[1:])
    ex = filescan.extract_file(src)
    assert ex.page_count == 3 and len(ex.frame_words) == 3
    found = detect(ex.text)
    assert {"AADHAAR", "PHONE"} <= set(f.kind for f in found)
    out = tmp_path / "safe.tif"
    res = redact.redact_file(src, ex, found, out)
    assert res.missed == 0
    from PIL import Image
    with Image.open(out) as im:
        assert im.n_frames == 3                     # every page kept
    left = kinds(filescan.extract_file(out).text)
    assert "AADHAAR" not in left and "PHONE" not in left


@needs_ocr
def test_very_tall_image_read_to_the_end(tmp_path):
    from PIL import Image, ImageDraw, ImageFont
    from piiguard import ocr
    img = Image.new("RGB", (1080, 16000), "white")
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype("arial.ttf", 26)
    for i in range(8):
        d.text((40, 400 + i * 2000), f"Entry {i} mobile 98765432{i:02d}", fill="black", font=font)
    text, _ = ocr.recognize(img)
    flat = text.replace(" ", "")
    assert all(f"98765432{i:02d}" in flat for i in range(8)), text


def test_whole_document_checked_not_just_the_start():
    """Live typing is capped at max_chars, but documents must be checked to the last character."""
    import queue
    import threading
    from piiguard.config import Config, IgnoreList
    from piiguard.detector.engine import DetectionEngine
    from piiguard.worker import DetectorWorker
    cfg = Config.defaults()
    cfg.data["ner"]["enabled"] = False
    cfg.data["presidio"]["enabled"] = False
    filler = "".join(f"Line {i}: the quarterly figures were reviewed and approved.\n" for i in range(4000))
    text = "Aadhaar 4918 3205 7615\n" + filler + "Applicant mobile 9876543210\n" + filler + "PAN ABCPE1234F\n"
    assert len(text) > 400_000
    engine = DetectionEngine(cfg, IgnoreList(), "/nonexistent")
    assert [f.kind for f in engine.detect(text)] == ["AADHAAR"]          # typing cap: start only
    found = engine.detect_all(text)
    assert [f.kind for f in found] == ["AADHAAR", "PHONE", "PAN"]
    assert all(text[f.start:f.end] == f.text for f in found)               # offsets point at the values
    worker = DetectorWorker(cfg, IgnoreList(), queue.Queue(), threading.Event())
    worker.engine = engine
    worker.ready.set()
    found = worker.detect_now(text)
    assert [f.kind for f in found] == ["AADHAAR", "PHONE", "PAN"]
    assert all(text[f.start:f.end] == f.text for f in found)


def test_old_settings_upgraded(tmp_path):
    from piiguard.config import Config
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"file_scan": {"max_pdf_pages": 30, "max_file_mb": 50}}), encoding="utf-8")
    cfg = Config(path=p)
    cfg.reload()
    assert cfg.data["file_scan"]["max_pdf_pages"] == 0
    assert cfg.data["file_scan"]["max_file_mb"] == 200
    p.write_text(json.dumps({"settings_version": 2, "file_scan": {"max_pdf_pages": 30}}), encoding="utf-8")
    cfg.reload()
    assert cfg.data["file_scan"]["max_pdf_pages"] == 30   # a limit chosen on purpose is kept
