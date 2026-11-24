"""Makes a safe copy of an image or file with the chosen details replaced.

* Images: each value is covered with a solid box showing its masked form
  (e.g. "XXXX XXXX 7615"). Boxes come from the OCR word positions.
* PDFs: every page is rendered to an image and the values are covered the same way,
  so the hidden text is really gone (a box drawn over live PDF text would still leave
  the text copyable underneath). The safe PDF's text can't be selected afterwards.
* Word / PowerPoint / Excel / OpenDocument and text files: values are replaced with
  their masked form, keeping the rest of the file as it was.

The original is never modified.
"""
from __future__ import annotations

import logging
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple
from xml.sax.saxutils import escape

from . import filescan
from .detector.types import Finding
from .masking import suggest

log = logging.getLogger(__name__)

BOX_FILL = (20, 24, 33)
BOX_TEXT = (255, 255, 255)


@dataclass
class RedactOutcome:
    replaced: int = 0
    missed: int = 0


# ---- drawing -------------------------------------------------------------------------
def _font(size: int):
    from PIL import ImageFont
    for name in ("arialbd.ttf", "segoeuib.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, max(8, size))
        except OSError:
            continue
    return ImageFont.load_default()


def _union(boxes: Iterable[Tuple[float, float, float, float]]):
    boxes = list(boxes)
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def _cover(draw, box, label: str) -> None:
    l, t, r, b = box
    pad = min(max(2.0, (b - t) * 0.18), 6 + (b - t) * 0.03)  # text lines: 18%; big boxes (QR, photo): less
    l, t, r, b = l - pad, t - pad, r + pad, b + pad
    draw.rectangle((l, t, r, b), fill=BOX_FILL)
    if not label:
        return
    size = int((b - t) * 0.62)
    font = _font(size)
    while size > 8 and draw.textlength(label, font=font) > (r - l) - 4:
        size -= 1
        font = _font(size)
    if draw.textlength(label, font=font) <= (r - l) - 2:
        draw.text(((l + r) / 2, (t + b) / 2), label, fill=BOX_TEXT, font=font, anchor="mm")


def _line_groups(words: Sequence[filescan.Word], start: int, end: int) -> List[List[Tuple[float, float, float, float]]]:
    """Boxes of the words overlapping [start, end), grouped by text line."""
    groups: List[List] = []
    for s, e, box in words:
        if e <= start or s >= end:
            continue
        if groups:
            _, top, _, bottom = _union(groups[-1])
            centre = (box[1] + box[3]) / 2
            if top - 1 <= centre <= bottom + 1 or top <= box[1] <= bottom:  # same text line
                groups[-1].append(box)
                continue
        groups.append([box])
    return groups


def _label(f: Finding, style: str) -> str:
    return suggest(f, style)


def redact_image(image, extracted: filescan.Extracted, findings: Sequence[Finding], style: str = "partial",
                 words: Optional[Sequence[filescan.Word]] = None):
    from PIL import ImageDraw
    out = image.convert("RGB").copy()
    draw = ImageDraw.Draw(out)
    result = RedactOutcome()
    words = extracted.words if words is None else words
    for f in findings:
        groups = _line_groups(words, f.start, f.end)
        if not groups:
            result.missed += 1
            continue
        for i, g in enumerate(groups):
            _cover(draw, _union(g), _label(f, style) if i == 0 else "")
        result.replaced += 1
    return out, result


# ---- PDF -----------------------------------------------------------------------------
def _pdf_char_boxes(page, textpage, first: int, last: int, bitmap_w: int, bitmap_h: int):
    """Device-pixel boxes of PDF characters [first, last) for a render of the given size."""
    import ctypes
    import pypdfium2.raw as raw
    boxes = []
    for i in range(first, last):
        l, b, r, t = textpage.get_charbox(i)
        if r - l <= 0 or t - b <= 0:
            continue
        pts = []
        for px, py in ((l, t), (r, b)):
            dx, dy = ctypes.c_int(), ctypes.c_int()
            raw.FPDF_PageToDevice(page.raw, 0, 0, bitmap_w, bitmap_h, 0, px, py, ctypes.byref(dx), ctypes.byref(dy))
            pts.append((dx.value, dy.value))
        (x1, y1), (x2, y2) = pts
        boxes.append((min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)))
    return boxes


def redact_pdf(path: Path, extracted: filescan.Extracted, findings: Sequence[Finding], out_path: Path,
               style: str = "partial") -> RedactOutcome:
    import pypdfium2 as pdfium
    from PIL import ImageDraw

    result = RedactOutcome()
    by_page = {p.index: p for p in extracted.pages}
    pending = list(findings)
    images = []
    doc = pdfium.PdfDocument(str(path))
    try:
        for i in range(len(doc)):
            page = doc[i]
            img = page.render(scale=filescan.PDF_SCALE).to_pil().convert("RGB")
            info = by_page.get(i)
            if info is not None:
                draw = ImageDraw.Draw(img)
                textpage = page.get_textpage()
                for f in list(pending):
                    page_end = info.start + info.text_len
                    if info.start <= f.start < page_end:  # in the PDF text layer
                        boxes = _pdf_char_boxes(page, textpage, f.start - info.start,
                                                min(f.end, page_end) - info.start, img.width, img.height)
                        groups = _line_groups([(k, k + 1, b) for k, b in enumerate(boxes)], 0, len(boxes))
                    elif info.ocr_words and any(s <= f.start < e or f.start <= s < f.end for s, e, _ in info.ocr_words):
                        groups = _line_groups(info.ocr_words, f.start, f.end)
                    else:
                        continue
                    pending.remove(f)
                    if not groups:
                        result.missed += 1
                        continue
                    for k, g in enumerate(groups):
                        _cover(draw, _union(g), _label(f, style) if k == 0 else "")
                    result.replaced += 1
                textpage.close()
            images.append(img)
            page.close()
    finally:
        doc.close()
    result.missed += len(pending)
    dpi = 72 * filescan.PDF_SCALE
    images[0].save(out_path, "PDF", save_all=True, append_images=images[1:], resolution=dpi)
    return result


# ---- documents and text ---------------------------------------------------------------
def _replace_all(text: str, values: Sequence[Tuple[str, str]]) -> Tuple[str, int]:
    done = 0
    for old, new in sorted(values, key=lambda v: -len(v[0])):
        if old and old in text:
            text = text.replace(old, new)
            done += 1
    return text, done


def redact_office(path: Path, findings: Sequence[Finding], out_path: Path, style: str = "partial") -> RedactOutcome:
    pat = re.compile(filescan.OFFICE_PARTS[path.suffix.lower()])
    pairs = [(escape(f.text), escape(_label(f, style))) for f in findings]
    found = set()
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if pat.fullmatch(item.filename):
                xml = data.decode("utf-8")
                for old, new in pairs:
                    if old in xml:
                        found.add(old)
                xml, _ = _replace_all(xml, pairs)
                data = xml.encode("utf-8")
            zout.writestr(item, data)
    return RedactOutcome(len(found), len({p[0] for p in pairs}) - len(found))


def redact_text_file(path: Path, findings: Sequence[Finding], out_path: Path, style: str = "partial") -> RedactOutcome:
    text, enc = filescan.read_text(path)
    pairs = [(f.text, _label(f, style)) for f in findings]
    new, done = _replace_all(text, pairs)
    out_path.write_bytes(new.encode("utf-8" if enc == "utf-8-sig" else enc))
    return RedactOutcome(done, len({p[0] for p in pairs}) - done)


def redact_file(path: Path, extracted: filescan.Extracted, findings: Sequence[Finding], out_path: Path,
                style: str = "partial") -> RedactOutcome:
    """Writes the safe copy to out_path. Unknown kinds are copied unchanged."""
    path, out_path = Path(path), Path(out_path)
    ext = path.suffix.lower()
    if not findings:
        shutil.copy2(path, out_path)
        return RedactOutcome()
    if ext in filescan.IMAGE_EXT:
        if extracted.frame_words:  # multi-page image (TIFF scan, GIF): redact every page
            frames = filescan.open_frames(path, len(extracted.frame_words))
            total = RedactOutcome()
            done = []
            for frame, words in zip(frames, extracted.frame_words):
                lo = words[0][0] if words else None
                hi = words[-1][1] if words else None
                mine = [f for f in findings if lo is not None and lo <= f.start < hi + 1]
                img, res = redact_image(frame, extracted, mine, style, words=words)
                done.append(img)
                total.replaced += res.replaced
                total.missed += res.missed
            covered = {id(f) for f in findings if any(w and w[0][0] <= f.start <= w[-1][1] for w in extracted.frame_words)}
            total.missed += sum(1 for f in findings if id(f) not in covered)
            done[0].save(out_path, save_all=True, append_images=done[1:])
            return total
        img, res = redact_image(filescan.open_image(path), extracted, findings, style)
        save_kwargs = {"quality": 92} if ext in (".jpg", ".jpeg", ".jfif") else {}
        img.save(out_path, **save_kwargs)
        return res
    if ext in filescan.PDF_EXT:
        return redact_pdf(path, extracted, findings, out_path, style)
    if ext in filescan.OFFICE_PARTS:
        return redact_office(path, findings, out_path, style)
    if ext in filescan.TEXT_EXT:
        return redact_text_file(path, findings, out_path, style)
    shutil.copy2(path, out_path)
    return RedactOutcome(0, len(findings))
