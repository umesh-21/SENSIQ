"""Turns files and images into text for the detectors, and remembers where each piece of
text came from so the redactor can later cover exactly those spots.

Images use Windows' built-in OCR. PDFs use their text layer, and pages without one
(scans, photos) are rendered and OCR'd. Word, OpenDocument and plain text files are
read directly. File contents are never logged.
"""
from __future__ import annotations

import logging
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from . import ocr

log = logging.getLogger(__name__)

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".jfif", ".bmp", ".gif", ".tif", ".tiff", ".webp", ".heic", ".heif"}
LEGACY_EXT = {".doc", ".xls", ".ppt"}     # old binary Office files: read, but no safe copy
ARCHIVE_EXT = {".zip"}

try:  # iPhone photos (HEIC/HEIF)
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:  # pragma: no cover - optional
    IMAGE_EXT -= {".heic", ".heif"}
PDF_EXT = {".pdf"}
DOCX_EXT = {".docx", ".docm", ".dotx"}
ODT_EXT = {".odt"}
PPTX_EXT = {".pptx"}
XLSX_EXT = {".xlsx", ".xlsm"}
TEXT_EXT = {".txt", ".csv", ".tsv", ".json", ".md", ".log", ".xml", ".html", ".htm", ".rtf",
            ".yaml", ".yml", ".ini", ".cfg", ".env", ".vcf", ".eml", ".sql", ".py", ".js"}
OFFICE_PARTS = {
    **{e: r"word/(document|header\d*|footer\d*|footnotes|endnotes|comments)\.xml" for e in DOCX_EXT},
    **{e: r"content\.xml" for e in ODT_EXT},
    **{e: r"ppt/(slides/slide\d+|notesSlides/notesSlide\d+)\.xml" for e in PPTX_EXT},
    **{e: r"xl/(sharedStrings|worksheets/sheet\d+)\.xml" for e in XLSX_EXT},
}
SUPPORTED = IMAGE_EXT | PDF_EXT | set(OFFICE_PARTS) | TEXT_EXT | LEGACY_EXT | ARCHIVE_EXT
MAX_TEXT_BYTES = 4_000_000
PDF_SCALE = 2.0  # render scale for OCR and for redacted output (144 dpi)

Word = Tuple[int, int, Tuple[float, float, float, float]]  # span in text, box in pixels


@dataclass
class PdfPage:
    index: int
    start: int                      # offset of this page's text in Extracted.text
    text_len: int                   # characters from the PDF text layer (char index = offset - start)
    ocr_words: List[Word] = field(default_factory=list)  # spans relative to Extracted.text, boxes at PDF_SCALE


@dataclass
class Extracted:
    text: str = ""
    method: str = ""              # "text", "pdf text", "OCR", "pdf text + OCR"
    note: Optional[str] = None    # why something couldn't be read
    kind: str = ""                # "image", "pdf", "office", "text"
    words: List[Word] = field(default_factory=list)       # images: OCR word boxes (first frame)
    frame_words: List[List[Word]] = field(default_factory=list)  # multi-page images: boxes per frame
    pages: List[PdfPage] = field(default_factory=list)    # PDFs
    page_count: int = 0
    # things that aren't text: ("CODE", start, end, decoded text) / ("FACE"|"SIGNATURE", start, end, "")
    # start/end point at placeholder text whose words map to the item's box, so redaction covers it
    visual: List[Tuple[str, int, int, str]] = field(default_factory=list)
    can_redact: bool = True       # False for formats we can read but not rewrite safely (.doc, .xls)
    unchecked: bool = False       # True when the file couldn't be checked at all (password, damaged)


MIN_REGION = (60, 24)   # pictures smaller than this on a PDF page (logos, icons) aren't OCR'd
MARK = ""   # placeholder text for faces/signatures (private-use characters, never detected)
VISUAL_MAX_PAGES = 40   # PDFs up to this many pages also get every page searched for QR codes and faces


_executor = None


def _pool():
    global _executor
    if _executor is None:
        from concurrent.futures import ThreadPoolExecutor
        _executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="visual")
    return _executor


def read_piece(image, base: int, ox: float = 0, oy: float = 0, do_ocr: bool = True, skip=None,
               page: bool = False):
    """OCR an image (or part of a page) and find codes, faces and signatures in it.
    Returns (text, words, visual items); offsets start at `base`, boxes are shifted by (ox, oy).
    Codes/faces whose centre lies inside a `skip` box (already searched) are left out."""
    from . import visual
    from .paths import models_dir
    on = visual.ENABLED
    # QR codes and faces are searched on another thread while OCR runs (they don't depend on it)
    pool = _pool()
    codes_job = pool.submit(visual.find_codes, image) if on["codes"] else None
    faces_job = pool.submit(visual.find_faces, image, models_dir(), not page) if on["faces"] else None
    text, words = ocr.recognize(image, thorough=not page) if do_ocr else ("", [])
    out_words: List[Word] = [(base + s, base + e, (l + ox, t + oy, r + ox, b + oy)) for s, e, (l, t, r, b) in words]
    items: List[Tuple[str, int, int, str]] = []
    parts = [text]
    pos = base + len(text)

    def add(kind: str, label: str, box, detail: str = "") -> None:
        nonlocal pos
        l, t, r, b = box
        cx, cy = (l + r) / 2, (t + b) / 2
        if skip and any(s[0] <= cx <= s[2] and s[1] <= cy <= s[3] for s in skip):
            return
        box = (l + ox, t + oy, r + ox, b + oy)
        start = pos + 1
        parts.append("\n" + label)
        k = start
        for tok in label.split(" "):  # every token of the placeholder maps to the item's box
            if tok:
                out_words.append((k, k + len(tok), box))
            k += len(tok) + 1
        pos = start + len(label)
        items.append((kind, start, pos, detail))

    for fmt, content, box in (codes_job.result() if codes_job else []):
        line = " ".join(content.split())[:3000]
        if line:
            add("CODE", line, box, detail=fmt)
        elif fmt == visual.UNREADABLE:  # a QR code we can see but not decode
            add("CODE", MARK, box, detail=fmt)
    for box in (faces_job.result() if faces_job else []):
        add("FACE", MARK, box)
    if do_ocr and on["signatures"]:
        for box in visual.find_signatures(image, text, words):
            add("SIGNATURE", MARK, box)
    return "".join(parts), out_words, items


def count_pages(path: Path) -> int:
    """Pages in a PDF or frames in an image (1 for anything else); cheap."""
    ext = Path(path).suffix.lower()
    try:
        if ext in PDF_EXT:
            import pypdfium2 as pdfium
            doc = pdfium.PdfDocument(str(path))
            n = len(doc)
            doc.close()
            return n
        if ext in IMAGE_EXT:
            from PIL import Image
            with Image.open(path) as im:
                return int(getattr(im, "n_frames", 1) or 1)
    except Exception:
        pass
    return 1


def is_supported(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED


def open_image(path: Path):
    """Image as displayed (EXIF rotation applied), first frame only."""
    return open_frames(path)[0]


def open_frames(path: Path, limit: int = 0) -> list:
    """Every page of a multi-page image (TIFF scans, GIF...), as displayed."""
    from PIL import Image, ImageOps
    frames = []
    with Image.open(path) as im:
        n = int(getattr(im, "n_frames", 1) or 1)
        for k in range(n if not limit else min(n, limit)):
            im.seek(k)
            frame = ImageOps.exif_transpose(im) if k == 0 else im
            frames.append(frame.convert("RGB").copy())
    return frames


def _extract_frames(path: Path, max_pages: int) -> Extracted:
    frames = open_frames(path, max_pages)
    if len(frames) == 1:
        return extract_image(frames[0])
    out = Extracted(method="OCR", kind="image")
    texts: List[str] = []
    pos = 0
    for k, frame in enumerate(frames):
        if k:
            texts.append("\n\f")
            pos += 2
        try:
            text, words, items = read_piece(frame, pos)
        except Exception as exc:
            out.note = f"couldn't read page {k + 1} ({exc})"
            text, words, items = "", [], []
        out.frame_words.append(words)
        out.visual.extend(items)
        texts.append(text)
        pos += len(text)
    out.words = out.frame_words[0]
    out.text = "".join(texts)
    out.page_count = len(frames)
    return out


def extract_image(image) -> Extracted:
    try:
        text, words, items = read_piece(image.convert("RGB"), 0)
        return Extracted(text, "OCR", kind="image", words=words, visual=items)
    except Exception as exc:
        return Extracted(note=f"couldn't read the image ({exc})", kind="image", unchecked=True)


def extract_file(path: Path, max_mb: float = 200, max_pdf_pages: int = 0) -> Extracted:
    """Text of a file. max_pdf_pages=0 means every page (and every frame of an image)."""
    path = Path(path)
    ext = path.suffix.lower()
    try:
        size = path.stat().st_size
    except OSError as exc:
        return Extracted(note=f"couldn't open the file ({exc.strerror or exc})")
    if max_mb and size > max_mb * 1024 * 1024:
        return Extracted(note=f"skipped: larger than {max_mb:g} MB")
    try:
        if ext in IMAGE_EXT:
            return _extract_frames(path, max_pdf_pages)
        if ext in PDF_EXT:
            return _pdf(path, max_pdf_pages)
        if ext in OFFICE_PARTS:
            if _is_encrypted_office(path):
                return Extracted(note="it is password-protected", kind="office", unchecked=True)
            return Extracted(_xml_text(path, OFFICE_PARTS[ext]), "text", kind="office")
        if ext in LEGACY_EXT:
            return _legacy_office(path)
        if ext in TEXT_EXT:
            text, _ = read_text(path)
            if ext in (".html", ".htm", ".xml"):
                text = re.sub(r"<[^>]+>", " ", text)
            return Extracted(text, "text", kind="text")
    except zipfile.BadZipFile:
        return Extracted(note="the file is damaged or password-protected", unchecked=True)
    except Exception as exc:
        log.debug("extract failed for %s", path.suffix, exc_info=True)
        return Extracted(note=f"couldn't read the file ({type(exc).__name__})", unchecked=True)
    return Extracted(note="this file type isn't checked")


def _is_encrypted_office(path: Path) -> bool:
    """Password-protected .docx/.xlsx/.pptx are stored as an encrypted OLE package, not a ZIP."""
    try:
        import olefile
        if not olefile.isOleFile(str(path)):
            return False
        with olefile.OleFileIO(str(path)) as ole:
            return ole.exists("EncryptionInfo") or ole.exists("EncryptedPackage")
    except Exception:
        return False


_RUN8 = re.compile(rb"[\x20-\x7e\t\r\n]{4,}")
_RUN16 = re.compile(rb"(?:[\x20-\x7e\t\r\n]\x00){4,}")


def _legacy_office(path: Path) -> Extracted:
    """Old binary .doc/.xls/.ppt: their text is pulled out of the file's streams so it can be
    checked. A safe copy can't be written reliably for these formats."""
    import olefile
    note = "old Office format: details can be found but not replaced. Save it as .docx/.xlsx/.pptx first"
    try:
        if not olefile.isOleFile(str(path)):
            return Extracted(note="the file is damaged", kind="office", unchecked=True)
        with olefile.OleFileIO(str(path)) as ole:
            if ole.exists("EncryptionInfo") or ole.exists("EncryptedPackage"):
                return Extracted(note="it is password-protected", kind="office", unchecked=True)
            chunks: List[str] = []
            for entry in ole.listdir(streams=True, storages=False):
                data = ole.openstream(entry).read(MAX_TEXT_BYTES)
                chunks += [m.group().decode("utf-16-le", "ignore") for m in _RUN16.finditer(data)]
                chunks += [m.group().decode("cp1252", "ignore") for m in _RUN8.finditer(data)]
    except Exception as exc:
        return Extracted(note=f"couldn't read the file ({type(exc).__name__})", kind="office", unchecked=True)
    seen, lines = set(), []
    for c in chunks:
        c = " ".join(c.split())
        if len(c) >= 4 and c not in seen:
            seen.add(c)
            lines.append(c)
    return Extracted("\n".join(lines), "text", note=note, kind="office", can_redact=False)


def read_text(path: Path) -> Tuple[str, str]:
    """(text, encoding) of a plain-text file."""
    raw = Path(path).read_bytes()[:MAX_TEXT_BYTES]
    for enc in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace"), "utf-8"


def _xml_text(path: Path, member_pattern: str) -> str:
    import html
    pat = re.compile(member_pattern)
    parts: List[str] = []
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if pat.fullmatch(name):
                xml = z.read(name).decode("utf-8", "replace")
                xml = re.sub(r"</(?:w:p|a:p|text:p|text:h|row)>|<w:br/>|<w:tab/>", "\n", xml)
                parts.append(re.sub(r"<[^>]+>", " ", xml))
                if sum(map(len, parts)) > MAX_TEXT_BYTES:
                    break
    return html.unescape("\n".join(parts))


def page_to_device(page, bitmap_w: int, bitmap_h: int, x: float, y: float) -> Tuple[int, int]:
    """PDF page coordinates -> pixel position in a render of the given size (handles rotation)."""
    import ctypes
    import pypdfium2.raw as raw
    dx, dy = ctypes.c_int(), ctypes.c_int()
    raw.FPDF_PageToDevice(page.raw, 0, 0, bitmap_w, bitmap_h, 0, x, y, ctypes.byref(dx), ctypes.byref(dy))
    return dx.value, dy.value


def _picture_regions(page, render_w: int, render_h: int) -> List[Tuple[int, int, int, int]]:
    """Pixel boxes (in a render of the given size) of the pictures placed on a page."""
    import pypdfium2.raw as raw
    boxes = []
    try:
        objs = list(page.get_objects(filter=(raw.FPDF_PAGEOBJ_IMAGE,), max_depth=4))
    except Exception:
        return boxes
    for obj in objs:
        try:  # pypdfium2 5 calls it get_bounds, older versions get_pos
            l, b, r, t = obj.get_bounds() if hasattr(obj, "get_bounds") else obj.get_pos()
        except Exception:
            continue
        (x1, y1), (x2, y2) = page_to_device(page, render_w, render_h, l, t), page_to_device(page, render_w, render_h, r, b)
        x1, x2 = sorted((max(0, x1), min(render_w, x2)))
        y1, y2 = sorted((max(0, y1), min(render_h, y2)))
        if x2 - x1 >= MIN_REGION[0] and y2 - y1 >= MIN_REGION[1]:
            boxes.append((x1, y1, x2, y2))
    return boxes


def _pdf(path: Path, max_pages: int) -> Extracted:
    """Every page's text layer; pages without one (scans) are OCR'd whole, and pictures placed
    on text pages (a scanned ID pasted into a typed form) are OCR'd too."""
    import pypdfium2 as pdfium

    try:
        doc = pdfium.PdfDocument(str(path))
    except pdfium.PdfiumError as exc:
        msg = "it is password-protected" if "password" in str(exc).lower() else "the PDF is damaged"
        return Extracted(note=msg, kind="pdf", unchecked=True)
    out = Extracted(method="pdf text", kind="pdf")
    texts: List[str] = []
    pos = 0
    ocr_pages = 0
    failed = 0
    try:
        n = len(doc)
        out.page_count = n
        last = n if not max_pages else min(n, max_pages)
        for i in range(last):
            if i:
                texts.append("\n\f")
                pos += 2
            page = doc[i]
            textpage = page.get_textpage()
            text = textpage.get_text_range() or ""
            textpage.close()
            info = PdfPage(i, pos, len(text))
            scanned = len(text.strip()) < 25
            regions: List[Tuple[int, int, int, int]] = []
            image = None
            if not scanned:
                w, h = (int(round(v * PDF_SCALE)) for v in page.get_size())
                regions = _picture_regions(page, w, h)
                # a picture covering (nearly) the whole page under a text layer is a searchable scan:
                # its text layer already holds the words
                regions = [r for r in regions if (r[2] - r[0]) * (r[3] - r[1]) < 0.8 * w * h]
            whole_page_visuals = not scanned and n <= VISUAL_MAX_PAGES  # QR codes / faces drawn anywhere
            if scanned or regions or whole_page_visuals:
                try:
                    image = page.render(scale=PDF_SCALE).to_pil()
                except Exception:
                    image = None
            pieces = [(0, 0, image, True)] if scanned and image is not None else \
                [(r[0], r[1], image.crop(r), True) for r in regions] if image is not None else []
            if whole_page_visuals and image is not None:
                pieces.append((0, 0, image, False))  # codes and faces only; the text layer has the words
            for ox, oy, part, do_ocr in pieces:
                base = pos + len(text) + 1
                try:
                    otext, owords, items = read_piece(part, base, ox, oy, do_ocr=do_ocr,
                                                      skip=None if do_ocr else regions, page=True)
                except Exception:
                    failed += 1
                    continue
                if not otext.strip() and not items:
                    continue
                text = text + "\n" + otext
                info.ocr_words.extend(owords)
                out.visual.extend(items)
            if scanned:
                ocr_pages += 1
            out.pages.append(info)
            texts.append(text)
            pos += len(text)
            page.close()
        if last < n:
            out.note = f"only the first {last} of {n} pages were checked (see max_pdf_pages in settings)"
        elif failed:
            out.note = f"{failed} picture(s) couldn't be read"
    finally:
        doc.close()
    if ocr_pages:
        out.method = "OCR" if ocr_pages == len(out.pages) else "pdf text + OCR"
    elif any(p.ocr_words for p in out.pages):
        out.method = "pdf text + pictures OCR"
    out.text = "".join(texts)
    return out

