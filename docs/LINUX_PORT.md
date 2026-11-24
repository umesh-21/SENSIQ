# SENSIQ for Linux (Ubuntu 22.04 – 24.04) — plan and hand-off

Status: **not started** (planned on 2026-11-24). The Windows app is at 1.6.0. This file is the
brief for whoever (or whichever Claude session) builds the Linux version on a Linux machine.

## Start here (on the Ubuntu machine)

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-dev python3-tk \
    tesseract-ocr tesseract-ocr-eng tesseract-ocr-hin \
    gir1.2-ayatanaappindicator3-0.1 libnotify-bin wl-clipboard xclip
git clone https://github.com/umesh-21/SENSIQ.git && cd SENSIQ
code .            # VS Code; start Claude Code in this folder (it reads CLAUDE.md)
```

First prompt to give Claude Code there:

> Read CLAUDE.md and docs/LINUX_PORT.md. Build the Linux version of SENSIQ for Ubuntu
> 22.04–24.04 following the plan in docs/LINUX_PORT.md, phase by phase. Keep the Windows
> version working. Run the tests on this machine after each phase and tell me what works.

## Open decisions (ask the user before phase 1 if still open)
1. **Scope of the first Linux release** — recommended: *files first* (everything except typing
   warnings); typing protection via AT-SPI as a later, experimental phase.
2. **Package** — recommended: a `.deb` (pulls in Tesseract etc.; updates install with the
   user's password via `pkexec`). Alternative: AppImage (no root, but Tesseract separate).

## What carries over, what changes

| Part | Linux plan |
|---|---|
| Detection: rules, NER (ONNX), Presidio/spaCy, QR (zxing-cpp), faces (ONNX), PDFs (pypdfium2), Office/ZIP/HEIC, redaction, masking | Same code. Check `pip install` of every requirement on Ubuntu (Python 3.10 on 22.04, 3.12 on 24.04). |
| OCR (`sensiq/ocr.py`, Windows `winrt`) | New backend: **Tesseract** via `pytesseract.image_to_data` (word boxes → same `recognize()` contract: text + `(start, end, box)` per word). Languages `eng+hin` when installed. Keep slicing/retry/variant logic; tune for Tesseract (it prefers 300-dpi-like input, binarised). |
| Browser extension + native host | Same extension. Native host manifest goes to `~/.config/google-chrome/NativeMessagingHosts/com.sensiq.host.json` (also `chromium`, `BraveSoftware/Brave-Browser`, `microsoft-edge`). Replace the Windows named pipe with a **Unix domain socket** in `$XDG_RUNTIME_DIR/sensiq/link.sock` (0600) + same auth key. `nativehost.py` stdio framing is portable except the Win32 bits. |
| Clipboard watcher (copied images/files) | Poll `wl-paste --list-types` / `wl-paste --type image/png` on Wayland, `xclip -selection clipboard -t TARGETS -o` on X11; `text/uri-list` for copied files. Put the safe copy back with `wl-copy` / `xclip`. |
| Open/Upload dialog watcher, drag watcher (`sharewatch.DialogWatcher`, `DragWatcher`) | **Not possible on Linux** (no global hooks; GTK portals). Replace with: a Files (Nautilus) right-click script **"Check with SENSIQ"** (`~/.local/share/nautilus/scripts/` or a `.desktop` action), tray **"Check a file…"**, and the browser extension for web uploads. |
| Tray (`pystray`) | Works with the AppIndicator backend (`gir1.2-ayatanaappindicator3`, enabled by default on Ubuntu). Default/left-click actions behave differently — keep everything in the menu. Notifications via `notify-send`. |
| Cards/overlays (`overlay.py`, Tk) | Portable. Remove Win32 calls (`winutil.make_overlay`, foreground restore, work area) behind a platform layer. On Wayland, Tk runs on XWayland: bottom-right placement usually works; test both "Ubuntu" (Wayland) and "Ubuntu on Xorg" sessions. |
| Typing protection (`capture.py`, UI Automation) | Later phase: **AT-SPI** (`pyatspi` / `gi.repository.Atspi`) — focused `EditableText`, read text, replace ranges. Works for GTK/Qt/Chromium/Electron with accessibility on. On Wayland the badge can't follow the caret: use a corner card instead. |
| Paths | `%APPDATA%\SENSIQ` → `~/.config/sensiq` (settings, ignore list, link key); `%LOCALAPPDATA%\SENSIQ` → `~/.local/share/sensiq` (safe copies default, work, updates); logs → `~/.local/state/sensiq`. Safe-copies folder setting + "chosen folder is never auto-cleaned" rules stay. |
| Start with Windows (registry Run) | `~/.config/autostart/sensiq.desktop`. |
| Registry (installer's safe-copies folder, migration) | Not needed; `migrate.py` is Windows-only. |
| Installer (Inno Setup) | `.deb` built with `dpkg-deb` (or `fpm`) from a PyInstaller one-folder build into `/opt/sensiq`, `/usr/bin/sensiq` symlink, `.desktop` file, icon, Depends: tesseract-ocr, tesseract-ocr-hin, gir1.2-ayatanaappindicator3-0.1, libnotify-bin, wl-clipboard \| xclip. Build on **ubuntu-22.04** so glibc works on 22.04 and 24.04. |
| Updater (`updater.py`) | Same GitHub check. Linux asset: `sensiq_X.Y.Z_amd64.deb` + `.sha256`. Install with `pkexec apt-get install -y ./file.deb` after the user clicks Update now (never automatically). Keep Windows asset matching untouched. |
| Release workflow | Add a `linux` job (`runs-on: ubuntu-22.04`) to `.github/workflows/release.yml`: venv, models, PyInstaller, `.deb`, tests under `xvfb-run`, upload `.deb` + `.sha256` to the same release. Optionally a test-only job on `ubuntu-24.04`. |

## Suggested phases
1. **Platform layer**: `sensiq/platform/` with `windows.py` and `linux.py` (paths, clipboard,
   startup, open file/URL, notify, single instance via lock file/socket). Windows behaviour
   unchanged; all tests still pass on Windows.
2. **OCR on Tesseract** (`ocr.py` backend switch) + run the image/PDF tests on Linux.
3. **Core app on Linux**: tray, cards, "Check a file…", Nautilus script, clipboard watcher,
   safe copies, updater (deb). `python -m sensiq --selftest` passes.
4. **Browser link** on Unix socket + native-host manifests; test upload/paste/drop in Chrome.
5. **Packaging + CI**: `.deb`, release workflow job, install/uninstall/upgrade test on 22.04 and 24.04.
6. (Later) **Typing protection** via AT-SPI.

## Things learned on Windows that matter for Linux
- OCR quality decides everything on ID cards. Windows OCR missed words on busy backgrounds; the
  fix was reading extra variants (2× grey copy, overlapping halves) and merging new words
  (`ocr._variants`, `_merge_lines`). Keep that idea with Tesseract.
- The NER model reads a chunk as a whole; OCR junk from card backgrounds can hide a clear name.
  Short texts also run 3-line windows (`ner._line_windows`) and junk words are filtered
  (`ner._wordlike`). Keep this.
- Dense Aadhaar secure QR codes can't be decoded at phone-photo resolution; `visual.locate_qr`
  finds them by texture. The tiny Aadhaar copy under the small photo is covered by
  `visual.small_photo_numbers`.
- "Not private" must never silently hide ID numbers; ignored values stay on file cards unticked.
- Test data must be fake; the repo is public.
