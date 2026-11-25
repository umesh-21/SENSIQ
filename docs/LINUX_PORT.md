# SENSIQ for Linux (Ubuntu 22.04 – 24.04) — plan and status

Status (2026-11-25): **released in 1.7.0, fixed after real-desktop tests in 1.7.1**; phases 1–6 built and tested on Ubuntu 22.04 (Python 3.10, Tesseract 4.1.1,
headless with Xvfb); the release workflow builds and publishes the `.deb`. Windows and Linux share one version number.

## Decisions taken
1. **Scope:** files *and* typing protection (AT-SPI typing checks shipped as a "test version").
2. **Package:** `.deb` (`/opt/sensiq`, `/usr/bin/sensiq`), updates via `pkexec apt-get install`
   after the user clicks Update now.

## What was done, phase by phase
| Phase | Result | Tested here |
|---|---|---|
| 1 Platform layer | `sensiq/platform/{windows,linux}.py` (`winutil.py` is now a shim); XDG paths in `paths.py`; autostart `.desktop`; `fcntl` single-instance lock; `control.py` (`--check FILE` hands files to the running app over a 0600 Unix socket / named pipe). Fixed a real Linux bug: path keys were lower-cased (`str(p).lower()`), so ZIP safe copies kept the original data on a case-sensitive disk. | `tests/test_linux.py` |
| 2 OCR | `ocr.py` engine classes: `_WinEngine` (unchanged behaviour) and `_TessEngine` (Tesseract CLI, image piped via stdin, TSV word boxes). Shared slicing/retry/variants; Tesseract adds a sparse-text (`--psm 11`) variant and runs variants in parallel. Word confidence is used to (a) retry rotations when the reading is junk, (b) drop words < 10 conf, (c) add Hindi lines only where they beat the English reading. | all image/PDF tests pass with Tesseract |
| 3 Core app | tray (explicit pystray backend: AppIndicator > GTK > Xorg), cards (Ubuntu font, no Win32 calls), clipboard watcher (xclip TIMESTAMP/TARGETS polling; wl-paste fallback), "Check a file…", Files script, "opened files" via `recently-used.xbel`, safe copies shown with FileManager1 D-Bus, notify-send, `.deb` updater. | app end-to-end under Xvfb (test + screenshots) |
| 4 Browser link | Unix socket `$XDG_RUNTIME_DIR/sensiq/link.sock` (0600) + same key; manifests in `~/.config/{google-chrome,chromium,BraveSoftware/Brave-Browser,microsoft-edge,vivaldi}/NativeMessagingHosts`; POSIX stdio framing. | host relay test (source and packaged binary). **Not tested in a real Chrome** (none on the build machine). |
| 5 Packaging + CI | `build_linux.sh`, `packaging/linux/sensiq.spec` (GTK limited to Adwaita), `.deb` ≈ 211 MB (576 MB installed). `release.yml`: `linux` (22.04 build + tests + install/remove), `linux-24` (install/reinstall/remove on 24.04), `publish-linux` (uploads after the Windows job created the release, appends a Linux line to the notes). | `.deb` built and the packaged app's self-test PASSes; **apt install/upgrade not run here (no root)** — CI does it |
| 6 Typing (AT-SPI) | `capture_linux.py`: private GLib context, `object:state-changed:focused` events, reads `Text`, replaces via `EditableText` (fallback: select + Ctrl+V, X11 only), password boxes and deny-listed apps never read, Wayland → badge in the corner. Tray item turns accessibility on (`toolkit-accessibility` + `org.a11y.Status.IsEnabled`). | real GTK text box under Xvfb (`test_typing_checks_with_atspi`) |

## Still to do / to check on a real Ubuntu desktop
- Real Chrome/Brave/Edge with the extension: upload, paste, drop (Snap Chromium can't use native hosts).
- GNOME Wayland session and "Ubuntu on Xorg": tray icon, card placement, clipboard (XWayland sync), notifications.
- Typing checks in Firefox, Chrome (needs `toolkit-accessibility`), LibreOffice, Electron apps.
- First tagged release: check the CI jobs and the update card offering the `.deb` (`DEB_NAME` in `updater.py`).
- Size: spaCy's `collect_all` bundles every language (112 MB); only English is needed (same on Windows).

## Gotchas found while porting
- `Atspi.set_main_context(ctx)` must be called **after** `Atspi.init()`; before it, events only
  arrive during our own synchronous calls. AT-SPI calls inside an event callback fail — only store
  the source there.
- pystray gives up (ValueError) when the AppIndicator typelib is missing instead of trying GTK/Xorg.
- PyInstaller sets `LD_LIBRARY_PATH` etc. for the bundle; every system program SENSIQ starts
  (tesseract, xclip, gdbus, pkexec…) gets `plat.child_env()` so they load the system's libraries.
- Tests built PDF text with `create_unicode_buffer` (wchar_t is 32-bit on Linux): use UTF-16 bytes.
- Tesseract reads pen strokes as words (e.g. `L\/N/`); the signature finder ignores tall, non-letter "words".

## Original plan

### Start here (on the Ubuntu machine)

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-dev python3-tk \
    tesseract-ocr tesseract-ocr-eng tesseract-ocr-hin \
    gir1.2-ayatanaappindicator3-0.1 libnotify-bin wl-clipboard xclip
git clone https://github.com/umesh-21/SENSIQ.git && cd SENSIQ
code .            # open the folder in VS Code
```

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
