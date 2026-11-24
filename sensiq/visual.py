"""Things in images that aren't plain text: QR codes / barcodes, faces and signatures.

All offline. QR codes and barcodes are decoded with zxing-cpp; faces are found with the
1 MB "Ultra-Light-Fast-Generic-Face-Detector" (MIT) on ONNX Runtime; signatures are found
next to their printed label ("Signature", "Signed", "हस्ताक्षर"...), where the ink is.

Every finder returns boxes in the given image's pixels: (left, top, right, bottom).
"""
from __future__ import annotations

import logging
import re
import threading
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

log = logging.getLogger(__name__)

Box = Tuple[float, float, float, float]

ENABLED = {"codes": True, "faces": True, "signatures": True}


def configure(options) -> None:
    """Turn the finders on/off (settings: visual.codes / visual.faces / visual.signatures)."""
    for k in ENABLED:
        if isinstance(options, dict) and k in options:
            ENABLED[k] = bool(options[k])

# ---------------------------------------------------------------------------------------
# QR codes and barcodes
# ---------------------------------------------------------------------------------------


def find_codes(image) -> List[Tuple[str, str, Box]]:
    """[(format name, decoded text, box)] for every QR code / barcode in the image."""
    try:
        import zxingcpp
    except Exception:
        return []
    out = []
    try:
        img = image.convert("RGB")
        results = zxingcpp.read_barcodes(img)
        if not results and max(img.size) > 2400:  # very large scans: try a smaller copy too
            small = img.copy()
            small.thumbnail((2000, 2000))
            sx, sy = img.width / small.width, img.height / small.height
            results = [(r, sx, sy) for r in zxingcpp.read_barcodes(small)]
        else:
            results = [(r, 1.0, 1.0) for r in results]
        for r, sx, sy in results:
            p = r.position
            xs = [p.top_left.x, p.top_right.x, p.bottom_left.x, p.bottom_right.x]
            ys = [p.top_left.y, p.top_right.y, p.bottom_left.y, p.bottom_right.y]
            box = (min(xs) * sx, min(ys) * sy, max(xs) * sx, max(ys) * sy)
            if box[2] - box[0] < 4 or box[3] - box[1] < 4:  # 1-D barcodes report a line: give it height
                cy = (box[1] + box[3]) / 2
                box = (box[0], cy - 30 * sy, box[2], cy + 30 * sy)
            text = r.text or (r.bytes.hex() if r.bytes else "")
            out.append((str(r.format), text, box))
    except Exception:
        log.debug("code reading failed", exc_info=True)
    try:  # QR codes too small, blurred or dense to decode (e.g. Aadhaar secure QR on a photo)
        for box in locate_qr(image):
            cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
            if not any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for _, _, b in out):
                out.append((UNREADABLE, "", box))
    except Exception:
        log.debug("QR locating failed", exc_info=True)
    return out


UNREADABLE = "QR code (unreadable)"


def locate_qr(image) -> List[Box]:
    """Boxes of QR codes zxing couldn't decode - typically very dense codes like the Aadhaar
    secure QR in a phone photo or a small scan - found by their texture. (Codes with big
    squares decode fine, so they never get here.)"""
    return _locate_by_texture(image)


def _locate_by_texture(image, block: int = 8) -> List[Box]:
    """Square areas of fine, evenly spread black/white checker texture with edges in both
    directions and no line gaps - what a dense QR code looks like and text doesn't."""
    import numpy as np
    from PIL import Image, ImageFilter
    out: List[Box] = []
    g0 = image.convert("L")
    for side in (1000, 500):
        scale = min(1.0, side / max(g0.size))
        if side == 500 and max(g0.size) <= 600:
            break
        g = g0 if scale == 1.0 else g0.resize((max(1, int(g0.width * scale)), max(1, int(g0.height * scale))),
                                              Image.BILINEAR)
        a = np.asarray(g, dtype=np.int16)
        H, W = a.shape[0] // block * block, a.shape[1] // block * block
        if H < block * 6 or W < block * 6:
            continue
        a = a[:H, :W]
        dx = np.zeros(a.shape, bool)
        dx[:, 1:] = np.abs(np.diff(a, axis=1)) > 25
        dy = np.zeros(a.shape, bool)
        dy[1:, :] = np.abs(np.diff(a, axis=0)) > 25
        local = np.asarray(g.filter(ImageFilter.BoxBlur(20)), dtype=np.int16)[:H, :W]
        dark = a < local - 5

        def blk(x):
            return x.reshape(H // block, block, W // block, block).mean(axis=(1, 3))
        ex, ey, dk = blk(dx), blk(dy), blk(dark)
        mask = (ex > 0.2) & (ey > 0.2) & (dk > 0.25) & (dk < 0.75)
        grown = mask.copy()  # bridge single-block holes
        grown[1:, :] |= mask[:-1, :]
        grown[:-1, :] |= mask[1:, :]
        grown[:, 1:] |= mask[:, :-1]
        grown[:, :-1] |= mask[:, 1:]
        seen = np.zeros_like(grown)
        gh, gw = grown.shape
        for y0, x0 in zip(*np.nonzero(grown)):
            if seen[y0, x0]:
                continue
            stack = [(y0, x0)]
            seen[y0, x0] = True
            ys, xs = [], []
            while stack:
                y, x = stack.pop()
                ys.append(y)
                xs.append(x)
                for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                    if 0 <= ny < gh and 0 <= nx < gw and grown[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
            t, btm, l, r = min(ys), max(ys) + 1, min(xs), max(xs) + 1
            # trim the margin the bridging added: keep rows/columns that are mostly texture
            part = mask[t:btm, l:r]
            rk = np.flatnonzero(part.mean(axis=1) >= 0.3)
            ck = np.flatnonzero(part.mean(axis=0) >= 0.3)
            if not rk.size or not ck.size:
                continue
            t, btm, l, r = t + rk[0], t + rk[-1] + 1, l + ck[0], l + ck[-1] + 1
            h, w = btm - t, r - l
            if h < 6 or w < 6 or not 0.7 <= w / h <= 1.4:
                continue
            if mask[t:btm, l:r].mean() < 0.6:
                continue
            # text has empty gaps between lines: edge density varies a lot from row to row
            rows = (dx[t * block:btm * block, l * block:r * block].mean(axis=1)
                    + dy[t * block:btm * block, l * block:r * block].mean(axis=1))
            rows = rows.reshape(-1, max(1, block // 2)).mean(axis=1)[1:-1]  # edges are ragged
            if rows.size and rows.min() < 0.25 * rows.mean():
                continue
            # QR modules are spread evenly: every pixel row/column is about half dark, and
            # edges run both ways. Small text (even blurred together) has uneven rows.
            area = dark[t * block:btm * block, l * block:r * block]
            rp, cp = area.mean(axis=1), area.mean(axis=0)
            if rp.std() > 0.5 * rp.mean() or cp.std() > 0.5 * cp.mean():
                continue
            if ex[t:btm, l:r].mean() < 0.35 or ey[t:btm, l:r].mean() < 0.35:
                continue
            # QR codes are black on white; coloured logos and photo grids aren't
            if image.mode not in ("L", "1", "LA", "I", "I;16"):
                crop = image.convert("RGB").crop((int(l * block / scale), int(t * block / scale),
                                                  int(r * block / scale), int(btm * block / scale)))
                crop.thumbnail((200, 200))
                sat = np.asarray(crop.convert("HSV"))[..., 1]
                if float(np.percentile(sat, 90)) > 80:
                    continue
            box = (float(l * block / scale), float(t * block / scale), float(r * block / scale),
                   float(btm * block / scale))
            cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
            if not any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b in out):
                out.append(box)
    return out


def is_aadhaar_qr(text: str) -> bool:
    """Aadhaar secure QR (a very long decimal number) or the older XML QR."""
    t = text.strip()
    return (len(t) > 300 and t.isdigit()) or "PrintLetterBarcodeData" in t or bool(re.search(r'\buid="\d{12}"', t))


# ---------------------------------------------------------------------------------------
# Faces
# ---------------------------------------------------------------------------------------
_face_lock = threading.Lock()
_face_session = None
_face_error: Optional[str] = None
FACE_SCORE = 0.85


def _face_model(models_root: Path):
    global _face_session, _face_error
    if _face_session is not None or _face_error is not None:
        return _face_session
    try:
        import onnxruntime as ort
        path = Path(models_root) / "face" / "face.onnx"
        so = ort.SessionOptions()
        so.log_severity_level = 3
        so.intra_op_num_threads = 2
        _face_session = ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])
    except Exception as exc:
        _face_error = f"{type(exc).__name__}: {exc}"
        log.warning("Face detection unavailable: %s", _face_error)
    return _face_session


def face_status(models_root: Path) -> dict:
    with _face_lock:
        s = _face_model(models_root)
    return {"face_loaded": s is not None, "face_error": _face_error}


def _nms(boxes, scores, iou: float = 0.3):
    import numpy as np
    order = scores.argsort()[::-1]
    keep = []
    while order.size:
        i = order[0]
        keep.append(i)
        if order.size == 1:
            break
        xx1 = np.maximum(boxes[i, 0], boxes[order[1:], 0])
        yy1 = np.maximum(boxes[i, 1], boxes[order[1:], 1])
        xx2 = np.minimum(boxes[i, 2], boxes[order[1:], 2])
        yy2 = np.minimum(boxes[i, 3], boxes[order[1:], 3])
        inter = np.clip(xx2 - xx1, 0, None) * np.clip(yy2 - yy1, 0, None)
        area = lambda b: (b[..., 2] - b[..., 0]) * (b[..., 3] - b[..., 1])  # noqa: E731
        ovr = inter / (area(boxes[i]) + area(boxes[order[1:]]) - inter + 1e-9)
        order = order[1:][ovr <= iou]
    return keep


def find_faces(image, models_root: Path, tiles: bool = True) -> List[Box]:
    """Boxes of human faces (front or slightly turned). tiles=True also searches large
    photos piece by piece for small faces (not needed for rendered PDF pages)."""
    import numpy as np
    with _face_lock:
        session = _face_model(models_root)
        if session is None:
            return []
        out: List[Box] = []
        img = image.convert("RGB")
        # large pages: look in overlapping tiles too, so small ID-card photos aren't missed
        views = [(0, 0, img.width, img.height)]
        parts = []
        if tiles and max(img.size) > 1400:
            step = 1000
            for y in range(0, max(1, img.height - 400), step):
                for x in range(0, max(1, img.width - 400), step):
                    parts.append((x, y, min(img.width, x + 1300), min(img.height, y + 1300)))
        for idx, (x0, y0, x1, y1) in enumerate(views + parts):
            if idx == len(views) and out:
                break  # the whole-image pass already found faces: no need to tile

            crop = img.crop((x0, y0, x1, y1)).resize((640, 480))
            arr = (np.asarray(crop, dtype=np.float32) - 127.0) / 128.0
            arr = arr.transpose(2, 0, 1)[None]
            scores, boxes = session.run(None, {"input": arr})
            s = scores[0, :, 1]
            m = s >= FACE_SCORE
            if not m.any():
                continue
            b = boxes[0][m]
            w, h = x1 - x0, y1 - y0
            b = np.stack([b[:, 0] * w + x0, b[:, 1] * h + y0, b[:, 2] * w + x0, b[:, 3] * h + y0], axis=1)
            for k in _nms(b, s[m]):
                bx = tuple(float(v) for v in b[k])
                if (bx[2] - bx[0]) >= 16 and (bx[3] - bx[1]) >= 16:
                    out.append(bx)
        if len(out) > 1:  # merge duplicates found in several tiles
            arr = np.array(out)
            keep = _nms(arr, (arr[:, 2] - arr[:, 0]) * (arr[:, 3] - arr[:, 1]), iou=0.2)
            out = [out[k] for k in keep]
        # widen a little so hair/chin are covered too
        res = []
        for l, t, r, b in out:
            pw, ph = (r - l) * 0.15, (b - t) * 0.15
            res.append((max(0, l - pw), max(0, t - ph), min(img.width, r + pw), min(img.height, b + ph)))
        return res


# ---------------------------------------------------------------------------------------
# Turning found items into findings
# ---------------------------------------------------------------------------------------
ID_KINDS = {"AADHAAR", "PAN", "PASSPORT", "VOTER_ID", "DRIVING_LICENCE", "AADHAAR_QR"}
ID_WORDS = re.compile(r"government of india|income tax|aadhaar|आधार|passport|election commission|"
                      r"driving licen[cs]e|identity card|id card|भारत सरकार", re.I)


def findings_for(extracted, detected) -> list:
    """Card entries for QR codes, faces and signatures found in a file or image.

    * QR / barcodes: if the decoded content holds private details, those details are already
      in `detected` and their boxes are the code's box, so the whole code gets covered.
      An Aadhaar secure QR (a long encoded number) is added as its own entry.
    * Faces: "Photo on an ID card" when the same file shows an ID, else "Face".
    * Signatures: one entry each.
    """
    from .detector.types import Finding
    out = []
    text = extracted.text
    has_id = any(f.kind in ID_KINDS for f in detected) or bool(ID_WORDS.search(text))
    for kind, s, e, detail in extracted.visual:
        if kind == "CODE":
            if detail == UNREADABLE:  # can't tell what it holds: on an ID card it's the ID's QR
                out.append(Finding(s, e, "AADHAAR_QR" if has_id else "QR_CODE",
                                   "QR code on an ID card" if has_id else "QR code (couldn't read it)",
                                   0.9 if has_id else 0.6, "visual"))
                continue
            inside = any(f.start < e and f.end > s for f in detected)
            if not inside and is_aadhaar_qr(text[s:e]):
                out.append(Finding(s, e, "AADHAAR_QR", f"{detail} code with Aadhaar details", 0.99, "visual"))
        elif kind == "FACE":
            out.append(Finding(s, e, "PHOTO_ID" if has_id else "FACE",
                               "photo on ID" if has_id else "face in picture", 0.9, "visual"))
        elif kind == "SIGNATURE":
            out.append(Finding(s, e, "SIGNATURE", "handwritten signature", 0.8, "visual"))
    out.extend(id_fragments(extracted, detected + out))
    return out


# OCR look-alikes, for matching a misread piece of a number ("2SS4" is 2554)
_DIGIT_LOOKALIKE = str.maketrans("OoDQIl|i!ZzSs$BbGTgqA", "000011111225558667994")
_TOKEN = re.compile(r"(?<![\w*])[0-9A-Za-z|!$]{4}(?![\w*])")


def id_fragments(extracted, detected) -> list:
    """An ID card often prints its Aadhaar number twice, once very small (under the second
    photo). OCR may only read part of the small copy, e.g. "2SS4". Any 4-character piece
    matching one of the hidden groups (first or second) of a found Aadhaar number is
    flagged too, and its box widened to where the whole number is printed."""
    from .detector.types import Finding
    groups = {}
    for f in detected:
        if f.kind == "AADHAAR":
            digits = re.sub(r"\D", "", f.text)
            if len(digits) == 12:
                groups[digits[0:4]] = 0
                groups[digits[4:8]] = 1
    if not groups:
        return []
    text = extracted.text
    out = []
    for m in _TOKEN.finditer(text):
        s, e = m.span()
        tok = m.group()
        if sum(ch.isdigit() for ch in tok) < 2 or any(f.start < e and f.end > s for f in detected):
            continue
        idx = groups.get(tok.translate(_DIGIT_LOOKALIKE))
        if idx is None:
            continue
        out.append(Finding(s, e, "AADHAAR", tok, 0.85, "fragment"))
        _widen(extracted, s, e, idx)
    return out


def _widen(extracted, s: int, e: int, group: int) -> None:
    """Stretch the box of the word at [s, e) over the whole 'XXXX XXXX XXXX' it belongs to."""
    lists = [extracted.words] + list(extracted.frame_words or [])
    for p in extracted.pages or []:
        if getattr(p, "ocr_words", None):
            lists.append(p.ocr_words)
    for words in lists:
        for i, (ws, we, (l, t, r, b)) in enumerate(words):
            if ws == s and we == e:
                cw = (r - l) / 4.0
                left = max(0.0, l - 5 * group * cw - cw * 0.5)
                words[i] = (ws, we, (left, t - (b - t) * 0.15, left + 15 * cw, b + (b - t) * 0.15))


# ---------------------------------------------------------------------------------------
# Signatures (found by their label, where the ink is)
# ---------------------------------------------------------------------------------------
SIGN_LABEL = re.compile(r"^(?:signature|signed|sign|signatory|authori[sz]ed|हस्ताक्षर|ह०|ह\.)\W*$", re.I)


def find_signatures(image, text: str, words: Sequence[Tuple[int, int, Box]]) -> List[Box]:
    """Ink areas next to a "Signature" label: the area just above it, or right of it on the same line.
    Only areas with real ink strokes (not printed text OCR could read) are returned."""
    import numpy as np
    gray = np.asarray(image.convert("L"), dtype=np.uint8)
    H, W = gray.shape
    text_boxes = [b for _, _, b in words]
    out: List[Box] = []
    for s, e, (l, t, r, b) in words:
        if not SIGN_LABEL.match(text[s:e]):
            continue
        h = max(8.0, b - t)
        candidates = [
            (l - h * 4, t - h * 3.5, r + h * 6, t - 1),          # above the label (most forms)
            (r + 2, t - h * 1.5, r + h * 14, b + h * 1.5),       # to the right, same line
        ]
        for cl, ct, cr, cb in candidates:
            cl, ct, cr, cb = max(0, int(cl)), max(0, int(ct)), min(W, int(cr)), min(H, int(cb))
            if cr - cl < 20 or cb - ct < 10:
                continue
            region = gray[ct:cb, cl:cr] < 110          # dark ink
            # ignore printed words OCR already read inside this area
            for tl, tt, tr, tb in text_boxes:
                if tr > cl and tl < cr and tb > ct and tt < cb:
                    region[max(0, int(tt) - ct):max(0, int(tb) - ct), max(0, int(tl) - cl):max(0, int(tr) - cl)] = False
            ink = region.mean() if region.size else 0
            if ink < 0.01:
                continue
            ys, xs = np.nonzero(region)
            out.append((cl + xs.min(), ct + ys.min(), cl + xs.max() + 1, ct + ys.max() + 1))
            break
    return out
