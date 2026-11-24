"""Reads text from images, on this computer only.

* Windows: the OCR engine built into Windows 10/11 (Windows.Media.Ocr). It needs no extra
  download and uses the OCR languages installed in Windows (Settings > Time & language > Language).
* Linux: Tesseract (`apt install tesseract-ocr tesseract-ocr-hin`), run as a separate program
  per reading; the image is piped in and never written to disk.

Both engines return the same thing (lines of words with boxes), so slicing of tall images,
retries on hard photos, extra variants for ID cards and the extra-language pass are shared.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
import threading
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)

IS_WIN = sys.platform == "win32"
ENGINE_NAME = "Windows OCR" if IS_WIN else "Tesseract OCR"

_lock = threading.Lock()
_engine = None
_error: Optional[str] = None


class _WinEngine:
    """Windows.Media.Ocr engine for one language."""
    parallel = False

    def __init__(self, engine):
        self.engine = engine
        self.tag = engine.recognizer_language.language_tag

    def prepare(self, image):
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

    def read(self, img):
        return asyncio.run(_recognize(self.engine, img))


# ---- Tesseract (Linux) ------------------------------------------------------------------
TESS_LANGS = {"en": "eng", "hi": "hin", "mr": "mar", "bn": "ben", "ta": "tam", "te": "tel", "gu": "guj",
              "kn": "kan", "ml": "mal", "pa": "pan", "ur": "urd", "or": "ori", "as": "asm", "ne": "nep"}
TESS_MAX = 6000      # longest side handed to Tesseract (slices keep tall pages sharp)
TESS_MIN_CONF = 10   # words Tesseract is less sure of are dropped (stray marks, ink, backgrounds)


def tesseract_path() -> Optional[str]:
    exe = os.environ.get("SENSIQ_TESSERACT") or shutil.which("tesseract")
    return exe if exe and os.path.exists(exe) else None


def _tess_env() -> dict:
    from .platform import child_env   # the installed app's own libraries stay out of Tesseract
    env = child_env()
    env["OMP_THREAD_LIMIT"] = "1"      # one thread per reading: SENSIQ runs a few readings at once
    return env


def _tess_languages() -> List[str]:
    exe = tesseract_path()
    if not exe:
        return []
    try:
        r = subprocess.run([exe, "--list-langs"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=15, env=_tess_env())
    except Exception:
        return []
    lines = r.stdout.decode("utf-8", "replace").splitlines()
    return [l.strip() for l in lines[1:] if l.strip() and " " not in l.strip() and l.strip() != "osd"]


class Word(str):
    """A word Tesseract read, with its confidence (0-100) as .conf."""
    conf: float = -1.0


def _word(text: str, conf: float) -> Word:
    w = Word(text)
    w.conf = conf
    return w


def parse_tsv(tsv: str):
    """Tesseract TSV -> lines of (word, (left, top, right, bottom)) in reading order."""
    lines: dict = {}
    order: list = []
    for row in tsv.splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) < 12 or cols[0] != "5":
            continue
        text = cols[11].strip()
        try:
            conf = float(cols[10])
            l, t, w, h = (int(cols[i]) for i in (6, 7, 8, 9))
        except ValueError:
            continue
        if not text or conf < TESS_MIN_CONF:
            continue
        key = (cols[1], cols[2], cols[3], cols[4])  # page, block, paragraph, line
        if key not in lines:
            lines[key] = []
            order.append(key)
        lines[key].append((_word(text, conf), (float(l), float(t), float(l + w), float(h + t))))
    return [lines[k] for k in order]


class _TessEngine:
    """Tesseract for one language (e.g. "eng", "hin"); psm 3 = whole page, 11 = scattered text."""
    parallel = True

    def __init__(self, lang: str = "eng", psm: int = 3):
        self.lang, self.psm = lang, psm
        self.tag = lang

    def sparse(self) -> "_TessEngine":
        return _TessEngine(self.lang, psm=11)

    def prepare(self, image):
        """Grey copy at a size Tesseract reads well (small text enlarged, like a 300-dpi scan)."""
        from PIL import Image, ImageOps
        img = ImageOps.grayscale(image.convert("RGB"))
        w, h = img.size
        scale = 1.0
        if max(w, h) < 1600:
            scale = min(3.0, 2000 / max(w, h, 1))
        if max(w, h) * scale > TESS_MAX:
            scale = TESS_MAX / max(w, h)
        if abs(scale - 1.0) > 0.05:
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
        return img

    def read(self, img):
        import io
        exe = tesseract_path()
        if not exe:
            raise RuntimeError("Tesseract isn't installed")
        buf = io.BytesIO()
        img.save(buf, "PNG")
        r = subprocess.run([exe, "stdin", "stdout", "-l", self.lang, "--psm", str(self.psm), "tsv"],
                           input=buf.getvalue(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=180, env=_tess_env())
        if r.returncode != 0:
            raise RuntimeError(f"Tesseract failed ({r.stderr.decode('utf-8', 'replace').strip()[-200:]})")
        return parse_tsv(r.stdout.decode("utf-8", "replace"))


def _get_engine():
    global _engine, _error
    if _engine is not None or _error is not None:
        return _engine
    if not IS_WIN:
        if not tesseract_path():
            _error = "Tesseract isn't installed (sudo apt install tesseract-ocr)"
        elif "eng" not in _tess_languages():
            _error = "Tesseract has no English data (sudo apt install tesseract-ocr-eng)"
        else:
            _engine = _TessEngine("eng")
            log.info("Tesseract OCR ready (%s)", tesseract_path())
        if _error:
            log.warning("OCR unavailable: %s", _error)
        return _engine
    try:
        from winrt.windows.media.ocr import OcrEngine
        engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            from winrt.windows.globalization import Language
            engine = OcrEngine.try_create_from_language(Language("en-US"))
        if engine is None:
            raise RuntimeError("no OCR language is installed in Windows")
        _engine = _WinEngine(engine)
        log.info("Windows OCR ready (%s)", _engine.tag)
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
    if not IS_WIN:
        have = set(_tess_languages())
        for code in _wanted_languages:
            tess = TESS_LANGS.get(code, code)
            if tess != main.lang and tess in have and all(e.lang != tess for e in _extra_engines):
                _extra_engines.append(_TessEngine(tess))
                log.info("Tesseract also reads %s", tess)
        return _extra_engines
    try:
        from winrt.windows.media.ocr import OcrEngine
        main_prefix = main.tag.split("-")[0].lower()
        seen = {main_prefix}
        for lang in OcrEngine.available_recognizer_languages:
            prefix = lang.language_tag.split("-")[0].lower()
            if prefix in seen or prefix not in _wanted_languages:
                continue
            engine = OcrEngine.try_create_from_language(lang)
            if engine is not None:
                _extra_engines.append(_WinEngine(engine))
                seen.add(prefix)
                log.info("Windows OCR also reads %s", lang.language_tag)
    except Exception:
        log.debug("extra OCR languages unavailable", exc_info=True)
    return _extra_engines


def installed_languages() -> List[str]:
    """Windows: language tags ("en-US", "hi-IN"); Linux: Tesseract codes ("eng", "hin")."""
    if not IS_WIN:
        return _tess_languages()
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
            "ocr_language": engine.tag if engine is not None else None,
            "ocr_extra_languages": [e.tag for e in extra]}


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
    img = engine.prepare(image)
    lines = engine.read(img)
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
    if _good(lines, RETRY_BELOW) or _blank(image):
        return lines
    best = lines
    clean = _enhanced(image)
    sx, sy = image.width / clean.width, image.height / clean.height

    def scaled(ls):
        return [[(t, (l * sx, tp * sy, r * sx, b * sy)) for t, (l, tp, r, b) in line] for line in ls]
    candidates = [scaled(_read_lines(engine, clean))]
    for turn, op in ((90, Image.Transpose.ROTATE_90), (270, Image.Transpose.ROTATE_270),
                     (180, Image.Transpose.ROTATE_180)):
        if _good(max(candidates + [best], key=_score), RETRY_BELOW * 2):
            break
        turned = clean.transpose(op)
        candidates.append(scaled(_unrotate(_read_lines(engine, turned), turn, clean.width, clean.height)))
    for c in candidates:
        if _score(c) > _score(best):
            best = c
    return best


SURE = 60   # Tesseract: mean word confidence of a reading that needs no second try


def _score(lines) -> float:
    """How much was read. With word confidences (Tesseract), junk read from sideways or
    blurred text counts for little; Windows OCR returns nothing there, so letters count."""
    conf = _mean_conf([w for line in lines for w in line])
    return _amount(lines) if conf is None else _amount(lines) * conf / 100.0


def _good(lines, need: int) -> bool:
    conf = _mean_conf([w for line in lines for w in line])
    return _amount(lines) >= need and (conf is None or conf >= SURE)


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


def _variant_jobs(engine, image) -> list:
    """OCR misses different words on busy backgrounds (ID cards, phone photos) depending on
    scale and framing: read a 2x grey copy and overlapping halves as well (and, with Tesseract,
    a "scattered text" reading that finds words outside the main text blocks)."""
    from PIL import Image, ImageOps
    jobs = []
    w, h = image.size
    g = ImageOps.autocontrast(image.convert("L"), cutoff=2)
    k = min(2.0, 2400 / max(w, h, 1))
    if k > 1.2:
        big = g.resize((int(w * k), int(h * k)), Image.LANCZOS).convert("RGB")
        jobs.append(lambda: [[(t, (l / k, tp / k, r / k, b / k)) for t, (l, tp, r, b) in line]
                             for line in _read_lines(engine, big)])
    if h >= w * 0.8:   # tall: top and bottom halves
        cuts = [(0, 0, w, int(h * 0.55)), (0, int(h * 0.45), w, h)]
    else:              # wide: left and right halves
        cuts = [(0, 0, int(w * 0.55), h), (int(w * 0.45), 0, w, h)]

    def half(l0, t0, r0, b0):
        part = _read_lines(engine, image.crop((l0, t0, r0, b0)))
        return [[(t, (l + l0, tp + t0, r + l0, b + t0)) for t, (l, tp, r, b) in line] for line in part]
    for cut in cuts:
        jobs.append(lambda c=cut: half(*c))
    if hasattr(engine, "sparse"):
        jobs.append(lambda: _read_lines(engine.sparse(), image))
    return jobs


def _run_all(engine, jobs: list) -> list:
    """Run readings one by one (Windows OCR), or side by side (Tesseract runs as separate
    processes, so a 4-core PC reads a card's variants in about the time of one)."""
    if getattr(engine, "parallel", False) and len(jobs) > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(4, len(jobs)), thread_name_prefix="ocr") as ex:
            return list(ex.map(lambda f: f(), jobs))
    return [f() for f in jobs]


def _variants(engine, image):
    return _run_all(engine, _variant_jobs(engine, image))


def _extra_lines(extra, image) -> list:
    """Lines an extra language reads that the main one can't (e.g. Devanagari)."""
    try:
        return [line for line in _read_lines(extra, image) if any(not t.isascii() for t, _ in line)]
    except Exception:
        log.debug("extra-language OCR failed", exc_info=True)
        return []


EXTRA_MARGIN = 8   # an extra-language line must be this much surer than the English words under it


def _mean_conf(words) -> Optional[float]:
    confs = [getattr(t, "conf", None) for t, _ in words]
    confs = [c for c in confs if c is not None and c >= 0]
    return sum(confs) / len(confs) if confs else None


def _add_extra(lines, extra_lines):
    """Add extra-language lines. Engines without word confidence (Windows OCR) add every one,
    as before. With Tesseract, a Hindi model also "reads" English text as Devanagari junk,
    so where both read the same spot the surer reading wins (and the other one is dropped)."""
    for line in extra_lines:
        extra_conf = _mean_conf(line)
        if extra_conf is None:
            lines.append(line)
            continue
        under = [(i, j) for i, old in enumerate(lines) for j, (_, ob) in enumerate(old)
                 if any(_overlap(ob, b) > 0.3 or _overlap(b, ob) > 0.3 for _, b in line)]
        if under:
            main_conf = _mean_conf([lines[i][j] for i, j in under]) or 0.0
            if extra_conf < main_conf + EXTRA_MARGIN:
                continue
            drop = set(under)
            for i in range(len(lines)):
                lines[i] = [w for j, w in enumerate(lines[i]) if (i, j) not in drop]
            lines[:] = [l for l in lines if l]
        lines.append(line)
    return lines


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
        jobs = []
        if thorough and max(image.size) <= THOROUGH_MAX and not _blank(image):
            jobs = _variant_jobs(engine, image)
        n_variants = len(jobs)
        jobs += [lambda e=extra: _extra_lines(e, image) for extra in _get_extra_engines()]
        results = _run_all(engine, jobs)
        for extra_lines in results[:n_variants]:
            lines = _merge_lines(lines, extra_lines)
        for extra in results[n_variants:]:
            lines = _add_extra(lines, extra)
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
