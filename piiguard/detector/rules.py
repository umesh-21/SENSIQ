"""Pattern detectors for structured personal data (IDs, numbers, keys).

Each rule is a regex plus an optional validator (checksums, format checks) and an
optional context requirement (a keyword that must appear just before the match).
These run in well under a millisecond on typical text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Pattern

from . import validators as v
from .types import Finding

I = re.IGNORECASE
_MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
# Any capitalisation: dob, DOB, D.O.B., D0B (OCR), date of birth, birth date, born,
# birthday, and the Hindi जन्म तिथि / जन्मतिथि printed on Aadhaar cards.
_DOB_CONTEXT = re.compile(
    r"(?:\bd\.?\s?[o0]\.?\s?b\b|date\s*of\s*birth|birth\s*date|\bborn\b|birthday|जन्म\s*तिथि|जन्मतिथि)", I)


@dataclass(frozen=True)
class Rule:
    kind: str
    pattern: Pattern
    validator: Optional[Callable[[str], bool]] = None
    context: Optional[Pattern] = None  # must match in the text just before the value
    window: int = 40
    group: int = 0
    score: float = 0.99
    strip: str = ""  # trailing characters to trim from the value
    trim: Optional[Callable[[str], str]] = None  # cuts the value short (keeps its start)
    exclude: Optional[Pattern] = None  # if this matches the text just before the value, skip it


RULES: List[Rule] = [
    # Secrets
    Rule("PRIVATE_KEY", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----(?:[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----)?")),
    Rule("API_KEY", re.compile(r"(?<![A-Za-z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Za-z0-9])")),
    Rule("API_KEY", re.compile(r"(?<![A-Za-z0-9_])(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{22,255})")),
    Rule("API_KEY", re.compile(r"(?<![A-Za-z0-9_-])sk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}")),
    Rule("API_KEY", re.compile(r"(?<![A-Za-z0-9_-])AIza[0-9A-Za-z_-]{35}")),
    Rule("API_KEY", re.compile(r"(?<![A-Za-z0-9-])xox[abprs]-[A-Za-z0-9-]{10,}")),
    Rule("API_KEY", re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    Rule("PASSWORD", re.compile(
        r"\b(?:password|passwd|pwd|passcode|pin|mpin|otp|cvv)\b\s*(?:is\b\s*[:=\-]?|[:=\-])\s*(\S{3,64})", I),
        validator=v.is_password_value, group=1, score=0.9, strip=".,;:!?)\"'"),

    # Financial and government IDs
    Rule("CARD", re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)"), validator=v.is_card),
    Rule("AADHAAR", re.compile(r"(?<!\d)[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}(?![ -]?\d)"), validator=v.is_aadhaar),
    Rule("PAN", re.compile(r"(?<![A-Za-z0-9])[A-Z]{3}[PCHFATBLJG][A-Z]\d{4}[A-Z](?![A-Za-z0-9])", I),
         validator=v.is_pan),
    Rule("SSN", re.compile(r"(?<!\d)(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}(?!\d)")),
    Rule("PASSPORT", re.compile(r"(?<![A-Za-z0-9])[A-Z][0-9]{7}(?![A-Za-z0-9])", I),
         context=re.compile(r"passport", I)),
    Rule("BANK_ACCOUNT", re.compile(r"(?<!\d)\d{9,18}(?!\d)"), validator=v.is_bank_account,
         context=re.compile(r"(?:account|a/c|\bacc\b|\bacct\b)", I)),
    Rule("VOTER_ID", re.compile(r"(?<![A-Za-z0-9])[A-Z]{3}\d{7}(?![A-Za-z0-9])", I),
         context=re.compile(r"(?:voter|epic|election)", I)),
    Rule("DRIVING_LICENCE", re.compile(
        r"(?<![A-Za-z0-9])[A-Z]{2}[- ]?\d{2}[- ]?(?:19|20)\d{2}[- ]?\d{7}(?![A-Za-z0-9])", I),
        validator=v.is_driving_licence),
    Rule("IFSC", re.compile(r"(?<![A-Za-z0-9])[A-Z]{4}0[A-Z0-9]{6}(?![A-Za-z0-9])")),

    # Contact details
    Rule("EMAIL", re.compile(
        r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}(?![\w-])")),
    Rule("UPI", re.compile(
        r"(?<![\w.+@-])[A-Za-z0-9][A-Za-z0-9._-]{1,255}@[A-Za-z][A-Za-z0-9]{1,63}(?![\w@-])(?!\.[A-Za-z0-9])")),
    Rule("PHONE", re.compile(r"(?<![\w+])(?:(?:\+|00)91[ -]?|0)?[6-9]\d(?:[ -]?\d){8}(?!\d)"),
         validator=v.is_indian_mobile),
    Rule("PHONE", re.compile(r"(?<![\w+])\+(?!91)[1-9]\d{0,2}(?:[ .-]?\(?\d{1,4}\)?){2,5}(?!\d)"),
         validator=v.is_intl_phone, score=0.9),

    # Context-dependent details
    Rule("DOB", re.compile(
        r"(?<![\d/.-])(?:\d{1,2}[/.-]\d{1,2}[/.-](?:19|20)?\d{2}"
        r"|(?:19|20)\d{2}[/.-]\d{1,2}[/.-]\d{1,2}"
        r"|\d{1,2}(?:st|nd|rd|th)?\s+" + _MONTHS + r",?\s+(?:19|20)\d{2}"
        r"|" + _MONTHS + r"\s+\d{1,2}(?:st|nd|rd|th)?,?\s+(?:19|20)\d{2})(?!\d)", I),
        context=_DOB_CONTEXT),
    # Older Aadhaar cards print only the year: "Year of Birth : 1990"
    Rule("DOB", re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)"),
         context=re.compile(r"(?:year\s*of\s*birth|\byob\b|birth\s*year|जन्म\s*वर्ष)\s*[:\-/]?\s*$", I),
         window=30),
    # Names after a label: "Name: Rahul Sharma", "S/O Ramesh Kumar", "पिता का नाम: रमेश", "Dear Priya,"
    Rule("PERSON", re.compile(
        r"(?:\b(?:(?:applicant|candidate|student|employee|patient|customer|account\s*holder|card\s*holder|"
        r"full|first|last|father|mother|husband|wife|guardian|spouse|nominee)(?:'s|s)?\s+)?name"
        r"(?:\s+of\s+(?:the\s+)?(?:applicant|candidate|student|father|mother))?\s*[:\-–]\s*"
        r"|\b[SDWC]\s*/\s*O\s*[:\-.]?\s+"
        r"|\b(?:applicant|candidate|patient|beneficiary|nominee|guardian)\s*[:\-–]\s*"
        r"|\bmy\s+name\s+is\s+|\bdear\s+(?:mr\.?\s+|mrs\.?\s+|ms\.?\s+|dr\.?\s+)?"
        r"|(?:पिता|माता|पति|पत्नी)?\s*(?:का|की)?\s*नाम\s*[:\-–]?\s*)"
        r"((?:[A-Za-zऀ-ॿ][A-Za-zऀ-ॿ.'’-]*)(?:[ \t]+(?:[A-Za-zऀ-ॿ][A-Za-zऀ-ॿ.'’-]*)){0,3})",
        I), validator=v.is_label_name, group=1, score=0.85, strip=".,;:'’-", trim=v.trim_label_name,
        exclude=re.compile(
            r"\b(?:file|folder|document|company|organi[sz]ation|business|firm|brand|product|item|project|app|"
            r"application|device|host|domain|server|network|wifi|wi-fi|ssid|user|username|display|screen|"
            r"bank|branch|school|college|university|institute|hospital|course|subject|event|place|city|"
            r"village|street|road|area|shop|store|hotel|team|group|channel|account|plan|scheme|model|"
            r"variable|field|column|table|class|module|package)\s*(?:'s\s+)?name\s*[:\-–]?\s*$", I)),
    Rule("IP_ADDRESS", re.compile(
        r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?!\.?\d)"),
        validator=v.is_ipv4, score=0.8),
    Rule("PINCODE", re.compile(r"(?<!\d)[1-9]\d{2} ?\d{3}(?!\d)"),
         context=re.compile(r"(?:pin\s*code|pincode|postal|zip|\bpin\b)", I), window=25, score=0.8),
    # A 6-digit PIN at the end of an address (Aadhaar: "... Kerala - 688525"), no "PIN" label
    Rule("PINCODE", re.compile(r"(?<![\d/.,:])[1-9]\d{2} ?\d{3}(?![\d/:]|[.,]\d)"),
         context=re.compile(
             r"(?:\baddress\b|\baddr\b|पता|\b[csdw]\s*/\s*o\b|\bdist(?:rict)?\b|\btaluk|\btehsil|\bmandal\b|"
             r"\bvill(?:age)?\b|\bp\.\s?o\.|\bnagar\b|\bcolony\b|andhra|arunachal|assam|bihar|chhattisgarh|"
             r"\bgoa\b|gujarat|haryana|himachal|jharkhand|karnataka|kerala|madhya pradesh|maharashtra|"
             r"manipur|meghalaya|mizoram|nagaland|odisha|orissa|punjab|rajasthan|sikkim|tamil\s*nadu|"
             r"telangana|tripura|uttar\s*pradesh|uttarakhand|west\s*bengal|delhi|chandigarh|puducherry|"
             r"jammu|kashmir|ladakh)", I), window=160, score=0.7),
]


class RuleDetector:
    def __init__(self, rules: Optional[List[Rule]] = None):
        self.rules = rules or RULES

    def detect(self, text: str, enabled: Optional[Dict[str, bool]] = None) -> List[Finding]:
        out: List[Finding] = []
        for rule in self.rules:
            if enabled is not None and not enabled.get(rule.kind, True):
                continue
            for m in rule.pattern.finditer(text):
                s, e = m.span(rule.group)
                if s < 0 or e <= s:
                    continue
                value = text[s:e]
                if rule.trim is not None:
                    value = rule.trim(value)
                    e = s + len(value)
                if rule.strip:
                    trimmed = value.rstrip(rule.strip)
                    e -= len(value) - len(trimmed)
                    value = trimmed
                if not value:
                    continue
                if rule.exclude is not None and rule.exclude.search(text[max(0, s - 40):s]):
                    continue
                if rule.context is not None:
                    before = text[max(0, s - rule.window):s]
                    if not rule.context.search(before):
                        continue
                if rule.validator is not None and not rule.validator(value):
                    continue
                out.append(Finding(s, e, rule.kind, value, rule.score, "rule"))
        return out
