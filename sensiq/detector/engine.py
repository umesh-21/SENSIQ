"""Combines the rule detectors and the local NER model into one result list."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Iterable, List, Optional

from .rules import RuleDetector
from .types import Finding

log = logging.getLogger(__name__)

_PLACEHOLDER = re.compile("[-]")  # placeholder text for faces/signatures in files


# Initials printed with a name ("Ramesh Kumar N P", "N. P. Ramesh", "K.R. Nair")
_TRAIL_INITIALS = re.compile(r"(?:[ \t]+[A-Z](?:\.|(?![A-Za-z])))+")
_LEAD_INITIALS = re.compile(r"(?:(?:\b[A-Z]\.[ \t]?){1,3}|(?:\b[A-Z][ \t]+){2,3})$")


def extend_initials(text: str, findings: Iterable[Finding]) -> List[Finding]:
    """Stretch name findings over initials next to them, so "N P" isn't left showing."""
    out = []
    for f in findings:
        if f.kind == "PERSON":
            s, e = f.start, f.end
            m = _TRAIL_INITIALS.match(text, e)
            if m:
                dotted = bool(re.search(r"[A-Z]\.", m.group()))
                letters = re.findall(r"[A-Z]", m.group())
                # "Rahul Sharma I am" / "... A good day" are words, not initials
                before_word = bool(re.match(r"[ \t]+[a-z]", text[m.end():m.end() + 3]))
                lone_word = len(letters) == 1 and letters[0] in "IA" and not dotted
                if (dotted or not before_word) and not lone_word:
                    e = m.end()
            m = _LEAD_INITIALS.search(text, max(0, s - 12), s)
            if m and m.end() == s:
                s = m.start()
            if (s, e) != (f.start, f.end):
                f = Finding(s, e, f.kind, text[s:e], f.score, f.source)
        out.append(f)
    return out


def resolve_overlaps(findings: Iterable[Finding]) -> List[Finding]:
    """Keep the most specific detection where spans overlap (e.g. EMAIL beats PERSON)."""
    ranked = sorted(findings, key=lambda f: (-f.priority, -(f.end - f.start), f.start))
    kept: List[Finding] = []
    for f in ranked:
        if all(f.end <= k.start or f.start >= k.end for k in kept):
            kept.append(f)
    return sorted(kept, key=lambda f: f.start)


class DetectionEngine:
    def __init__(self, config, ignore, models_root: Path):
        self.config = config
        self.ignore = ignore
        self.rules = RuleDetector()
        self.ner = None
        self.ner_error = None
        ner_cfg = config.data.get("ner", {})
        if ner_cfg.get("enabled", True):
            try:
                from .ner import NerModel
                self.ner = NerModel(Path(models_root) / "ner",
                                    threshold=float(ner_cfg.get("threshold", 0.8)),
                                    labels=dict(ner_cfg.get("labels", {"PER": True})))
            except Exception as exc:  # missing files, broken install, etc.
                self.ner_error = f"{type(exc).__name__}: {exc}"
                log.warning("Name detection unavailable: %s", self.ner_error)
        self.presidio = None
        self.presidio_error = None
        if config.data.get("presidio", {}).get("enabled", True):
            try:
                from .presidio_layer import PresidioDetector
                self.presidio = PresidioDetector(config.data.get("presidio", {}).get("spacy_model", "en_core_web_sm"))
            except Exception as exc:
                self.presidio_error = f"{type(exc).__name__}: {exc}"
                log.warning("Presidio detectors unavailable: %s", self.presidio_error)

    def detect(self, text: str, limit: Optional[int] = None) -> List[Finding]:
        """Findings in text. limit caps the characters checked (default: max_chars, for live
        typing); 0 means no cap. Use detect_all() for whole documents."""
        if limit is None:
            limit = int(self.config.data.get("max_chars", 20000))
        if limit:
            text = text[:limit]
        if not text.strip():
            return []
        findings = self.rules.detect(text, self.config.data.get("detectors"))
        if self.ner is not None:
            ner_cfg = self.config.data.get("ner", {})
            self.ner.threshold = float(ner_cfg.get("threshold", 0.8))
            self.ner.labels = dict(ner_cfg.get("labels", {"PER": True}))
            try:
                findings.extend(self.ner.detect(text))
            except Exception:
                log.exception("NER inference failed")
        if self.presidio is not None:
            enabled = self.config.data.get("detectors") or {}
            try:
                findings.extend(f for f in self.presidio.detect(text) if enabled.get(f.kind, True))
            except Exception:
                log.exception("Presidio analysis failed")
        findings = [f for f in findings if not self.ignore.contains(f.fingerprint())
                    and not _PLACEHOLDER.search(f.text) and not self._never(f)]
        return resolve_overlaps(extend_initials(text, findings))

    def _never(self, f: Finding) -> bool:
        """Names/places you told SENSIQ never to flag (settings: never_flag_words)."""
        if f.kind not in ("PERSON", "LOCATION", "ORG"):
            return False
        words = self.config.data.get("never_flag_words") or []
        if not words:
            return False
        norm = " ".join(f.text.lower().split())
        return norm in {" ".join(str(w).lower().split()) for w in words}

    CHUNK = 15000  # characters per detection pass for whole documents

    def detect_all(self, text: str) -> List[Finding]:
        """Whole documents (files, PDFs, OCR'd images): every character is checked, in
        line-aligned chunks, unlike detect() which is capped for live typing."""
        import dataclasses
        out: List[Finding] = []
        pos = 0
        while pos < len(text):
            end = len(text)
            if end - pos > self.CHUNK:
                cut = text.rfind("\n", pos + self.CHUNK // 2, pos + self.CHUNK)
                end = cut + 1 if cut > pos else pos + self.CHUNK
            for f in self.detect(text[pos:end], limit=0):
                out.append(dataclasses.replace(f, start=f.start + pos, end=f.end + pos) if pos else f)
            pos = end
        return out

    def status(self) -> dict:
        return {
            "ner_loaded": self.ner is not None,
            "ner_error": self.ner_error,
            "model": getattr(self.ner, "model_name", None),
            "presidio_loaded": self.presidio is not None,
            "presidio_error": self.presidio_error,
        }
