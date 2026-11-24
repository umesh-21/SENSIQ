"""Offline self-test: runs the full detector on fake sample data and writes a report."""
from __future__ import annotations

import platform
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

from . import __version__
from .config import Config, IgnoreList
from .masking import suggest
from .paths import models_dir

# Every value below is fake. The Aadhaar number passes the Verhoeff check; the card
# number is the standard Visa test number; the AWS key is AWS's documentation example.
SAMPLE = """Hi team, this is Rahul Sharma from the Bhilai office.
My email is rahul.sharma@example.com and my phone is +91 98765 43210.
Aadhaar: 4918 3205 7615, PAN: ABCPE1234F, DOB: 14/08/1996.
Refund to card 4111 1111 1111 1111 or account no 123456789012, IFSC SBIN0001234.
UPI: rahul.s@okaxis. The wifi password is Tiger@2024.
Deploy key: AKIAIOSFODNN7EXAMPLE
"""

CLEAN = "The meeting moved to Thursday at 4 pm. Order 20231105 shipped in 3 boxes, version 1.2.3."

EXPECTED_RULE_KINDS = {
    "EMAIL", "PHONE", "AADHAAR", "PAN", "DOB", "CARD", "BANK_ACCOUNT", "IFSC", "UPI",
    "PASSWORD", "API_KEY",
}


def _linux_desktop_report() -> str:
    """Which Linux helpers are there (informational; files are checked without them)."""
    import shutil
    from . import platform as plat
    have = []
    for tool in ("xclip", "wl-paste", "notify-send", "pkexec"):
        have.append(f"{tool} {'yes' if shutil.which(tool) else 'missing'}")
    try:
        from .capture_linux import accessibility_enabled
        have.append("typing checks " + ("on" if accessibility_enabled() else "off"))
    except Exception:
        have.append("typing checks unavailable")
    return f"{plat.session_type() or 'no display'} session; " + ", ".join(have)


def run_selftest(report_path: Optional[Path] = None, use_ner: bool = True) -> Tuple[bool, str]:
    from .detector.engine import DetectionEngine

    cfg = Config.defaults()
    cfg.data["ner"]["enabled"] = use_ner
    lines = [f"SENSIQ {__version__} self-test",
             f"Python {sys.version.split()[0]} on {platform.platform()}",
             f"Models folder: {models_dir()}", ""]

    t0 = time.perf_counter()
    engine = DetectionEngine(cfg, IgnoreList(), models_dir())
    load_ms = (time.perf_counter() - t0) * 1000
    status = engine.status()
    if status["ner_loaded"]:
        lines.append(f"Name model: loaded ({status['model']}) in {load_ms:.0f} ms")
    elif use_ner:
        lines.append(f"Name model: NOT loaded - {status['ner_error']}")
    else:
        lines.append("Name model: skipped (--no-ner)")

    t1 = time.perf_counter()
    findings = engine.detect(SAMPLE)
    first_ms = (time.perf_counter() - t1) * 1000
    t2 = time.perf_counter()
    engine.detect(SAMPLE)
    cached_ms = (time.perf_counter() - t2) * 1000
    clean = engine.detect(CLEAN)

    lines.append(f"Detection time: {first_ms:.1f} ms first run, {cached_ms:.1f} ms repeat")
    lines.append("")
    lines.append("Found in the sample text:")
    for f in findings:
        lines.append(f"  {f.label:<28} {f.text!r:<28} -> {suggest(f)!r}  ({f.source}, {f.score:.2f})")

    found = {f.kind for f in findings}
    missing = sorted(EXPECTED_RULE_KINDS - found)
    ner_ok = (not use_ner) or (status["ner_loaded"] and "PERSON" in found)
    ok = not missing and ner_ok and not clean

    lines.append("")
    lines.append("Pattern detectors: " + ("all expected types found" if not missing else f"MISSING {missing}"))
    if use_ner:
        lines.append("Name detection: " + ("found 'Rahul Sharma'" if ner_ok else "FAILED"))
    lines.append("Clean text check: " + ("no false alarms" if not clean else f"FALSE ALARMS {[f.text for f in clean]}"))

    # Presidio layer and image checks (OCR) -------------------------------------------
    lines.append("Presidio detectors: " + ("loaded" if status.get("presidio_loaded")
                                            else f"NOT loaded - {status.get('presidio_error')}"))
    ok = ok and bool(status.get("presidio_loaded"))
    try:
        from PIL import Image, ImageDraw, ImageFont
        from . import ocr
        img = Image.new("RGB", (600, 120), "white")
        font = None
        for name in ("arial.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
                     "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "DejaVuSans.ttf"):
            try:
                font = ImageFont.truetype(name, 28)
                break
            except OSError:
                continue
        font = font or ImageFont.load_default()
        ImageDraw.Draw(img).text((20, 40), "Aadhaar 4918 3205 7615", fill="black", font=font)
        t3 = time.perf_counter()
        text = ocr.image_to_text(img)
        ocr_ms = (time.perf_counter() - t3) * 1000
        img_kinds = {f.kind for f in engine.detect(text)}
        ocr_ok = "AADHAAR" in img_kinds
        lines.append(f"Image check ({ocr.ENGINE_NAME}, {ocr.status()['ocr_language']}): "
                     + (f"found the Aadhaar number in {ocr_ms:.0f} ms" if ocr_ok else f"FAILED, read {text!r}"))
    except Exception as exc:
        ocr_ok = False
        lines.append(f"Image check (OCR): NOT available - {exc}")
    ok = ok and ocr_ok

    # QR codes, faces, extra OCR languages -------------------------------------------------
    try:
        from . import visual
        from .ocr import status as ocr_status
        import zxingcpp  # noqa: F401
        lines.append("QR / barcode reader: ready")
    except Exception as exc:
        lines.append(f"QR / barcode reader: NOT available - {exc}")
        ok = False
    try:
        fs = visual.face_status(models_dir())
        lines.append("Face detector: " + ("ready" if fs["face_loaded"] else f"NOT available - {fs['face_error']}"))
        ok = ok and fs["face_loaded"]
        extra = ocr_status().get("ocr_extra_languages") or []
        lines.append("Extra OCR languages: " + (", ".join(extra) if extra else
                                                 "none (tray menu: 'Read Hindi in images' to add Hindi)"))
        if sys.platform.startswith("linux"):
            lines.append("Desktop: " + _linux_desktop_report())
    except Exception as exc:
        lines.append(f"Face detector: NOT available - {exc}")
        ok = False
    lines.append("")
    lines.append("RESULT: " + ("PASS" if ok else "FAIL"))
    report = "\n".join(lines)
    if report_path is not None:
        Path(report_path).write_text(report, encoding="utf-8")
    return ok, report
