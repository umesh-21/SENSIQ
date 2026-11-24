"""Checksum and format validators that cut false positives for structured IDs."""
from __future__ import annotations

import re

# Verhoeff tables (Aadhaar uses the Verhoeff checksum)
_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6], [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4], [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2], [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]
_INV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]

INDIAN_STATE_CODES = {
    "AN", "AP", "AR", "AS", "BR", "CG", "CH", "DD", "DL", "DN", "GA", "GJ", "HP", "HR",
    "JH", "JK", "KA", "KL", "LA", "LD", "MH", "ML", "MN", "MP", "MZ", "NL", "OD", "OR",
    "PB", "PY", "RJ", "SK", "TN", "TR", "TS", "UK", "UP", "WB",
}

# Words that often follow "password is ..." in normal sentences, so they are not secrets.
PASSWORD_STOPWORDS = {
    "is", "the", "a", "an", "not", "same", "changed", "change", "reset", "expired", "wrong",
    "correct", "incorrect", "required", "sent", "below", "above", "attached", "here", "there",
    "going", "being", "still", "also", "now", "please", "protected", "updated", "forgotten",
    "missing", "invalid", "valid", "different", "weak", "strong", "mandatory", "optional",
    "too", "very", "your", "my", "our", "their", "his", "her", "for", "and", "but",
}


def digits_only(value: str) -> str:
    return re.sub(r"\D", "", value)


def verhoeff_valid(number: str) -> bool:
    if not number.isdigit():
        return False
    c = 0
    for i, ch in enumerate(reversed(number)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0


def verhoeff_check_digit(number: str) -> str:
    c = 0
    for i, ch in enumerate(reversed(number)):
        c = _D[c][_P[(i + 1) % 8][int(ch)]]
    return str(_INV[c])


def luhn_valid(number: str) -> bool:
    if not number.isdigit():
        return False
    total = 0
    for i, ch in enumerate(reversed(number)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _not_repetitive(d: str) -> bool:
    return len(set(d)) > 2


def is_aadhaar(value: str) -> bool:
    d = digits_only(value)
    return len(d) == 12 and d[0] not in "01" and _not_repetitive(d) and verhoeff_valid(d)


def is_card(value: str) -> bool:
    d = digits_only(value)
    return 13 <= len(d) <= 19 and d[0] in "23456" and len(set(d)) > 1 and luhn_valid(d)


def is_pan(value: str) -> bool:
    v = value.upper()
    return len(v) == 10 and v[3] in "PCHFATBLJG"


def is_indian_mobile(value: str) -> bool:
    d = digits_only(value)
    if len(d) == 14 and d.startswith("0091"):
        d = d[4:]
    elif len(d) == 12 and d.startswith("91"):
        d = d[2:]
    elif len(d) == 11 and d.startswith("0"):
        d = d[1:]
    return len(d) == 10 and d[0] in "6789" and _not_repetitive(d)


def is_intl_phone(value: str) -> bool:
    d = digits_only(value)
    return 8 <= len(d) <= 15 and _not_repetitive(d)


def is_bank_account(value: str) -> bool:
    d = digits_only(value)
    return 9 <= len(d) <= 18 and _not_repetitive(d)


def is_driving_licence(value: str) -> bool:
    s = re.sub(r"[\s-]", "", value.upper())
    return len(s) == 15 and s[:2] in INDIAN_STATE_CODES


def is_ipv4(value: str) -> bool:
    if value in {"0.0.0.0", "127.0.0.1", "255.255.255.255"}:
        return False
    parts = value.split(".")
    return len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)


def is_password_value(value: str) -> bool:
    w = value.strip(".,;:!?)\"'").lower()
    return len(w) >= 3 and w not in PASSWORD_STOPWORDS


# ---- names written after a label ("Name: ...", "S/O ...", "नाम: ...") ---------------------------
# Words that end a name (the next field on the same line) or show it isn't a name at all.
_NAME_STOP = {
    "dob", "d.o.b", "date", "birth", "age", "gender", "sex", "male", "female", "m", "f", "address", "addr",
    "mobile", "phone", "mob", "ph", "tel", "email", "e-mail", "s/o", "d/o", "w/o", "c/o", "son", "daughter",
    "wife", "of", "aadhaar", "aadhar", "pan", "uid", "vid", "roll", "no", "no.", "number", "id", "class",
    "dept", "department", "designation", "signature", "sign", "pin", "pincode", "city", "state", "district",
    "and", "with", "who", "is", "was", "will", "has", "have", "from", "at", "in", "for", "on", "to", "the",
    "जन्म", "पता", "लिंग", "पुरुष", "महिला", "मोबाइल",
}
_NOT_A_NAME = {
    "not", "na", "n/a", "nil", "none", "same", "above", "below", "xyz", "abc", "test", "sample", "here",
    "your", "my", "our", "this", "that", "please", "enter", "write", "fill", "blank", "unknown", "self",
    "sir", "madam", "maam", "team", "all", "everyone", "friend", "friends", "customer", "user", "candidate",
    "applicant", "student", "a", "an", "it", "required", "optional", "mandatory",
}


def trim_label_name(value: str) -> str:
    """Keep the words of a labelled name up to the next field label ("Rahul Sharma DOB" -> "Rahul Sharma")."""
    parts = value.split()
    keep = []
    for p in parts:
        if p.lower().strip(".,:;") in _NAME_STOP or any(ch.isdigit() for ch in p):
            break
        keep.append(p)
    if not keep:
        return ""
    end = value.find(keep[-1], sum(len(k) for k in keep[:-1]))  # position of the last kept word
    return value[: end + len(keep[-1])] if end >= 0 else " ".join(keep)


def is_label_name(value: str) -> bool:
    words = value.split()
    if not 1 <= len(words) <= 4 or len(value) > 60:
        return False
    if words[0].lower().strip(".,") in _NOT_A_NAME:
        return False
    if any(re.search(r"\w\.\w{2,}$", w) or "@" in w or "/" in w or "_" in w for w in words):
        return False  # report.pdf, emails, paths, identifiers
    letters = sum(ch.isalpha() for ch in value)
    return letters >= 3 and any(len(w.strip(".")) >= 2 for w in words)
