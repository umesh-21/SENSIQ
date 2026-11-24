"""QR codes, signatures, hard photos, ZIPs, HEIC, old Office files, never-flag words and
large batches. Every value is fake."""
import queue
import threading
import zipfile
from pathlib import Path

import pytest

from sensiq import filescan, redact
from sensiq.config import Config, IgnoreList
from sensiq.detector.engine import DetectionEngine


def _ocr_ok():
    try:
        from sensiq import ocr
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


def scanner(engine):
    from sensiq.sharewatch import FileScanWorker
    return FileScanWorker(engine.detect_all, engine.config, queue.Queue(), threading.Event())


def _font(size=28):
    from PIL import ImageFont
    return ImageFont.truetype("arial.ttf", size)


def _text_image(lines, size=(800, 300), bg="white", fg="black"):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(img)
    for i, t in enumerate(lines):
        d.text((24, 24 + i * 60), t, fill=fg, font=_font())
    return img


def _qr(text, scale=5):
    import numpy as np
    import zxingcpp
    from PIL import Image
    b = zxingcpp.create_barcode(text, zxingcpp.BarcodeFormat.QRCode)
    return Image.fromarray(np.array(b.to_image(scale=scale))).convert("RGB")


# ---- QR codes ---------------------------------------------------------------------------
@needs_ocr
def test_qr_with_private_details_is_found_and_covered(tmp_path, engine):
    pytest.importorskip("zxingcpp")
    img = _text_image(["Event pass"], size=(900, 500))
    img.paste(_qr("Name: Rahul Sharma; Mobile 9876543210"), (500, 120))
    src = tmp_path / "pass.png"
    img.save(src)
    res = scanner(engine)._scan_file(src)
    assert "PHONE" in [f.kind for f in res.findings]
    out = tmp_path / "safe.png"
    redact.redact_file(src, res.extracted, res.findings, out)
    from sensiq import visual
    from PIL import Image
    assert visual.find_codes(Image.open(out)) == []       # the code is covered, no longer readable


@needs_ocr
def test_aadhaar_secure_qr(tmp_path, engine):
    pytest.importorskip("zxingcpp")
    img = _text_image(["Aadhaar card back"], size=(900, 600))
    img.paste(_qr("1" + "234567890" * 60, scale=3), (420, 80))   # long number like a secure QR
    src = tmp_path / "back.png"
    img.save(src)
    res = scanner(engine)._scan_file(src)
    assert "AADHAAR_QR" in [f.kind for f in res.findings]


@needs_ocr
def test_plain_link_qr_is_not_flagged(tmp_path, engine):
    pytest.importorskip("zxingcpp")
    img = _text_image(["Scan for the menu"], size=(800, 500))
    img.paste(_qr("https://example.com/menu"), (450, 120))
    src = tmp_path / "menu.png"
    img.save(src)
    assert scanner(engine)._scan_file(src).findings == []


# ---- signatures, hard photos ---------------------------------------------------------------
@needs_ocr
def test_signature_next_to_label(tmp_path, engine):
    from PIL import ImageDraw
    img = _text_image(["Application form", "Applicant declaration"], size=(900, 520))
    d = ImageDraw.Draw(img)
    d.line([(520, 400), (560, 370), (600, 410), (650, 375), (700, 405), (740, 380)], fill=(20, 20, 90), width=4)
    d.text((540, 425), "Signature", fill="black", font=_font())
    src = tmp_path / "form.png"
    img.save(src)
    res = scanner(engine)._scan_file(src)
    assert "SIGNATURE" in [f.kind for f in res.findings]


@needs_ocr
@pytest.mark.parametrize("turn", [90, 270])
def test_sideways_photo(tmp_path, engine, turn):
    img = _text_image(["Aadhaar 4918 3205 7615", "Mobile 9876543210"]).rotate(turn, expand=True)
    src = tmp_path / "side.png"
    img.save(src)
    res = scanner(engine)._scan_file(src)
    assert {"AADHAAR", "PHONE"} <= {f.kind for f in res.findings}
    out = tmp_path / "safe.png"
    out_res = redact.redact_file(src, res.extracted, res.findings, out)
    assert out_res.missed == 0
    assert not {"AADHAAR", "PHONE"} & {f.kind for f in scanner(engine)._scan_file(out).findings}


@needs_ocr
def test_faint_photo(tmp_path, engine):
    img = _text_image(["Aadhaar 4918 3205 7615"], bg=(200, 200, 200), fg=(165, 165, 165), size=(600, 120))
    src = tmp_path / "faint.png"
    img.save(src)
    assert "AADHAAR" in [f.kind for f in scanner(engine)._scan_file(src).findings]


# ---- formats ----------------------------------------------------------------------------
@needs_ocr
def test_heic_photo(tmp_path, engine):
    pytest.importorskip("pillow_heif")
    if ".heic" not in filescan.IMAGE_EXT:
        pytest.skip("HEIC support not available")
    src = tmp_path / "phone.heic"
    _text_image(["Aadhaar 4918 3205 7615"]).save(src, format="HEIF")
    res = scanner(engine)._scan_file(src)
    assert "AADHAAR" in [f.kind for f in res.findings]
    out = tmp_path / "safe.heic"
    redact.redact_file(src, res.extracted, res.findings, out)
    assert "AADHAAR" not in [f.kind for f in scanner(engine)._scan_file(out).findings]


def test_zip_checked_and_rebuilt(tmp_path, engine):
    from sensiq.sharewatch import ShareContext
    src = tmp_path / "docs.zip"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("notes/a.txt", "Aadhaar 4918 3205 7615")
        z.writestr("b.txt", "nothing private here")
        z.writestr("readme.bin", b"\x00\x01binary")
    sc = scanner(engine)
    handed = []  # the browser path hands the safe copies back directly (no clipboard, no Explorer)
    ctx = ShareContext("browser", "File added to a web page", [str(src)],
                       reply=lambda action, files=None: handed.append((action, files)))
    sc._run_job(("files", ctx, [str(src)]))
    ev = sc.events.get(timeout=10)
    while ev[0] == "scan_start":   # "checking…" note comes first for uploads
        ev = sc.events.get(timeout=10)
    assert ev[0] == "file_report"
    results = ev[2]
    assert [r.name for r in results] == ["docs.zip › notes/a.txt"]
    sc._redact_job(ctx, results, {0: results[0].findings})
    done = sc.events.get(timeout=10)
    assert done[0] == "redact_done" and done[1]
    assert handed and handed[-1][0] == "safe"
    out_zip = Path(handed[-1][1][0])
    assert out_zip.name == "docs.zip"
    with zipfile.ZipFile(out_zip) as z:
        assert sorted(z.namelist()) == ["b.txt", "notes/a.txt", "readme.bin"]   # nothing lost
        assert b"4918 3205 7615" not in z.read("notes/a.txt")
        assert z.read("b.txt") == b"nothing private here"


def test_legacy_doc_read_but_not_rewritten(tmp_path, engine):
    pytest.importorskip("olefile")
    src = tmp_path / "old.doc"   # olefile can't write, so a minimal OLE file is built by hand
    ole = _minimal_ole("WordDocument", "Applicant Aadhaar 4918 3205 7615 mobile 9876543210".encode("utf-16-le"))
    src.write_bytes(ole)
    ex = filescan.extract_file(src)
    assert not ex.can_redact
    assert {"AADHAAR", "PHONE"} <= {f.kind for f in engine.detect_all(ex.text)}


def _minimal_ole(stream_name: str, data: bytes) -> bytes:
    """Smallest valid OLE2 compound file with one stream (512-byte sectors, regular FAT)."""
    import struct
    sector = 512
    data = data + b"\x00" * ((-len(data)) % sector)
    n_data = max(1, len(data) // sector)
    # sectors: 0 = FAT, 1 = directory, 2.. = stream data (stored in big FAT; size >= 4096 required)
    data = data + b"\x00" * max(0, 4096 - len(data))
    n_data = len(data) // sector
    fat = [0xFFFFFFFD, 0xFFFFFFFE] + [i + 3 for i in range(2, 2 + n_data - 1)] + [0xFFFFFFFE]
    fat += [0xFFFFFFFF] * (128 - len(fat))
    header = bytearray(512)
    header[0:8] = bytes.fromhex("D0CF11E0A1B11AE1")
    struct.pack_into("<HHHHH", header, 24, 0x3E, 3, 0xFFFE, 9, 6)
    struct.pack_into("<I", header, 44, 1)            # number of FAT sectors
    struct.pack_into("<I", header, 48, 1)            # first directory sector
    struct.pack_into("<I", header, 56, 4096)         # mini stream cutoff
    struct.pack_into("<I", header, 60, 0xFFFFFFFE)   # first mini FAT sector
    struct.pack_into("<I", header, 68, 0xFFFFFFFE)   # first DIFAT sector
    struct.pack_into("<I", header, 76, 0)            # DIFAT[0] = FAT in sector 0
    for i in range(1, 109):
        struct.pack_into("<I", header, 76 + i * 4, 0xFFFFFFFF)

    def entry(name, typ, start, size, child=0xFFFFFFFF):
        e = bytearray(128)
        n = name.encode("utf-16-le") + b"\x00\x00"
        e[0:len(n)] = n
        struct.pack_into("<H", e, 64, len(n))
        e[66] = typ
        e[67] = 1
        struct.pack_into("<III", e, 68, 0xFFFFFFFF, 0xFFFFFFFF, child)
        struct.pack_into("<I", e, 116, start)
        struct.pack_into("<I", e, 120, size)
        return bytes(e)
    directory = entry("Root Entry", 5, 0xFFFFFFFE, 0, child=1) + entry(stream_name, 2, 2, len(data))
    directory += b"\x00" * (sector - len(directory))
    return bytes(header) + struct.pack("<128I", *fat) + directory + data


# ---- never flag, batches -------------------------------------------------------------------
def test_never_flag_words():
    cfg = Config.defaults()
    cfg.data["presidio"]["enabled"] = False
    cfg.data["ner"]["enabled"] = False
    eng = DetectionEngine(cfg, IgnoreList(), "/nonexistent")
    assert [f.text for f in eng.detect("Name: Grace Thomas")] == ["Grace Thomas"]
    cfg.data["never_flag_words"] = ["grace thomas"]
    assert eng.detect("Name: Grace Thomas") == []


def test_no_ten_file_cap(tmp_path, engine):
    from sensiq.sharewatch import ShareContext
    paths = []
    for i in range(25):
        p = tmp_path / f"f{i}.txt"
        p.write_text(f"mobile 98765432{i:02d}", encoding="utf-8")
        paths.append(str(p))
    sc = scanner(engine)
    sc._run_job(("files", ShareContext("copy", "Copied file", paths), paths))
    events = []
    while not sc.events.empty():
        events.append(sc.events.get())
    report = [e for e in events if e[0] == "file_report"][0]
    assert len(report[2]) == 25
    assert any(e[0] == "scan_progress" for e in events)   # "Checking 25 files…"


def test_opened_file_offer(tmp_path, engine, monkeypatch):
    """Opening a document (it appears in Recent items) leads to a 'Check it' offer, only when switched on."""
    import time as _t
    from sensiq.sharewatch import OpenedFileWatcher
    recent = tmp_path / "Microsoft" / "Windows" / "Recent"
    recent.mkdir(parents=True)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    doc = tmp_path / "statement.pdf"
    doc.write_bytes(b"%PDF-1.4\n")
    cfg = Config.defaults()
    cfg.data["file_scan"]["opened_files"] = True
    events, stop = queue.Queue(), threading.Event()
    w = OpenedFileWatcher(None, cfg, events, stop)
    w.start()
    _t.sleep(1.0)
    import comtypes.client
    sh = comtypes.client.CreateObject("WScript.Shell", dynamic=True)
    link = sh.CreateShortcut(str(recent / "statement.pdf.lnk"))
    link.TargetPath = str(doc)
    link.Save()
    try:
        ev = events.get(timeout=8)
    finally:
        stop.set()
    assert ev == ("offer_check", str(doc))


def test_folder_searched_recursively(tmp_path, engine):
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "b" / "deep.txt").write_text("PAN ABCPE1234F", encoding="utf-8")
    files = scanner(engine)._expand([str(tmp_path / "a")])
    assert [f.name for f in files] == ["deep.txt"]
