"""Safe replacement text for each kind of finding."""
from __future__ import annotations

import re

from .detector.types import Finding

REDACT = {"PRIVATE_KEY", "API_KEY", "PASSWORD"}
LABEL_ONLY = {"PERSON", "LOCATION", "ORG", "IP_ADDRESS", "PINCODE",
              "AADHAAR_QR", "AADHAAR_COPY", "QR_CODE", "PHOTO_ID", "FACE", "SIGNATURE", "CRYPTO_WALLET"}
KEEP_LAST4 = {"AADHAAR", "CARD", "BANK_ACCOUNT", "PHONE", "SSN", "PASSPORT",
              "VOTER_ID", "DRIVING_LICENCE", "PAN", "DOB", "GSTIN", "VEHICLE_REG"}


def placeholder(f: Finding) -> str:
    return f"[{f.placeholder}]"


def mask_keep_last(value: str, keep: int = 4, ch: str = "X") -> str:
    prefix = ""
    m = re.match(r"^(\+\d{1,3}[ -]?)", value)  # keep a phone country code readable
    if m:
        prefix, value = m.group(1), value[m.end():]
    positions = [i for i, c in enumerate(value) if c.isalnum()]
    hide = set(positions[:-keep] if keep and len(positions) > keep else positions[:max(0, len(positions) - 1)])
    return prefix + "".join(ch if i in hide else c for i, c in enumerate(value))


def mask_email(value: str) -> str:
    local, _, domain = value.partition("@")
    return (local[:1] if local else "") + "***@" + domain


def suggest(f: Finding, style: str = "partial") -> str:
    if f.kind == "PRIVATE_KEY":
        return "[private key removed]"
    if f.kind in REDACT:
        return "[REDACTED]"
    if style == "placeholder" or f.kind in LABEL_ONLY:
        return placeholder(f)
    if f.kind in ("EMAIL", "UPI"):
        return mask_email(f.text)
    if f.kind == "IFSC":
        return f.text[:4] + "0XXXXXX"
    if f.source == "fragment":  # part of a number shown elsewhere in full: hide all of it
        return "X" * len(f.text)
    if f.kind in KEEP_LAST4:
        return mask_keep_last(f.text, 4)
    return placeholder(f)
