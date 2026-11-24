# SENSIQ — notes for Claude Code

SENSIQ ("Local AI Privacy Intelligence — Discover before you disclose", called *PII Guard*
up to 1.3.0) is an on-device privacy guard. The released app is **Windows 10/11**; the
current work item is a **Linux (Ubuntu 22.04–24.04) version** — read
[docs/LINUX_PORT.md](docs/LINUX_PORT.md) before starting on it.

Repository: https://github.com/umesh-21/SENSIQ (public). Latest release: 1.6.0.

## What the app does
- Typing protection (Windows UI Automation): badge + panel over any text box, one-click replace.
- Images/PDFs/Office/ZIP/HEIC checked when shared (clipboard, drag, Open/Upload dialogs,
  browser extension): a report card lists each detail with a tick box, "▸ where" shows the
  line it came from, and the safe copy (details covered/replaced) is shared instead.
- Detection is fully offline: regex rules + checksums, BERT NER (ONNX), Presidio + spaCy,
  OCR, QR (zxing-cpp, plus a texture locator for undecodable dense QRs), faces (ONNX),
  signatures.
- Updates from GitHub Releases are **offered only** (notification + card); never installed
  without the user clicking Update now.

## Layout
```
sensiq/              the app (package; was `piiguard` before 1.4.0)
  app.py             main, Tk event loop, tray actions, update + safe-copies folder handling
  detector/          rules.py, validators.py, ner.py, engine.py, presidio_layer.py, types.py
  filescan.py        text + word boxes from images/PDF/Office/text; read_piece() runs OCR,
                     QR, faces, signatures
  ocr.py             Windows OCR (winrt)                         <- Windows-only
  visual.py          QR / faces / signatures / Aadhaar-on-card helpers
  redact.py          safe copies (boxes on images/PDF pages, text replace in documents)
  sharewatch.py      FileScanWorker + clipboard/drag/dialog watchers  <- watchers Windows-only
  capture.py         typing capture via UI Automation                  <- Windows-only
  winutil.py         Win32 helpers (clipboard, startup, windows)       <- Windows-only
  browserlink.py / nativehost.py   browser extension link (named pipe; HKCU registration)
  overlay.py         all Tk cards/badge/panel/toasts (portable Tk)
  tray.py            pystray tray menu
  updater.py         GitHub Releases check, SHA-256-verified download, silent install
  migrate.py         one-time PII Guard -> SENSIQ migration (Windows)
  paths.py, config.py, masking.py, selftest.py, worker.py
browser_extension/   MV3 extension (content.js, page.js, background.js), native host com.sensiq.host
tests/               pytest, ~181 tests (some need Windows OCR / a display and skip otherwise)
tools/               download_model.py, make_icon.py, set_version.py, release.ps1
build.ps1 / installer.iss   Windows build (PyInstaller + Inno Setup)
.github/workflows/release.yml   tag vX.Y.Z -> builds Windows setup -> GitHub Release
CHANGELOG.md         each version's section = release notes = "What's new" in the update card
```

## Rules for working on this repo
- **Never put real personal data in the repo** (tests, docs, examples). Use made-up values
  (e.g. Aadhaar 4918 3205 7615 / 2554 8151 2997 are fake test numbers, names like Ramesh Kumar).
  The repo is public.
- Commit as `Umesh Kashyap <61264950+umesh-21@users.noreply.github.com>` (repo-local git config).
  End commit messages with `Co-Authored-By: Claude <noreply@anthropic.com>`.
- Releases: add `## [X.Y.Z]` to CHANGELOG.md (user-facing words), run
  `python tools/set_version.py X.Y.Z`, commit, `git tag -a vX.Y.Z`, push tag -> Actions builds.
  The Windows release also uploads an old-name copy `PIIGuard-Setup-X.Y.Z.exe` on purpose
  (PII Guard 1.2/1.3 installs only look for that name) — keep it.
- Do **not** install updates on the user's machine yourself; the user chooses Update now.
- Ask before publishing anything new (new repos, making things public).
- Keep tests passing: `python -m pytest -q tests`.
- User-facing text: plain, short, no jargon (users are non-technical, India-focused:
  Aadhaar, PAN, Hindi OCR).

## Run from source (Windows)
`run_dev.bat` or `.venv\Scripts\python -m sensiq --debug`; `--selftest`, `--no-ner`, `--no-tray`.
Models are not in git (name model > 100 MB): `build.ps1` downloads them into `models/`
(`tools/download_model.py --repo Xenova/bert-base-NER --out models/ner`, face model
`models/face/face.onnx` from Linzaer/Ultra-Light-Fast-Generic-Face-Detector-1MB
version-RFB-640.onnx, SHA-256 8F4C659275977E7A3BFBFA339A9C769AD793DF50F9C0BAA8C14B11BAA1646430).
