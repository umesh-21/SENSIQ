"""Local named-entity recognition with a small transformer model (ONNX Runtime, CPU).

The default model is dslim/bert-base-NER (quantized ONNX export by Xenova, ~110 MB),
which tags PER, LOC, ORG and MISC. Any Hugging Face token-classification model
exported to ONNX works, as long as the folder contains:
    model.onnx (or model_quantized.onnx), tokenizer.json, config.json (with id2label)

Nothing here touches the network: files are read from disk only.
"""
from __future__ import annotations

import json
import logging
import os
import re
from collections import OrderedDict
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from .types import Finding

log = logging.getLogger(__name__)

LABEL_TO_KIND = {"PER": "PERSON", "PERSON": "PERSON", "LOC": "LOCATION", "GPE": "LOCATION", "ORG": "ORG"}

RawEntity = Tuple[int, int, str, float]  # start, end, raw group (PER/LOC/...), mean score


class NerModel:
    MAX_CHUNK = 600      # characters per inference call (well under 512 tokens)
    CACHE_SIZE = 2048    # chunks remembered, so only the edited paragraph is re-run

    def __init__(self, model_dir: Path, threshold: float = 0.8,
                 labels: Optional[Dict[str, bool]] = None, threads: Optional[int] = None):
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self.np = np
        model_dir = Path(model_dir)
        candidates = [model_dir / "model.onnx", model_dir / "model_quantized.onnx",
                      model_dir / "onnx" / "model_quantized.onnx", model_dir / "onnx" / "model.onnx"]
        onnx_path = next((p for p in candidates if p.exists()), None)
        if onnx_path is None:
            raise FileNotFoundError(f"No ONNX model found in {model_dir}")
        tok_path, cfg_path = model_dir / "tokenizer.json", model_dir / "config.json"
        for p in (tok_path, cfg_path):
            if not p.exists():
                raise FileNotFoundError(f"Missing {p.name} in {model_dir}")

        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        self.id2label = {int(k): v for k, v in cfg["id2label"].items()}

        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        so.intra_op_num_threads = threads or max(1, min(4, (os.cpu_count() or 2) // 2))
        so.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(onnx_path), sess_options=so,
                                            providers=["CPUExecutionProvider"])
        self.input_names = {i.name for i in self.session.get_inputs()}

        self.tokenizer = Tokenizer.from_file(str(tok_path))
        self.tokenizer.no_padding()
        self.tokenizer.enable_truncation(max_length=512)

        self.threshold = threshold
        self.labels = labels or {"PER": True}
        self.model_name = f"{model_dir.name}/{onnx_path.name}"
        self._cache: "OrderedDict[str, List[RawEntity]]" = OrderedDict()
        log.info("NER model loaded: %s (%d labels)", onnx_path, len(self.id2label))

    # ---- public -----------------------------------------------------------------
    WINDOW_TEXT_MAX = 3000  # short texts (ID cards, screenshots, forms) also get the line-window pass

    def _line_windows(self, text: str) -> Iterator[Tuple[int, str]]:
        """Each non-blank line with the line above and below it. The model reads a chunk as a
        whole, so OCR noise from a card's background art several lines away can flip a clear
        name ("Sarathkumar N P") to "not a name"; a small window doesn't see that noise."""
        spans = []
        pos = 0
        for line in text.splitlines(keepends=True):
            if line.strip():
                spans.append((pos, pos + len(line)))
            pos += len(line)
        seen = set()
        for i in range(len(spans)):
            s, e = spans[max(0, i - 1)][0], spans[min(len(spans) - 1, i + 1)][1]
            if (s, e) not in seen:
                seen.add((s, e))
                yield s, text[s:e]

    def detect(self, text: str) -> List[Finding]:
        chunks = list(self._chunks(text))
        if len(text) <= self.WINDOW_TEXT_MAX and len(chunks) and text.count("\n") >= 3:
            chunks += list(self._line_windows(text))
        best: dict = {}
        for f in self._detect_chunks(chunks):
            key = (f.start, f.end, f.kind)
            if key not in best or f.score > best[key].score:
                best[key] = f
        return sorted(best.values(), key=lambda f: f.start)

    def _detect_chunks(self, chunks) -> List[Finding]:
        out: List[Finding] = []
        for offset, chunk in chunks:
            # The model is cased: it looks for capital letters. Text typed all in lowercase
            # ("rahul sharma from pune") or printed in CAPITALS (ID cards) is re-capitalised
            # word by word first, and only confident results are kept from that pass.
            source, recased_spans = self._recase(chunk)
            for s, e, group, score in self._entities(source):
                kind = LABEL_TO_KIND.get(group)
                if kind is None or not self.labels.get(group, False) or score < self.threshold:
                    continue
                if any(a <= s < b for a, b in recased_spans):
                    floor = self.RECASED_MIN.get(group, 0.99)
                    words = set(re.findall(r"[a-z]+", source[s:e].lower()))
                    if score < floor or source[s:e].lower().startswith("the ") or words <= self.LABEL_WORDS:
                        continue
                s, e = self._trim_edges(chunk, s, e)
                value = chunk[s:e]
                if len(value.strip()) < 2 or not self._wordlike(value):
                    continue
                out.append(Finding(offset + s, offset + e, kind, value, float(score), "ner"))
        return out

    # Everyday words that the model sometimes pulls into a name ("im rahul" -> "Im Rahul",
    # "im fine" -> a "name"). They are trimmed from both ends of a match; a match made only
    # of these words is dropped.
    EDGE_WORDS = {
        "i", "im", "i'm", "iam", "m", "am", "me", "my", "mine", "myself", "its", "it's", "it", "s", "is",
        "this", "that", "here", "there", "the", "a", "an", "and", "or", "to", "from", "with", "for", "of",
        "in", "at", "on", "by", "hi", "hii", "hello", "hey", "dear", "thanks", "thank", "thx", "ok", "okay",
        "yes", "no", "yeah", "fine", "good", "nice", "sir", "madam", "maam", "ma'am", "bro", "bhai", "ji",
        "mr", "mrs", "ms", "miss", "dr", "prof", "shri", "smt", "name", "called", "call", "u",
        "you", "your", "ur", "we", "our", "he", "she", "his", "her", "they", "them", "regards", "best",
    }

    @classmethod
    def _trim_edges(cls, text: str, s: int, e: int) -> Tuple[int, int]:
        words = [(m.start() + s, m.end() + s, m.group().lower())
                 for m in re.finditer(r"[A-Za-z][A-Za-z'.]*", text[s:e])]
        words = [(a, b, w.rstrip(".")) for a, b, w in words]
        while words and words[0][2] in cls.EDGE_WORDS:
            words.pop(0)
        while words and words[-1][2] in cls.EDGE_WORDS:
            words.pop()
        if not words:
            return s, s
        return words[0][0], words[-1][1]

    _WORD_SHAPES = re.compile(
        r"^(?:[A-Z]?[a-z]+|[A-Z]+|(?:Mc|Mac|O'|D'|De|Di|Da|La|Le|Van|Von)?[A-Z][a-z]+(?:[A-Z][a-z]+)?)$")

    @classmethod
    def _wordlike(cls, value: str) -> bool:
        """OCR noise from patterned backgrounds ("TTtTR", "Hrmz") is not a name or place: every
        word of 3+ letters needs a vowel and a normal shape (Rahul, RAHUL, rahul, McDonald)."""
        for w in re.findall(r"[A-Za-z']+", value):
            if len(w) < 3:
                continue  # initials, "Mr", "N P"
            if not re.search(r"[aeiouyAEIOUY]", w) or not cls._WORD_SHAPES.match(w):
                return False
        return True

    RECASED_MIN = {"PER": 0.90, "LOC": 0.98, "ORG": 0.98}
    # Field labels printed on ID cards and forms; never a name on their own.
    LABEL_WORDS = {
        "dob", "d", "o", "b", "yob", "date", "of", "birth", "name", "male", "female", "gender", "sex",
        "address", "father", "mother", "husband", "wife", "son", "daughter", "signature", "sign",
        "mobile", "phone", "email", "pin", "pincode", "aadhaar", "aadhar", "pan", "vid", "year",
        "government", "india", "account", "number", "no", "card", "holder", "issue", "valid", "upto",
    }

    @staticmethod
    def _recase(chunk: str) -> Tuple[str, List[Tuple[int, int]]]:
        """Copy of the chunk where every all-lowercase or mostly-CAPITALS line is title-cased
        (lengths unchanged), plus the spans that were changed."""
        out: List[str] = []
        spans: List[Tuple[int, int]] = []
        pos = 0
        for line in chunk.splitlines(keepends=True):
            letters = [c for c in line if c.isalpha() and c.isascii()]
            upper = sum(c.isupper() for c in letters)
            new = line
            if len(letters) >= 3 and not (0 < upper < 0.8 * len(letters)):
                titled = line.title()
                if len(titled) == len(line) and titled != line:
                    new = titled
                    spans.append((pos, pos + len(line)))
            out.append(new)
            pos += len(line)
        return "".join(out), spans

    # ---- chunking -----------------------------------------------------------------
    def _chunks(self, text: str) -> Iterator[Tuple[int, str]]:
        """Group lines into paragraph-sized chunks, keeping absolute offsets."""
        buf: List[str] = []
        buf_start = buf_len = pos = 0
        for line in text.splitlines(keepends=True):
            blank = not line.strip()
            if buf and (blank or buf_len + len(line) > self.MAX_CHUNK):
                yield from self._split_long(buf_start, "".join(buf))
                buf, buf_len = [], 0
            if not blank:
                if not buf:
                    buf_start = pos
                buf.append(line)
                buf_len += len(line)
            pos += len(line)
        if buf:
            yield from self._split_long(buf_start, "".join(buf))

    def _split_long(self, start: int, chunk: str) -> Iterator[Tuple[int, str]]:
        i = 0
        while i < len(chunk):
            j = min(len(chunk), i + self.MAX_CHUNK)
            if j < len(chunk):
                k = chunk.rfind(" ", i + self.MAX_CHUNK // 2, j)
                if k > i:
                    j = k + 1
            yield start + i, chunk[i:j]
            i = j

    # ---- inference ------------------------------------------------------------------
    def _entities(self, chunk: str) -> List[RawEntity]:
        hit = self._cache.get(chunk)
        if hit is not None:
            self._cache.move_to_end(chunk)
            return hit
        ents = self._infer(chunk)
        self._cache[chunk] = ents
        if len(self._cache) > self.CACHE_SIZE:
            self._cache.popitem(last=False)
        return ents

    def _logits(self, enc):
        np = self.np
        feeds = {}
        for name in self.input_names:
            if name == "input_ids":
                feeds[name] = np.asarray([enc.ids], dtype=np.int64)
            elif name == "attention_mask":
                feeds[name] = np.asarray([enc.attention_mask], dtype=np.int64)
            elif name == "token_type_ids":
                feeds[name] = np.asarray([enc.type_ids], dtype=np.int64)
        return self.session.run(None, feeds)[0][0]

    def _infer(self, chunk: str) -> List[RawEntity]:
        np = self.np
        enc = self.tokenizer.encode(chunk)
        logits = np.asarray(self._logits(enc), dtype=np.float32)
        logits = logits - logits.max(axis=-1, keepdims=True)
        probs = np.exp(logits)
        probs /= probs.sum(axis=-1, keepdims=True)
        pred = probs.argmax(axis=-1)
        conf = probs.max(axis=-1)

        # One label per word: take the first sub-token's prediction ("first" strategy).
        words: List[list] = []
        last_wid = None
        for t, wid in enumerate(enc.word_ids):
            if wid is None:
                continue
            s, e = enc.offsets[t]
            if e <= s:
                continue
            if wid != last_wid:
                words.append([s, e, self.id2label.get(int(pred[t]), "O"), float(conf[t])])
                last_wid = wid
            else:
                words[-1][1] = e

        # Merge B-/I- tagged words into entities.
        ents: List[list] = []
        cur = None
        for s, e, lab, sc in words:
            if lab == "O":
                if cur:
                    ents.append(cur)
                    cur = None
                continue
            prefix, group = lab.split("-", 1) if "-" in lab else ("I", lab)
            if cur and cur[2] == group and prefix == "I" and self._joinable(chunk, cur[1], s):
                cur[1] = e
                cur[3].append(sc)
            else:
                if cur:
                    ents.append(cur)
                cur = [s, e, group, [sc]]
        if cur:
            ents.append(cur)
        return [(s, e, g, sum(scores) / len(scores)) for s, e, g, scores in ents]

    @staticmethod
    def _joinable(text: str, prev_end: int, next_start: int) -> bool:
        gap = text[prev_end:next_start]
        return len(gap) <= 3 and gap.strip(" .'-") == ""
