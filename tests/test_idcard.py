"""ID-card photos: dense QR codes that can't be decoded, initials after names, PIN codes at
the end of an address, a second (small) copy of the Aadhaar number. Every value is fake."""
import queue
import threading

import pytest

from sensiq import filescan, ocr, visual
from sensiq.config import Config, IgnoreList
from sensiq.detector.engine import DetectionEngine, extend_initials
from sensiq.detector.types import Finding
from sensiq.masking import suggest


def _ocr_ok():
    try:
        return ocr.status()["ocr_loaded"]
    except Exception:
        return False


needs_ocr = pytest.mark.skipif(not _ocr_ok(), reason="Windows OCR not available")


@pytest.fixture(scope="module")
def engine():
    cfg = Config.defaults()
    cfg.data["ner"]["enabled"] = False
    cfg.data["presidio"]["enabled"] = False
    return DetectionEngine(cfg, IgnoreList(), "/nonexistent")


def _font(size):
    from PIL import ImageFont
    return ImageFont.truetype("arial.ttf", size)


def _dense_qr(side=240):
    """A version-40-size QR squeezed to ~1.4 px per square and blurred, like the Aadhaar
    secure QR in a phone photo: visible, but not decodable."""
    import numpy as np
    import zxingcpp
    from PIL import Image, ImageFilter
    b = zxingcpp.create_barcode("1" + "234567890" * 300, zxingcpp.BarcodeFormat.QRCode)
    img = Image.fromarray(np.array(b.to_image(scale=1))).convert("RGB")
    return img.resize((side, side), Image.BILINEAR).filter(ImageFilter.GaussianBlur(0.9))


def _card(with_id_words=True):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (800, 520), (236, 244, 230))
    d = ImageDraw.Draw(img)
    if with_id_words:
        d.text((200, 20), "Government of India", fill="black", font=_font(30))
    else:
        d.text((200, 20), "Conference badge", fill="black", font=_font(30))
    img.paste(_dense_qr(), (530, 240))
    return img


def test_dense_qr_is_located_even_if_not_decodable():
    pytest.importorskip("zxingcpp")
    import zxingcpp
    img = _card()
    assert zxingcpp.read_barcodes(img) == []          # zxing can't read it...
    codes = visual.find_codes(img)
    assert [c[0] for c in codes] == [visual.UNREADABLE]  # ...but we see where it is
    l, t, r, b = codes[0][2]
    assert 515 <= l <= 560 and 225 <= t <= 270 and 740 <= r <= 790 and 450 <= b <= 500


@needs_ocr
@pytest.mark.parametrize("id_words, kind", [(True, "AADHAAR_QR"), (False, "QR_CODE")])
def test_unreadable_qr_is_reported_and_covered(tmp_path, engine, id_words, kind):
    pytest.importorskip("zxingcpp")
    from sensiq import redact
    from sensiq.sharewatch import FileScanWorker
    src = tmp_path / "card.png"
    _card(id_words).save(src)
    res = FileScanWorker(engine.detect_all, engine.config, queue.Queue(), threading.Event())._scan_file(src)
    assert kind in [f.kind for f in res.findings]
    out = tmp_path / "safe.png"
    redact.redact_file(src, res.extracted, res.findings, out)
    from PIL import Image
    assert visual.locate_qr(Image.open(out)) == []    # the code is gone


def test_text_page_is_not_a_qr_code():
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (1224, 1584), "white")
    d = ImageDraw.Draw(img)
    line = "The quick brown fox jumps over the lazy dog 0123456789 " * 3
    for i in range(110):
        d.text((100, 100 + i * 12), line[i % 7:][:160], fill="black", font=_font(10))
    assert visual.locate_qr(img) == []
    small = img.resize((612, 792))
    assert visual.locate_qr(small) == []


# ---- names with initials ----------------------------------------------------------------
@pytest.mark.parametrize("text, name, expect", [
    ("Ramesh Kumar N P\nDOB: 12/03/1997", "Ramesh Kumar", "Ramesh Kumar N P"),
    ("Dear N. P. Ramesh, welcome", "Ramesh", "N. P. Ramesh"),
    ("Dear N P Ramesh, welcome", "Ramesh", "N P Ramesh"),
    ("Signed K.R. Nair.", "Nair", "K.R. Nair"),
    ("Hi, I am Rahul Sharma I am fine", "Rahul Sharma", "Rahul Sharma"),
    ("I Rahul Sharma declare", "Rahul Sharma", "Rahul Sharma"),
    ("met Rahul. A good day", "Rahul", "Rahul"),
])
def test_initials_join_the_name(text, name, expect):
    s = text.index(name)
    out = extend_initials(text, [Finding(s, s + len(name), "PERSON", name)])
    assert out[0].text == expect


# ---- PIN code at the end of an address ------------------------------------------------------
def test_pin_after_address(engine):
    text = "Address: C/O: Suresh, Kottayam,\nAlappuzha, Kerala - 688525"
    assert "688525" in [f.text for f in engine.detect(text) if f.kind == "PINCODE"]


@pytest.mark.parametrize("text", ["Order 688525 shipped in 3 boxes", "Invoice no 560001 dated 12/03/2024"])
def test_six_digits_without_address_not_pin(engine, text):
    assert "PINCODE" not in [f.kind for f in engine.detect(text)]


# ---- the small second copy of the Aadhaar number ---------------------------------------------
def test_misread_piece_of_aadhaar_is_covered():
    # big number read fine; the tiny copy only as "2SS4" (OCR's reading of 2554)
    ex = filescan.Extracted(text="2554 8151 2997\n2SS4\nxxxx 2997", kind="image")
    ex.words = [(0, 4, (10, 10, 50, 20)), (5, 9, (55, 10, 95, 20)), (10, 14, (100, 10, 140, 20)),
                (15, 19, (300, 100, 316, 106)), (20, 24, (10, 40, 50, 50)), (25, 29, (55, 40, 95, 50))]
    aadhaar = Finding(0, 14, "AADHAAR", "2554 8151 2997")
    found = visual.id_fragments(ex, [aadhaar])
    assert [(f.text, f.kind) for f in found] == [("2SS4", "AADHAAR")]   # "2997" (shown last 4) is left
    assert suggest(found[0]) == "XXXX"
    l, t, r, b = ex.words[3][2]
    assert l <= 300 and r >= 300 + 14 * 4 - 2    # box stretched over the whole printed number


# ---- "where" drop-down on the card -----------------------------------------------------------
def test_where_shows_the_line_and_page():
    from sensiq.overlay import where_found
    text = "Certificate\nThis is to certify that\nPriya Sharma has presented a paper at the Bhilai meet"
    ex = filescan.Extracted(text=text, kind="pdf")
    ex.pages = [filescan.PdfPage(index=0, start=0, text_len=12), filescan.PdfPage(index=1, start=12, text_len=99)]
    s = text.index("Bhilai")
    info = where_found(Finding(s, s + 6, "LOCATION", "Bhilai"), ex)
    assert info["page"] == "Page 2" and info["match"] == "Bhilai"
    assert info["before"].endswith("at the ") and "presented" in info["before"]
    s = text.index("Priya")
    info = where_found(Finding(s, s + 12, "PERSON", "Priya Sharma"), ex)
    assert info["before"] == "" and info["after"].startswith(" has presented")


def test_where_short_line_gets_line_above_and_secrets_stay_hidden():
    from sensiq.overlay import where_found
    text = "Address: C/O Suresh, Kerala\n688525\nwifi password is Tiger@2024"
    ex = filescan.Extracted(text=text, kind="image")
    s = text.index("688525")
    info = where_found(Finding(s, s + 6, "PINCODE", "688525"), ex)
    assert info["prev"] == "Address: C/O Suresh, Kerala"
    s = text.index("Tiger")
    info = where_found(Finding(s, s + 10, "PASSWORD", "Tiger@2024"), ex)
    assert "Tiger@2024" not in info["match"] + info["before"] + info["after"]


def test_where_for_pictures():
    from sensiq.overlay import where_found
    ex = filescan.Extracted(text="Government of India\n\ue000\ue001", kind="image")
    info = where_found(Finding(20, 22, "AADHAAR_QR", "QR code on an ID card", 0.9, "visual"), ex)
    assert info["match"] == "QR code in the picture"


def test_not_private_can_be_undone(tmp_path):
    import types
    from sensiq.app import App
    cfg = Config.defaults()
    ign = IgnoreList(path=tmp_path / "ignored.json")
    fake = types.SimpleNamespace(config=cfg, ignore=ign)
    cfg.save = lambda: None
    name = Finding(0, 12, "PERSON", "Suresh Kumar")
    phone = Finding(20, 30, "PHONE", "9876543210")
    App.not_private(fake, [name, phone])
    assert "Suresh Kumar" in cfg.data["never_flag_words"] and ign.contains(phone.fingerprint())
    App.flag_again(fake, [name, phone])
    assert "Suresh Kumar" not in cfg.data["never_flag_words"] and not ign.contains(phone.fingerprint())
