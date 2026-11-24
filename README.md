<div align="center">

# SENSIQ

### Local AI Privacy Intelligence

***Discover before you disclose.***

[![Latest release](https://img.shields.io/github/v/release/umesh-21/SENSIQ?label=download)](https://github.com/umesh-21/SENSIQ/releases/latest)
[![Build](https://github.com/umesh-21/SENSIQ/actions/workflows/release.yml/badge.svg)](https://github.com/umesh-21/SENSIQ/actions/workflows/release.yml)
![Windows 10/11](https://img.shields.io/badge/Windows-10%20%7C%2011-0078D4)
![Runs offline](https://img.shields.io/badge/AI-on%20your%20PC-0E7C7B)

</div>

## Product capabilities

| | |
|---|---|
| ⌨️ **Typing protection** | Warns you the moment you type an Aadhaar, PAN, card, bank or phone number, password, name or address into any app or website, and replaces it in one click. |
| 🖼️ **Image intelligence** | Reads screenshots and photos, including ID cards and Hindi text, and finds faces, signatures and QR codes, even ones too blurred to scan. |
| 📄 **Document scanning** | Checks every page of PDFs (scanned ones too), Word, Excel, PowerPoint, ZIP and text files, and shows the exact line each detail was found in. |
| 🔒 **Safe sharing** | When you upload, paste, drag or attach a file, you pick the details to hide and the safe copy is shared instead. Your original is never changed. |

---

**On-device privacy guard for Windows 10/11** (called *PII Guard* up to version 1.3.0). It stops Aadhaar numbers, PAN, phone numbers, names, addresses, ID-card photos and QR codes from leaving your PC by accident, in what you type and in the images, PDFs and documents you upload, paste or drag.

## Download and install

1. Go to **[Releases → latest](https://github.com/umesh-21/SENSIQ/releases/latest)** and download `SENSIQ-Setup-x.y.z.exe`. That one file is all you need; it can also be copied to other PCs on a USB stick.
2. Run it. No administrator rights, Python or internet are needed. It installs for the current user under `%LOCALAPPDATA%\Programs\SENSIQ` and adds an uninstaller (Settings → Apps).
3. If Windows shows "Windows protected your PC", click **More info → Run anyway**. The setup file isn't code-signed, but you can compare its SHA-256 with the one on the release page.
4. A teal shield appears in the system tray. Right-click it for all options.
5. Optional, to check uploads on websites: tray → **Check uploads in my browser (set up)…** (see [Browser uploads](#browser-uploads-extension)).

Requirements: Windows 10 or 11, 64-bit.

## Updates

SENSIQ checks this repository's [Releases](https://github.com/umesh-21/SENSIQ/releases) about 90 seconds after it starts and then every 12 hours. **SENSIQ never updates by itself.** When a newer version is published, Windows shows a notification ("Update found: SENSIQ x.y.z is available"), the tray menu gets an **Update available…** item, and a card opens with:

- the new version number and download size;
- **What's new**: that version's notes from [CHANGELOG.md](CHANGELOG.md);
- **Update now** downloads the setup file, checks it against the SHA-256 published with the release (a mismatching or unverifiable file is never run), installs it over the current version and restarts SENSIQ. Your settings are kept;
- **Later** asks again at the next check; **Skip this version** stays quiet until an even newer one; **See on GitHub** opens the release page.

Tray → **Check for updates now** checks at once. Tray → **Check for updates automatically** turns the background check off (`updates.check` in settings).

## What it does

SENSIQ warns you when you type personal data (Aadhaar, PAN, card and bank numbers, phone numbers, emails, UPI IDs, passwords, API keys, names and more) into any app or website. It shows a badge in the corner of the text box, explains what it found and replaces it with a masked version in one click.

It also checks **images, PDFs and documents at the moment you share them**: when you copy an image or screenshot, copy a file, drag a file out of File Explorer, or pick a file in an Open / Upload / Attach dialog. A card lists what it found, with a tick box for each detail. One click makes a safe copy with the ticked details replaced and shares that instead of the original. Originals are never changed.

All detection runs on your PC. Nothing you type or share is ever sent anywhere; the only network request is the update check (see [Privacy](#privacy-and-offline-operation)).

## What it detects

| Kind | Method | Example replacement |
|---|---|---|
| Aadhaar | pattern + Verhoeff checksum | `XXXX XXXX 7615` |
| Card number | pattern + Luhn checksum | `XXXX XXXX XXXX 1111` |
| PAN, IFSC, Voter ID, DL, passport, SSN | format rules (+ keyword context where formats are ambiguous) | `XXXXXX234F` |
| Bank account | digits + "account / a/c" context | `XXXXXXXX9012` |
| Phone (Indian and international), email, UPI ID | patterns + validation | `+91 XXXXX X3210`, `r***@example.com` |
| Date of birth, PIN code | patterns + "DOB / born / pincode" context | `XX/XX/1996` |
| Passwords, PINs, OTPs, API keys, tokens, private keys | patterns | `[REDACTED]` |
| GSTIN, vehicle registration, crypto wallets, IBAN, international phone numbers | [Presidio](https://github.com/data-privacy-stack/presidio) recognizers | `XXXXXXXXXXX1ZV` |
| Person names and places (optionally organisations) | local BERT NER model (ONNX, ~110 MB, CPU) | `[name]`, `[location]` |

The checksums and context rules keep false alarms low. Order numbers, version strings and plain dates don't trigger it.

Capitalisation doesn't matter. `dob`, `DOB`, `D.O.B.`, `date of birth`, `Year of Birth` and `जन्म तिथि` all mark a date of birth. Names typed all in lowercase (`rahul sharma`) or printed in CAPITALS on ID cards (`PRIYA PATEL`) are re-capitalised before the name model sees them. Results from that pass must be more confident, and card labels like DOB or MALE are ignored.

## Pictures: QR codes, faces, signatures

- **QR codes and barcodes** are decoded offline. A code holding private details, or an Aadhaar secure QR, is listed and covered completely in the safe copy. Plain link codes are left alone.
- **QR codes too dense or blurred to decode** (like the Aadhaar secure QR in a phone photo) are still found by their black-and-white checker texture. On an ID card they're listed as "Aadhaar QR code", and elsewhere as "QR code that couldn't be read". Either way the safe copy covers them.
- **ID-card photos** are also read as a 2× grey copy and in overlapping halves, and words the first pass missed are added. Initials next to a name ("Ramesh Kumar N P") are covered with the name. A 6-digit PIN at the end of an address is caught without a "PIN" label. Aadhaar cards print the number again in tiny type under a small second photo. OCR can't read it, so on an image that shows an Aadhaar number, the strip under any photo much smaller than the main one is covered too ("Small copy of the Aadhaar number"). If a piece of a found Aadhaar number appears elsewhere (for example the small copy under the second photo, misread as "2SS4"), the whole printed number there is covered too.
- **Faces** are found with a 1 MB offline model. They're listed as "Photo on an ID card" when the file shows an ID, else "Face in a picture".
- **Signatures** are found next to their label ("Signature", "Signed", "हस्ताक्षर") where there's ink.
- **Hard photos:** if a photo gives almost no text, it's read again cleaned up (contrast, sharpening, enlarging) and turned 90°, 180° and 270°.
- **Hindi:** tray → "Read Hindi in images (install)…" adds Windows' Hindi OCR (needs your OK in a Windows prompt). Images are then read in English and Hindi. Other installed Indian languages are used too.

Names are also found from their labels, in English or Hindi ("Name:", "Father's name:", "S/O", "D/O", "W/O", "Dear …", "नाम:", "पिता का नाम:"). Rows for names, places, dates and other low-risk details have a **not private** link. Names and places are added to `never_flag_words`, and other values to the ignore list as a fingerprint with a masked hint (for example `Aadhaar number: XXXX XXXX 2997`, never the real value). Clicked it by mistake? Click **undo**. **ID numbers (Aadhaar, PAN, card, bank, passport…) have no "not private" link**: untick them to keep them for one file only. Anything marked "not private" still appears on file cards, unticked, with "marked not private · flag again", so nothing is left in a file without you seeing it. Review or remove all of them under tray → **Not-private items…**.

## Browser uploads (extension)

Tray → "Check uploads in my browser (set up)…" opens a short guide: in `chrome://extensions` (or `edge://`, `brave://`), turn on Developer mode, click Load unpacked and choose the `browser_extension` folder in the app's install folder.

With the extension, every file a web page receives is held until SENSIQ has checked it: upload buttons, drag and drop (from anywhere, including the downloads list and email attachments), paste, and sites using the newer file picker. Clean files pass straight through. Files with details show the card: **Replace and send** gives the page the safe copy; **Use original** sends the file as it is. With no choice within 5 minutes the file isn't given to the page. If SENSIQ isn't running, pages work as normal.

On Edge profiles signed in to a work or school account, the organisation may block developer-loaded extensions. Use Chrome or a personal Edge profile in that case.

## Images, PDFs and documents

| You do this | SENSIQ checks |
|---|---|
| Copy an image, or take a screenshot with Win+Shift+S | the copied image (OCR) |
| Copy files in File Explorer (to paste into WhatsApp, Gmail, Teams...) | each copied file |
| Drag files out of File Explorer or off the desktop | each dragged file, as the drag starts |
| Select a file in an Open / Upload / Attach dialog (browser uploads, email attachments) | the selected file, before you click Open |

Supported files: images (PNG, JPG, BMP, GIF, TIFF, WEBP), PDF (text, and OCR for scanned pages), Word, PowerPoint, Excel, OpenDocument text, and plain-text formats (TXT, CSV, JSON, HTML, EML...). Images are read with the OCR engine built into Windows 10/11, so nothing extra is installed.

Every page and every character is checked:
- **PDFs:** all pages. Scanned pages are OCR'd. On typed pages, pictures placed on the page are OCR'd too, such as a scanned ID pasted into a form.
- **Multi-page images:** every page of a TIFF scan or GIF.
- **Very tall images:** long screenshots are read in overlapping slices at full sharpness.
- **Long files:** a short notice appears while they are checked. A 230-page scanned PDF takes about 40 seconds.
- **The same value repeated** (your name on every page) is one row on the card, marked `×230`, with one tick box.
- **"▸ where"** on each row opens the line the detail was found in, with the detail highlighted. It shows the page number for PDFs and multi-page scans, and the line above when the line alone says little (a PIN code on its own line). Up to 5 places are shown per row. Passwords and keys stay hidden there too; QR codes, photos and signatures say where in the picture they are.

Also checked:
- **Folders:** searched with all their sub-folders.
- **ZIP files:** opened and rebuilt with safe copies inside.
- **iPhone photos (HEIC).**
- **Old .doc/.xls/.ppt:** checked but not changed; save them as .docx/.xlsx/.pptx to get a safe copy.

Password-protected or damaged files show a warning that they couldn't be checked.

Optional (tray → "Offer a check when I open a file"): when you open a document or picture, SENSIQ offers a one-click check.

Limits: up to 500 files per action and 200 MB per file. `file_scan.max_pdf_pages` can cap pages; `0`, the default, means all.

SENSIQ never searches or indexes your folders. For a file dialog, it asks the dialog's own file list for the full paths of the selected items: it sends the list's built-in Copy command and reads the paths, then puts your clipboard back exactly as it was. This works in any folder, drive, OneDrive, Home or search view. For drags it asks File Explorer which items are selected, and for copied files the clipboard already holds the paths.

If you double-click a file, or press Enter at once, the dialog may close before the card can appear. SENSIQ still checks the file. It tells Open from Cancel by watching whether the dialog was closed with Cancel, the window's X, Esc or Alt+F4. For this it hooks only those two keys, and only while a file dialog is open. The card then says the file was already added to the page and offers a safe copy to paste in its place.

When you pick a file to upload, attach or send, a small **"SENSIQ is checking…"** note appears at once, asking you to wait for the result. It is replaced by the report card, or by **"✓ No personal information found"** for a clean file. (Copying with Ctrl+C stays quiet unless something is found.) Save dialogs are ignored. Drags that start outside File Explorer, for example from a browser's downloads list, aren't seen.

### Replacing details before you share

The card lists every detail with a tick box, all ticked. Only the tick box itself changes it; clicking the row's text doesn't. Whenever something is unticked, a red line above the button says exactly what **will not** be replaced, for example "⚠ Will NOT be replaced: Person's name". The **✕** in its top-right corner closes the report without doing anything, so you can pick a different file. In an upload window, just selecting another file also replaces the report with the new file's. Untick anything you want to keep, then press the button:

| Shared by | Button | What is shared instead |
|---|---|---|
| Copied image or screenshot | Replace and copy | The clipboard gets the safe image, so Ctrl+V pastes that |
| Copied files | Replace and copy | The clipboard gets the safe copies, so Ctrl+V pastes those |
| Open / Upload / Attach dialog | Replace and upload | The dialog is pointed at the safe copy and Open is pressed for you |
| Dragged files | Make safe copy | A drag can't be changed once it has started. The safe copies go on the clipboard and open in File Explorer, ready to drag again |

How each type is made safe:

- **Images**: each value is covered with a dark box showing its replacement (`XXXX XXXX 7615`, `[name]`). Boxes are placed from the OCR word positions.
- **PDFs**: every page is turned into an image and covered the same way. A box drawn over real PDF text would leave the text underneath copyable; this way it's really gone. The safe PDF's text can no longer be selected.
- **Word, PowerPoint, Excel, OpenDocument, text files**: the values are replaced with their masked form and the rest of the file is kept. A value split oddly across formatting may be missed; the message says so if anything wasn't found.

Replacements follow the mask style (tray → "Replace with labels like [Aadhaar]"). "Use original" closes the card and changes nothing.

**Where safe copies are saved.** You choose this on the setup's **Safe copies folder** page, and can change it any time from tray → **Safe copies folder** → Change folder… (or Open, or Use the default folder). The default is `%LOCALAPPDATA%\SENSIQ\Safe copies`, and copies there are deleted after 24 hours. Copies in a folder you choose are kept until you delete them. Each action gets its own dated sub-folder. Updates keep your choice.

## Build it yourself

Most people should use the setup file from [Releases](https://github.com/umesh-21/SENSIQ/releases). To build from source you need Windows 10 or 11 (64-bit), [Python 3.11 64-bit](https://www.python.org/downloads/windows/) with "Add to PATH" ticked, and [Inno Setup 6](https://jrsoftware.org/isdl.php) for the setup file (`winget install JRSoftware.InnoSetup --scope user`).

```
git clone https://github.com/umesh-21/SENSIQ.git
cd SENSIQ
build.bat
```

The script:

1. creates `.venv` and installs the packages in `requirements.txt`
2. downloads the name model (`Xenova/bert-base-NER`, quantized ONNX), the face detector and spaCy's small English model into `models\` (they aren't stored in git: the name model is over GitHub's 100 MB file limit)
3. runs the unit tests and an end-to-end self-test with the real model
4. packages everything with PyInstaller into `dist\SENSIQ\`
5. builds `dist\installer\SENSIQ-Setup-<version>.exe` (or `dist\SENSIQ-portable.zip` if Inno Setup isn't installed)

The build is the only step that needs the internet. For a quick run from source after the first build: `run_dev.bat`.

To use a different token-classification model, pass any Hugging Face repo that has an ONNX export: `build.ps1 -Model "<repo>"`.

## Publishing a new version (maintainers)

1. Make your changes and run the tests: `.venv\Scripts\python.exe -m pytest -q tests`.
2. Add a section at the top of [CHANGELOG.md](CHANGELOG.md), for example `## [1.3.0]`, listing what's new in plain words. This text becomes the release notes and the **What's new** box users see in the app.
3. Run `powershell -ExecutionPolicy Bypass -File tools\release.ps1 -Version 1.3.0`. It sets the version in every file (`tools/set_version.py`), commits, tags `v1.3.0` and pushes.
4. GitHub Actions ([.github/workflows/release.yml](.github/workflows/release.yml)) builds the app on Windows, creates the setup file and its `.sha256`, and publishes the release with the CHANGELOG notes. That takes about 20 minutes; follow it on the **Actions** tab.
5. Within 12 hours every installed copy shows the update card (or right away with tray → Check for updates now).

Pushing commits without a new tag doesn't release anything. Users only get versions that were tagged and built.

## Demo walkthrough

1. Open the demo page (Start menu → SENSIQ demo page). It runs from disk, no internet.
2. Click "Put in message box" next to "Government IDs". About half a second later a red badge with a number appears in the bottom-right corner of the message box, and a short alert names the most serious item.
3. Click the badge. The panel lists each finding: the original value struck through in red, and the safe replacement in teal.
4. Click Replace on the Aadhaar row. The number in the text box changes to `XXXX XXXX 7615` and the count drops.
5. Click Replace all to fix the rest. The badge disappears.
6. Click "Use labels like [Aadhaar]" at the bottom of the panel to switch the replacement style.
7. Paste the clean sample. Nothing appears.
8. Now repeat in other apps: Notepad, Word, Outlook, Teams, WhatsApp Desktop, Slack, Chrome or Edge on any site.
9. Right-click the tray shield → Run self-test. A report opens showing every detector, the name model status and timings.

## How it works

```
 Any app's text box ──UI Automation──▶ FocusWatcher thread ──text──▶ Detector thread
        ▲                                (reads focused field          (rules + checksums,
        │                                 every 300 ms)                 BERT NER on CPU)
        │                                                                     │
        └──── select value + paste ◀── Replace click ◀── Overlay (Tk) ◀── findings
```

- **Reading text.** Windows UI Automation is the accessibility API used by screen readers. It exposes the focused text box of almost every app, including browsers, Office and Electron apps. SENSIQ reads only editable fields. It skips password boxes, its own windows, and apps in `deny_processes`.
- **Detecting.** Detection waits until you pause typing (500 ms). Rules run on the whole text in under a millisecond. The name model runs paragraph by paragraph and caches results, so only the paragraph you edit is re-checked.
- **Showing.** The badge and panel are topmost, non-activating windows. Clicking them never moves your cursor out of the app you're typing in.
- **Replacing.** SENSIQ selects exactly the detected value in the target field (UI Automation `FindText` + `Select`) and pastes the replacement. A paste triggers the same events as a user paste, so web apps notice the change. It restores your clipboard afterwards. For simple fields it falls back to setting the value directly. If a field can't be edited from outside, the safe text is copied to your clipboard and you're told to paste it.

## Privacy and offline operation

- Detection is fully offline. The model, tokenizer and rules ship inside the installer, and ONNX Runtime runs on the CPU.
- The only network request is the update check: an anonymous HTTPS GET to `api.github.com` for this repository's latest release (and the download if you click Update now). No text, file, name or machine detail is sent. Turn it off with tray → "Check for updates automatically".
- Typed text and text read from images and files live only in memory. They are never written to the log. The only files SENSIQ writes are the safe copies you ask for, which are deleted after 24 hours.
- "Always ignore" stores a SHA-256 fingerprint of the value, never the value itself.
- Uninstalling removes the app and `%APPDATA%\SENSIQ` (settings, ignore list, log).

To check this yourself, block `SENSIQ.exe` in Windows Firewall or disconnect from the network. Everything except the update check keeps working.

## Settings

Tray menu → Open settings file (`%APPDATA%\SENSIQ\settings.json`), then Reload settings.

| Setting | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Same as Pause/Resume in the tray. A pause ends by itself after 1 hour; left-clicking the shield only shows the status |
| `debounce_ms` | `500` | Pause after typing before checking |
| `mask_style` | `"partial"` | `"partial"` (XXXX 1234) or `"placeholder"` ([Aadhaar]) |
| `ner.enabled` | `true` | Use the name model |
| `ner.threshold` | `0.60` | Minimum confidence for names and places |
| `ner.labels` | `PER`, `LOC` on | Set `ORG` to `true` to also flag organisations |
| `presidio.enabled` | `true` | Use Presidio's extra recognizers |
| `file_scan.enabled` | `true` | Check images and files you share |
| `file_scan.clipboard` / `drag_drop` / `file_dialogs` | `true` | Turn each trigger on or off |
| `file_scan.max_file_mb` / `max_pdf_pages` | `200` / `0` | Limits for large files (`0` = every page) |
| `file_scan.safe_copies_folder` | `""` | Folder for safe copies (`""` = default, cleared after 24 h). Set from the tray or the setup |
| `file_scan.browser_extension` / `opened_files` | `true` / `false` | Browser-extension link; offer a check when you open a file |
| `never_flag_words` | `[]` | Names/places never flagged (the card's "not private" adds here) |
| `updates.check` | `true` | Check GitHub for new versions twice a day |
| `updates.skip_version` | `""` | Version you chose to skip ("Skip this version") |
| `detectors.<KIND>` | on (IP off) | Turn individual detectors on or off |
| `deny_processes` | password managers, RDP | Apps SENSIQ never reads |
| `document_editor_processes` | Word, Outlook, OneNote, Notepad… | Apps whose document area counts as editable |
| `show_alert_for_high_risk` | `true` | Short alert when an ID, card, password or key appears |

## Troubleshooting

| Problem | Fix |
|---|---|
| No badge in Chrome or Edge | Open `chrome://accessibility` (or `edge://accessibility`) and tick "Native accessibility API support". Most systems don't need this. |
| No badge in an Electron app (VS Code, Discord…) | Start the app with `--force-renderer-accessibility`. In VS Code you can instead set `"editor.accessibilitySupport": "on"`. |
| No badge in Google Docs | Docs draws text on a canvas. Turn on Tools → Accessibility → Screen reader support. |
| Nothing in an app running as administrator | Windows blocks normal apps from reading elevated windows. Run SENSIQ as administrator too, or leave that app out. |
| "Name detection is off" | The model files are missing. Run the self-test from the tray to see why, and rebuild if needed. |
| Replace didn't change the text | Some apps refuse outside edits. The safe text is already on your clipboard: select the value and press Ctrl+V. |
| Something else | Tray → Open log folder → `sensiq.log`. Run `run_dev.bat` from the source folder for a live debug log. |

## Known limits

- Text drawn directly on a canvas (games, some design tools, remote desktop and VM windows) isn't exposed to UI Automation, so it can't be read.
- Image OCR uses the languages installed in Windows (English here). For Hindi text on cards, add Hindi under Settings → Time & language → Language. Numbers are read in any case.
- Images already open in a viewer, or PDFs you only look at, aren't scanned. Checks run when you copy, drag or upload them.
- Handwriting and very blurry photos may not be read.
- The bundled model is trained on English news text, so it finds English and most Indian names written in Latin script. It doesn't find names written in Devanagari or other scripts. A multilingual NER model can be swapped in with `build.ps1 -Model`.
- Pattern rules focus on Indian and common international formats. Add more in `sensiq/detector/rules.py`.
- "Replace all" in rich editors replaces items one at a time from the end of the text, which takes about a second.

## Project layout

```
sensiq/
  app.py           main program, event loop, tray actions, CLI
  capture.py       UI Automation: read focused field, write replacements
  overlay.py       badge, panel and alerts (Tk)
  worker.py        debounced background detection
  detector/
    rules.py       regex detectors
    validators.py  Verhoeff, Luhn and format checks
    ner.py         ONNX Runtime + tokenizers NER
    engine.py      combines results, resolves overlaps
    presidio_layer.py  Presidio recognizers (offline, spaCy en_core_web_sm)
  sharewatch.py    clipboard, drag-and-drop and file-dialog watchers + file scanner
  filescan.py      text from images, PDFs, Office and text files (with word positions)
  redact.py        safe copies: boxes on images/PDF pages, masked text in documents
  ocr.py           Windows built-in OCR
  masking.py       replacement text
  config.py        settings and ignore list
  updater.py       update check, verified download and install from GitHub Releases
  browserlink.py, nativehost.py   link to the browser extension
  visual.py        QR codes, faces, signatures in pictures
  selftest.py      offline end-to-end check
  tray.py, icon.py, winutil.py
browser_extension/ Chrome/Edge/Brave extension (Manifest V3)
tools/             model download, icon, set_version.py, release.ps1
.github/workflows/ release.yml: builds and publishes a release for each version tag
CHANGELOG.md       what's new in each version (shown in the update card)
demo/demo.html     offline test page
tests/             unit tests (pytest)
build.ps1 / build.bat / installer.iss
```

Command-line options: `SENSIQ.exe --selftest`, `--no-ner` (patterns only), `--no-tray`, `--debug`.

## Licences

The default model is [dslim/bert-base-NER](https://huggingface.co/dslim/bert-base-NER) (MIT), using the ONNX export by Xenova. [Presidio](https://github.com/data-privacy-stack/presidio) and spaCy are MIT-licensed, pypdfium2 is Apache-2.0/BSD-3. Third-party Python packages keep their own licences.
