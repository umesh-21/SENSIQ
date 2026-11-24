"""Reads text from images with the OCR engine built into Windows 10/11
(Windows.Media.Ocr). It runs on this computer only, needs no extra download and
uses the OCR languages installed in Windows (Settings > Time & language > Language).
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)

_lock = threading.Lock()
_engine = None
_error: Optional[str] = None


def _get_engine():
    global _engine, _error
    if _engine is not None or _error is not None:
        return _engine
    try:
        from winrt.windows.media.ocr import OcrEngine
        engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            from winrt.windows.globalization import Language
            engine = OcrEngine.try_create_from_language(Language("en-US"))
        if engine is None:
            raise RuntimeError("no OCR language is installed in Windows")
        _engine = engine
        log.info("Windows OCR ready (%s)", engine.recognizer_language.language_tag)
    except Exception as exc:
        _error = f"{type(exc).__name__}: {exc}"
        log.warning("Windows OCR unavailable: %s", _error)
    return _engine


_wanted_languages: List[str] = ["hi"]   # extra languages to read when Windows has them
_extra_engines: Optional[list] = None


def configure(languages) -> None:
    """Languages (e.g. ["en", "hi", "ta"]) to read besides the main one, when installed in Windows."""
    global _wanted_languages, _extra_engines
    with _lock:
        _wanted_languages = [str(l).lower() for l in (languages or [])]
        _extra_engines = None


def reset() -> None:
    """Forget engines, e.g. after a new OCR language was installed."""
    global _engine, _error, _extra_engines
    with _lock:
        _engine, _error, _extra_engines = None, None, None


def _get_extra_engines() -> list:
    """Engines for installed languages other than the main one (e.g. Hindi next to English)."""
    global _extra_engines
    if _extra_engines is not None:
        return _extra_engines
    _extra_engines = []
    main = _get_engine()
    if main is None:
        return _extra_engines
    try:
        from winrt.windows.media.ocr import OcrEngine
        main_prefix = main.recognizer_language.language_tag.split("-")[0].lower()
        seen = {main_prefix}
        for lang in OcrEngine.available_recognizer_languages:
            prefix = lang.language_tag.split("-")[0].lower()
            if prefix in seen or prefix not in _wanted_languages:
                continue
            engine = OcrEngine.try_create_from_language(lang)
            if engine is not None:
                _extra_engines.append(engine)
                seen.add(prefix)
                log.info("Windows OCR also reads %s", lang.language_tag)
    except Exception:
        log.debug("extra OCR languages unavailable", exc_info=True)
    return _extra_engines


def installed_languages() -> List[str]:
    try:
        from winrt.windows.media.ocr import OcrEngine
        return [l.language_tag for l in OcrEngine.available_recognizer_languages]
    except Exception:
        return []


def status() -> dict:
    with _lock:
        engine = _get_engine()
        extra = _get_extra_engines()
    return {"ocr_loaded": engine is not None, "ocr_error": _error,
            "ocr_language": engine.recognizer_language.language_tag if engine is not None else None,
            "ocr_extra_languages": [e.recognizer_language.language_tag for e in extra]}


def _prepare(image):
    """RGBA image sized for the OCR engine: small text is upscaled, huge images shrunk."""
    from PIL import Image
    from winrt.windows.media.ocr import OcrEngine

    img = image.convert("RGBA")
    w, h = img.size
    limit = int(OcrEngine.max_image_dimension)
    scale = 1.0
    if max(w, h) < 1200:
        scale = min(2.5, 1600 / max(w, h, 1))
    if max(w, h) * scale > limit:
        scale = limit / max(w, h)
    if abs(scale - 1.0) > 0.05:
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    return img


Box = Tuple[float, float, float, float]  # left, top, right, bottom in the original image's pixels


async def _recognize(engine, img):
    from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.storage.streams import DataWriter

    writer = DataWriter()
    writer.write_bytes(img.tobytes("raw", "BGRA"))
    bitmap = SoftwareBitmap.create_copy_from_buffer(
        writer.detach_buffer(), BitmapPixelFormat.BGRA8, img.width, img.height, BitmapAlphaMode.PREMULTIPLIED)
    result = await engine.recognize_async(bitmap)
    lines = []
    for line in result.lines:
        words = []
        for w in line.words:
            r = w.bounding_rect
            words.append((w.text, (r.x, r.y, r.x + r.width, r.y + r.height)))
        lines.append(words)
    return lines


TILE = 3000      # tall images are read in slices this high (full sharpness)...
OVERLAP = 160    # ...overlapping by this much so no line is cut in half


def _lines_scaled(engine, image, oy: float = 0.0):
    """OCR one image (or slice); word boxes in the original image's pixels, shifted down by oy."""
    img = _prepare(image)
    lines = asyncio.run(_recognize(engine, img))
    sx, sy = image.width / img.width, image.height / img.height
    return [[(t, (l * sx, tp * sy + oy, r * sx, b * sy + oy)) for t, (l, tp, r, b) in line] for line in lines]


def _read_lines(engine, image):
    """All text lines of an image with one engine; tall images are read in slices."""
    if image.height <= TILE * 1.5 or image.height <= image.width * 2:
        return _lines_scaled(engine, image)
    lines = []
    top = 0
    while top < image.height:
        bottom = min(image.height, top + TILE)
        part = _lines_scaled(engine, image.crop((0, top, image.width, bottom)), oy=top)
        lo = top + (OVERLAP / 2 if top else -1)                       # skip lines the previous slice kept
        hi = bottom - OVERLAP / 2 if bottom < image.height else image.height + 1
        for line in part:
            if line and lo <= line[0][1][1] < hi:
                lines.append(line)
        if bottom >= image.height:
            break
        top = bottom - OVERLAP
    return lines


def _amount(lines) -> int:
    return sum(sum(ch.isalnum() for ch in t) for line in lines for t, _ in line)


RETRY_BELOW = 12   # an image giving fewer letters/digits than this is retried cleaned up / turned


def _blank(image) -> bool:
    """A (nearly) empty page or plain background: nothing worth a second look."""
    from PIL import ImageStat
    g = image.convert("L")
    g.thumbnail((400, 400))
    return ImageStat.Stat(g).stddev[0] < 6


def _enhanced(image):
    """Grey, contrast-stretched, sharpened and (if small) enlarged copy, for hard photos."""
    from PIL import Image, ImageFilter, ImageOps
    g = ImageOps.autocontrast(image.convert("L"), cutoff=2)
    if min(g.size) < 900:
        k = min(3.0, 1200 / max(1, min(g.size)))
        g = g.resize((int(g.width * k), int(g.height * k)), Image.LANCZOS)
    return g.filter(ImageFilter.UnsharpMask(radius=2, percent=160, threshold=3)).convert("RGB")


def _unrotate(lines, turn: int, w: int, h: int):
    """Map boxes read on a turned copy (PIL transpose) back to the original image."""
    def back(x, y):
        if turn == 90:      # copy = original turned 90° counter-clockwise
            return w - y, x
        if turn == 270:     # turned 90° clockwise
            return y, h - x
        return w - x, h - y  # 180°
    out = []
    for line in lines:
        nl = []
        for t, (l, tp, r, b) in line:
            (x1, y1), (x2, y2) = back(l, tp), back(r, b)
            nl.append((t, (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))))
        out.append(nl)
    return out


def _best_lines(engine, image):
    """Read the image; if it gives (almost) nothing, try a cleaned-up copy and turned copies."""
    from PIL import Image
    lines = _read_lines(engine, image)
    if _amount(lines) >= RETRY_BELOW or _blank(image):
        return lines
    best = lines
    clean = _enhanced(image)
    sx, sy = image.width / clean.width, image.height / clean.height

    def scaled(ls):
        return [[(t, (l * sx, tp * sy, r * sx, b * sy)) for t, (l, tp, r, b) in line] for line in ls]
    candidates = [scaled(_read_lines(engine, clean))]
    for turn, op in ((90, Image.Transpose.ROTATE_90), (270, Image.Transpose.ROTATE_270),
                     (180, Image.Transpose.ROTATE_180)):
        if _amount(max(candidates + [best], key=_amount)) >= RETRY_BELOW * 2:
            break
        turned = clean.transpose(op)
        candidates.append(scaled(_unrotate(_read_lines(engine, turned), turn, clean.width, clean.height)))
    for c in candidates:
        if _amount(c) > _amount(best):
            best = c
    return best


THOROUGH_MAX = 2000  # single images up to this size are also read as a few extra variants


def _overlap(a: Box, b: Box) -> float:
    """Share of box a's area covered by box b."""
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    return w * h / max(1e-6, (a[2] - a[0]) * (a[3] - a[1]))


def _merge_lines(lines, extra):
    """Add the words of `extra` lines that the first reading missed (no overlapping word),
    each as a line placed after the existing line just above it."""
    have = [box for line in lines for _, box in line]
    for line in extra:
        new = [(t, b) for t, b in line if t.strip() and not any(_overlap(b, h) > 0.3 for h in have)]
        if not new or not any(any(ch.isalnum() for ch in t) for t, _ in new):
            continue
        top = min(b[1] for _, b in new)
        at = 0
        for i, old in enumerate(lines):
            if old and min(b[1] for _, b in old) <= top:
                at = i + 1
        lines.insert(at, new)
        have.extend(b for _, b in new)
    return lines


def _variants(engine, image):
    """Windows OCR misses different words on busy backgrounds (ID cards, phone photos)
    depending on scale and framing: read a 2x grey copy and overlapping halves as well."""
    from PIL import Image, ImageOps
    out = []
    w, h = image.size
    g = ImageOps.autocontrast(image.convert("L"), cutoff=2)
    k = min(2.0, 2400 / max(w, h, 1))
    if k > 1.2:
        big = g.resize((int(w * k), int(h * k)), Image.LANCZOS).convert("RGB")
        out.append([[(t, (l / k, tp / k, r / k, b / k)) for t, (l, tp, r, b) in line]
                    for line in _read_lines(engine, big)])
    if h >= w * 0.8:   # tall: top and bottom halves
        cuts = [(0, 0, w, int(h * 0.55)), (0, int(h * 0.45), w, h)]
    else:              # wide: left and right halves
        cuts = [(0, 0, int(w * 0.55), h), (int(w * 0.45), 0, w, h)]
    for l0, t0, r0, b0 in cuts:
        part = _read_lines(engine, image.crop((l0, t0, r0, b0)))
        out.append([[(t, (l + l0, tp + t0, r + l0, b + t0)) for t, (l, tp, r, b) in line] for line in part])
    return out


def recognize(image, thorough: bool = False) -> Tuple[str, List[Tuple[int, int, Box]]]:
    """OCR a PIL image. Returns the text (words joined by spaces, lines by newlines) and,
    for every word, its character span in that text and its box in the image.
    * tall images are read in overlapping slices;
    * images that give (almost) no text are retried cleaned up and turned 90/180/270 degrees;
    * extra installed languages (e.g. Hindi) are read too; their non-English lines are added;
    * thorough (single images, not PDF pages): small images are also read as a 2x grey copy
      and in overlapping halves, and words the first reading missed are merged in.
    Raises RuntimeError if OCR is unavailable."""
    with _lock:
        engine = _get_engine()
        if engine is None:
            raise RuntimeError(f"OCR unavailable: {_error}")
        lines = _best_lines(engine, image)
        if thorough and max(image.size) <= THOROUGH_MAX and not _blank(image):
            for extra_lines in _variants(engine, image):
                lines = _merge_lines(lines, extra_lines)
        for extra in _get_extra_engines():
            try:
                for line in _read_lines(extra, image):
                    if any(not t.isascii() for t, _ in line):  # e.g. Devanagari lines English can't read
                        lines.append(line)
            except Exception:
                log.debug("extra-language OCR failed", exc_info=True)
    parts: List[str] = []
    words: List[Tuple[int, int, Box]] = []
    pos = 0
    for i, line in enumerate(lines):
        if i:
            parts.append("\n")
            pos += 1
        for j, (text, box) in enumerate(line):
            if j:
                parts.append(" ")
                pos += 1
            parts.append(text)
            words.append((pos, pos + len(text), box))
            pos += len(text)
    return "".join(parts), words


def image_to_text(image) -> str:
    """Text found in a PIL image ('' if none). Raises RuntimeError if OCR is unavailable."""
    return recognize(image)[0]
