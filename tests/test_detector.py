import random

import pytest

from piiguard.config import Config, IgnoreList
from piiguard.detector.engine import DetectionEngine, resolve_overlaps
from piiguard.detector.rules import RuleDetector
from piiguard.detector.types import Finding
from piiguard.detector import validators as v
from piiguard.masking import suggest
from piiguard.selftest import CLEAN, SAMPLE


def kinds(text):
    return {f.kind: f.text for f in resolve_overlaps(RuleDetector().detect(text))}


def engine_no_ner():
    cfg = Config.defaults()
    cfg.data["ner"]["enabled"] = False
    cfg.data["presidio"]["enabled"] = False
    return DetectionEngine(cfg, IgnoreList(), "/nonexistent")


# ---- validators ----------------------------------------------------------------------
def test_verhoeff_roundtrip():
    rng = random.Random(7)
    for _ in range(200):
        base = str(rng.randint(2, 9)) + "".join(str(rng.randint(0, 9)) for _ in range(10))
        full = base + v.verhoeff_check_digit(base)
        assert v.verhoeff_valid(full)
        bad = full[:-1] + str((int(full[-1]) + 1) % 10)
        assert not v.verhoeff_valid(bad)


def test_luhn():
    assert v.luhn_valid("4111111111111111")
    assert not v.luhn_valid("4111111111111112")


# ---- individual detectors ----------------------------------------------------------------
@pytest.mark.parametrize("text,kind,value", [
    ("mail me at rahul.sharma@example.com today", "EMAIL", "rahul.sharma@example.com"),
    ("call +91 98765 43210 now", "PHONE", "+91 98765 43210"),
    ("call 9876543210", "PHONE", "9876543210"),
    ("US office +1 415 555 0132", "PHONE", "+1 415 555 0132"),
    ("Aadhaar 4918 3205 7615 attached", "AADHAAR", "4918 3205 7615"),
    ("PAN ABCPE1234F", "PAN", "ABCPE1234F"),
    ("card 4111-1111-1111-1111 exp 12/29", "CARD", "4111-1111-1111-1111"),
    ("IFSC SBIN0001234", "IFSC", "SBIN0001234"),
    ("pay rahul.s@okaxis.", "UPI", "rahul.s@okaxis"),
    ("account no: 123456789012", "BANK_ACCOUNT", "123456789012"),
    ("passport number J8369854", "PASSPORT", "J8369854"),
    ("voter id ABC1234567", "VOTER_ID", "ABC1234567"),
    ("DL MH12 2015 0012345", "DRIVING_LICENCE", "MH12 2015 0012345"),
    ("SSN 123-45-6789", "SSN", "123-45-6789"),
    ("DOB: 14/08/1996", "DOB", "14/08/1996"),
    ("born on 3rd March 1994", "DOB", "3rd March 1994"),
    ("pincode 490020", "PINCODE", "490020"),
    ("the wifi password is Tiger@2024.", "PASSWORD", "Tiger@2024"),
    ("OTP: 482913", "PASSWORD", "482913"),
    ("key AKIAIOSFODNN7EXAMPLE", "API_KEY", "AKIAIOSFODNN7EXAMPLE"),
    ("token ghp_" + "a" * 36, "API_KEY", "ghp_" + "a" * 36),
])
def test_detects(text, kind, value):
    found = kinds(text)
    assert found.get(kind) == value, found


def test_private_key_block():
    text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----"
    assert "PRIVATE_KEY" in kinds(text)


@pytest.mark.parametrize("text", [
    CLEAN,
    "I have a password reset link for you",
    "my password is changed now",
    "Aadhaar 4918 3205 7616",          # wrong check digit
    "card 4111 1111 1111 1112",        # fails Luhn
    "ticket 123456789012 is closed",   # 12 digits, no account context
    "call me at 12345",
    "J8369854 is the tracking code",   # passport format without context
    "version 1.2.3.4 released",        # IP detector is off by default in the engine
])
def test_no_false_alarms(text):
    assert engine_no_ner().detect(text) == []


def test_sample_text_all_rule_kinds():
    found = {f.kind for f in engine_no_ner().detect(SAMPLE)}
    for k in ["EMAIL", "PHONE", "AADHAAR", "PAN", "DOB", "CARD", "BANK_ACCOUNT", "IFSC",
              "UPI", "PASSWORD", "API_KEY"]:
        assert k in found, k


def test_overlap_prefers_specific():
    a = Finding(0, 10, "PERSON", "rahul.shar")
    b = Finding(0, 24, "EMAIL", "rahul.sharma@example.com")
    assert resolve_overlaps([a, b]) == [b]


def test_ignore_list():
    eng = engine_no_ner()
    text = "mail rahul.sharma@example.com"
    f = eng.detect(text)[0]
    eng.ignore.add(f.fingerprint())
    assert eng.detect(text) == []


def test_detector_toggle():
    cfg = Config.defaults()
    cfg.data["ner"]["enabled"] = False
    cfg.data["detectors"]["EMAIL"] = False
    eng = DetectionEngine(cfg, IgnoreList(), "/nonexistent")
    assert eng.detect("mail rahul.sharma@example.com") == []


# ---- masking -------------------------------------------------------------------------
@pytest.mark.parametrize("kind,text,partial,label", [
    ("AADHAAR", "4918 3205 7615", "XXXX XXXX 7615", "[Aadhaar]"),
    ("CARD", "4111 1111 1111 1111", "XXXX XXXX XXXX 1111", "[card number]"),
    ("PHONE", "+91 98765 43210", "+91 XXXXX X3210", "[phone]"),
    ("EMAIL", "rahul.sharma@example.com", "r***@example.com", "[email]"),
    ("DOB", "14/08/1996", "XX/XX/1996", "[date of birth]"),
    ("PASSWORD", "Tiger@2024", "[REDACTED]", "[REDACTED]"),
    ("PERSON", "Rahul Sharma", "[name]", "[name]"),
])
def test_masking(kind, text, partial, label):
    f = Finding(0, len(text), kind, text)
    assert suggest(f, "partial") == partial
    assert suggest(f, "placeholder") == label


# ---- NER aggregation (fake model, real tokenizer library) -------------------------------
def test_ner_aggregation(tmp_path):
    np = pytest.importorskip("numpy")
    tokenizers = pytest.importorskip("tokenizers")
    from tokenizers import Tokenizer, models, normalizers, pre_tokenizers, processors
    from piiguard.detector.ner import NerModel

    vocab = {t: i for i, t in enumerate(
        ["[PAD]", "[UNK]", "[CLS]", "[SEP]", "Hi", ",", "I", "am", "Rahul", "Sha", "##rma", "from", "Bhilai", "."])}
    tok = Tokenizer(models.WordPiece(vocab, unk_token="[UNK]"))
    tok.normalizer = normalizers.BertNormalizer(lowercase=False)
    tok.pre_tokenizer = pre_tokenizers.BertPreTokenizer()
    tok.post_processor = processors.TemplateProcessing(
        single="[CLS] $A [SEP]", special_tokens=[("[CLS]", 2), ("[SEP]", 3)])

    labels = ["O", "B-PER", "I-PER", "B-LOC", "I-LOC"]
    tag_for = {"Rahul": "B-PER", "Sha": "I-PER", "##rma": "I-PER", "Bhilai": "B-LOC"}

    class FakeNer(NerModel):
        def __init__(self):
            self.np = np
            self.tokenizer = tok
            self.id2label = dict(enumerate(labels))
            self.threshold = 0.5
            self.labels = {"PER": True, "LOC": True}
            self._cache = __import__("collections").OrderedDict()

        def _logits(self, enc):
            out = np.full((len(enc.ids), len(labels)), -5.0, dtype=np.float32)
            for i, t in enumerate(enc.tokens):
                out[i, labels.index(tag_for.get(t, "O"))] = 5.0
            return out

    text = "Hi, I am Rahul Sharma from Bhilai."
    found = {(f.kind, f.text) for f in FakeNer().detect(text)}
    assert ("PERSON", "Rahul Sharma") in found
    assert ("LOCATION", "Bhilai") in found
