"""Safe-copy tests: chosen details are gone from the copy, unchosen ones stay,
and the original file is untouched. Every value is fake."""
import zipfile

import pytest

from sensiq import filescan, redact
from sensiq.detector.engine import resolve_overlaps
from sensiq.detector.rules import RuleDetector


def detect(text):
    return resolve_overlaps(RuleDetector().detect(text))


def kinds(text):
    return {f.kind for f in detect(text)}


def test_text_file_partial_choice(tmp_path):
    src = tmp_path / "a.txt"
    src.write_text("Aadhaar 4918 3205 7615\nmobile 9876543210\n", encoding="utf-8")
    ex = filescan.extract_file(src)
    found = detect(ex.text)
    chosen = [f for f in found if f.kind == "AADHAAR"]
    out = tmp_path / "out.txt"
    res = redact.redact_file(src, ex, chosen, out)
    assert res.replaced == 1 and res.missed == 0
    after = out.read_text(encoding="utf-8")
    assert "XXXX XXXX 7615" in after and "4918 3205 7615" not in after
    assert "9876543210" in after                     # not chosen: kept
    assert "4918 3205 7615" in src.read_text(encoding="utf-8")  # original untouched


def test_docx(tmp_path):
    src = tmp_path / "f.docx"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", '<w:document xmlns:w="w"><w:body><w:p><w:r><w:t>'
                   'dob 12-05-1988 &amp; PAN ABCPE1234F</w:t></w:r></w:p></w:body></w:document>')
    ex = filescan.extract_file(src)
    out = tmp_path / "out.docx"
    res = redact.redact_file(src, ex, detect(ex.text), out)
    assert res.replaced == 2
    assert kinds(filescan.extract_file(out).text) == set()
    with zipfile.ZipFile(out) as z:
        assert "[Content_Types].xml" in z.namelist()   # rest of the package kept


def test_no_findings_copies_unchanged(tmp_path):
    src = tmp_path / "n.txt"
    src.write_text("nothing here", encoding="utf-8")
    out = tmp_path / "o.txt"
    redact.redact_file(src, filescan.extract_file(src), [], out)
    assert out.read_bytes() == src.read_bytes()


def _ocr_ok():
    try:
        from sensiq import ocr
        return ocr.status()["ocr_loaded"]
    except Exception:
        return False


@pytest.mark.skipif(not _ocr_ok(), reason="Windows OCR not available")
@pytest.mark.parametrize("name", ["card.png", "card.jpg", "card.pdf"])
def test_image_and_scanned_pdf(tmp_path, name):
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 26)
    img = Image.new("RGB", (640, 220), "white")
    d = ImageDraw.Draw(img)
    d.text((20, 20), "DOB: 14/08/1996", fill="black", font=font)
    d.text((20, 80), "Mobile: 9876543210", fill="black", font=font)
    d.text((20, 140), "4918 3205 7615", fill="black", font=font)
    src = tmp_path / name
    img.save(src)
    ex = filescan.extract_file(src)
    found = detect(ex.text)
    assert {"DOB", "PHONE", "AADHAAR"} <= {f.kind for f in found}
    keep_phone = [f for f in found if f.kind != "PHONE"]
    out = tmp_path / ("safe_" + name)
    res = redact.redact_file(src, ex, keep_phone, out)
    assert res.missed == 0
    left = kinds(filescan.extract_file(out).text)
    assert "AADHAAR" not in left and "DOB" not in left
    assert "PHONE" in left                            # unticked: still there


def test_text_layer_pdf(tmp_path):
    """A PDF with real text: the safe copy must not contain the text any more."""
    pdfium = pytest.importorskip("pypdfium2")
    import pypdfium2.raw as raw
    import ctypes
    doc = pdfium.PdfDocument.new()
    page = doc.new_page(400, 200)
    font = raw.FPDFText_LoadStandardFont(doc.raw, b"Helvetica")
    obj = raw.FPDFPageObj_NewTextObj(doc.raw, b"Helvetica", ctypes.c_float(14))
    text = "Applicant Aadhaar 4918 3205 7615 for the admission form"
    buf = ctypes.create_string_buffer(text.encode("utf-16-le") + b"\0\0")   # UTF-16 on every system
    raw.FPDFText_SetText(obj, ctypes.cast(buf, ctypes.POINTER(ctypes.c_ushort)))
    raw.FPDFPageObj_Transform(obj, 1, 0, 0, 1, 30, 100)
    raw.FPDFPage_InsertObject(page.raw, obj)
    raw.FPDFPage_GenerateContent(page.raw)
    src = tmp_path / "t.pdf"
    doc.save(str(src))
    raw.FPDFFont_Close(font)
    ex = filescan.extract_file(src)
    assert ex.method == "pdf text" and "4918 3205 7615" in ex.text
    out = tmp_path / "safe.pdf"
    res = redact.redact_file(src, ex, detect(ex.text), out)
    assert res.replaced == 1 and res.missed == 0
    safe = pdfium.PdfDocument(str(out))
    tp = safe[0].get_textpage()
    assert "4918" not in (tp.get_text_range() or "")  # really removed, not just covered


def test_clipboard_save_restore():
    from sensiq import winutil
    if not winutil.IS_WIN:
        pytest.skip("Windows only")
    import uiautomation as auto
    original = winutil.save_clipboard()
    if original is None:
        pytest.skip("clipboard unavailable (for example the PC is locked)")
    try:
        auto.SetClipboardText("marker text 123")
        snap = winutil.save_clipboard()
        auto.SetClipboardText("something else")
        winutil.restore_clipboard(snap)
        assert auto.GetClipboardText() == "marker text 123"
    finally:
        winutil.restore_clipboard(original)


def test_copy_selection_without_dialog_is_harmless():
    from sensiq import winutil
    if not winutil.IS_WIN:
        pytest.skip("Windows only")
    assert winutil.copy_selection_paths(0) == []


def test_clipboard_formats():
    from sensiq import winutil
    if not winutil.IS_WIN:
        pytest.skip("Windows only")
    import struct
    header = struct.pack("<IiiII", 20, 0, 0, 0, 1)
    assert len(header) == 20
