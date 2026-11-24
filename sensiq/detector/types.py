"""Shared types for detections."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

HIGH, MEDIUM, LOW = 3, 2, 1

# kind: (display label, severity, placeholder text, overlap priority)
KIND_INFO = {
    "PRIVATE_KEY": ("Private key", HIGH, "private key removed", 100),
    "API_KEY": ("API key or token", HIGH, "API key", 95),
    "PASSWORD": ("Password, PIN or OTP", HIGH, "password", 90),
    "CARD": ("Card number", HIGH, "card number", 88),
    "AADHAAR": ("Aadhaar number", HIGH, "Aadhaar", 86),
    "AADHAAR_QR": ("Aadhaar QR code", HIGH, "Aadhaar QR", 85),
    "AADHAAR_COPY": ("Small copy of the Aadhaar number", HIGH, "Aadhaar", 85),
    "QR_CODE": ("QR code that couldn't be read", MEDIUM, "QR code", 56),
    "PHOTO_ID": ("Photo on an ID card", HIGH, "photo", 83),
    "FACE": ("Face in a picture", MEDIUM, "face", 60),
    "SIGNATURE": ("Signature", MEDIUM, "signature", 57),
    "PAN": ("PAN", HIGH, "PAN", 84),
    "SSN": ("US Social Security number", HIGH, "SSN", 84),
    "PASSPORT": ("Passport number", HIGH, "passport no.", 80),
    "BANK_ACCOUNT": ("Bank account number", HIGH, "account no.", 78),
    "VOTER_ID": ("Voter ID (EPIC)", MEDIUM, "voter ID", 74),
    "DRIVING_LICENCE": ("Driving licence number", MEDIUM, "DL no.", 74),
    "GSTIN": ("GSTIN", MEDIUM, "GSTIN", 72),
    "CRYPTO_WALLET": ("Crypto wallet address", MEDIUM, "wallet", 70),
    "VEHICLE_REG": ("Vehicle registration number", LOW, "vehicle no.", 45),
    "EMAIL": ("Email address", MEDIUM, "email", 62),
    "UPI": ("UPI ID", MEDIUM, "UPI ID", 61),
    "IFSC": ("IFSC code", MEDIUM, "IFSC", 58),
    "PHONE": ("Phone number", MEDIUM, "phone", 55),
    "DOB": ("Date of birth", MEDIUM, "date of birth", 50),
    "IP_ADDRESS": ("IP address", LOW, "IP address", 40),
    "PINCODE": ("PIN code", LOW, "PIN code", 35),
    "PERSON": ("Person's name", LOW, "name", 25),
    "LOCATION": ("Place or address", LOW, "location", 20),
    "ORG": ("Organisation", LOW, "organisation", 15),
}

_UNKNOWN = ("Private detail", LOW, "private", 0)


def fingerprint(kind: str, text: str) -> str:
    """Stable hash used for the ignore list, so raw values are never stored."""
    norm = re.sub(r"[\s\-]+", "", text).lower()
    return hashlib.sha256(f"{kind}|{norm}".encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Finding:
    start: int
    end: int
    kind: str
    text: str
    score: float = 1.0
    source: str = "rule"  # "rule", "ner" or "presidio"
    ignored: bool = False  # files: a value you marked "not private" (shown unticked, never hidden)

    @property
    def label(self) -> str:
        return KIND_INFO.get(self.kind, _UNKNOWN)[0]

    @property
    def severity(self) -> int:
        return KIND_INFO.get(self.kind, _UNKNOWN)[1]

    @property
    def placeholder(self) -> str:
        return KIND_INFO.get(self.kind, _UNKNOWN)[2]

    @property
    def priority(self) -> int:
        return KIND_INFO.get(self.kind, _UNKNOWN)[3]

    def fingerprint(self) -> str:
        return fingerprint(self.kind, self.text)
