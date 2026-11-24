"""Set the app version everywhere it appears: python tools/set_version.py 1.2.0"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def set_version(v: str) -> None:
    if not re.fullmatch(r"\d+\.\d+\.\d+", v):
        raise SystemExit(f"version must look like 1.2.0, got {v!r}")
    a, b, c = v.split(".")
    edits = {
        "piiguard/__init__.py": [(r'__version__ = "[^"]+"', f'__version__ = "{v}"')],
        "installer.iss": [(r'#define AppVersion "[^"]+"', f'#define AppVersion "{v}"')],
        "browser_extension/manifest.json": [(r'"version": "[^"]+"', f'"version": "{v}"')],
        "build.ps1": [(r"PIIGuard-Setup-\d+\.\d+\.\d+\.exe", f"PIIGuard-Setup-{v}.exe")],
        "version_info.txt": [(r"filevers=\(\d+, \d+, \d+, 0\)", f"filevers=({a}, {b}, {c}, 0)"),
                             (r"prodvers=\(\d+, \d+, \d+, 0\)", f"prodvers=({a}, {b}, {c}, 0)"),
                             (r"'FileVersion', '[^']+'", f"'FileVersion', '{v}'"),
                             (r"'ProductVersion', '[^']+'", f"'ProductVersion', '{v}'")],
    }
    for rel, subs in edits.items():
        p = ROOT / rel
        s = p.read_text(encoding="utf-8")
        for pat, rep in subs:
            s, n = re.subn(pat, rep, s)
            if not n:
                raise SystemExit(f"{rel}: pattern {pat!r} not found")
        p.write_text(s, encoding="utf-8")
        print(f"{rel}: {v}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    set_version(sys.argv[1])
