# Changelog

Each version's section is published as its GitHub release notes and shown in the app's
"update available" card as **What's new**. Write it for the people using the app.

## [1.7.2]

- **The Linux package is back:** 1.7.1's Linux download was missing because a component it uses changed. This version contains all of 1.7.1's fixes below (the shield in the top bar, cards on your screen, typing checks in Brave and Chrome, more uploads checked in the browser). Nothing else changed; on Windows it is the same as 1.7.1.

## [1.7.1]

### Linux: fixes from the first real-desktop tests
- **The shield now shows in the top bar.** In 1.7.0 SENSIQ ran but its icon never appeared.
- **Cards appear on your screen.** On some PCs the report card opened outside the visible screen, so checking a file seemed to do nothing.
- **SENSIQ starts by itself when you sign in**, and if the top bar can't show its shield, it tells you what to do.
- **Typing checks work in Brave and Chrome**, including chat boxes like claude.ai and Gmail. When you turn typing checks on, SENSIQ also sets up Brave and Chrome; close the browser fully and open it again from the app list. The first start offers typing checks with a **Turn on** button.
- **Uploads in the browser are checked on more websites:** upload buttons that sites hide or build into their own controls (Gmail, WhatsApp Web, claude.ai…) now go through SENSIQ too, and tabs that were already open are protected as soon as the extension is added. After updating, click reload on SENSIQ in `brave://extensions` / `chrome://extensions`.
- Chrome is set up for the extension even before its first start.

### Fixed (Windows and Linux)
- The word "Aadhaar" before a number is no longer replaced as if it were a person's name.
- File contents never reach the log, even when SENSIQ runs with `--debug`.
- Opening SENSIQ again while it's running shows that it's on, instead of an "already running" box.

## [1.7.0]

### SENSIQ for Linux (Ubuntu 22.04 and 24.04)
- **Install with one file:** `sudo apt install ./sensiq_x.y.z_amd64.deb`. Everything still runs on your computer only; images are read with Tesseract (English and Hindi).
- **Check a file before you share it:** in Files, right-click → **Scripts → Check with SENSIQ**, or the shield in the top bar → **Check a file…**. Copied images and files are checked by themselves, and web uploads through the browser extension, the same as on Windows.
- **Typing warnings (test version):** shield → **Typing checks: turn on…**. Works in GNOME apps, LibreOffice, Firefox and Chrome.
- **Updates are only offered:** nothing is installed until you click **Update now**; then Ubuntu asks for your password.

### Also new (Windows and Linux)
- Tray → **Check a file…** checks any file you pick.

### Fixed
- An empty line to sign on ("Signature: ________") is no longer listed as a signature.

## [1.6.0]

### Fixed
- **Names on ID-card photos are found reliably.** The patterned background of Aadhaar cards produces OCR noise that could stop the name model from recognising a clear name (e.g. "Ramesh Kumar N P"). Short texts such as cards, screenshots and forms are now also checked a few lines at a time, which adds about 0.1 s.
- **Background noise isn't flagged as a name** any more (e.g. "TTtTR").

### Changed
- **Updates are only offered, never installed by themselves.** You get a Windows notification and an **Update available…** item in the tray menu; nothing is installed until you click **Update now**.

## [1.5.0]

### Nothing is skipped without you seeing it
- **Details you marked "not private" are still listed on file cards**, unticked, with "marked not private · flag again". Before, they disappeared from the card and stayed in the shared file.
- **ID numbers can't be marked "not private"** any more (Aadhaar, PAN, card, bank, passport…). Untick one to keep it in a single file.
- **Only the tick box changes a row.** Clicking a row's text no longer unticks it by accident.
- **A red line names what won't be replaced**, e.g. "⚠ Will NOT be replaced: Person's name", whenever something is unticked.
- **Tray → Not-private items…** lists everything you marked, showing only masked values like XXXX XXXX 2997, and lets you remove items one at a time.
- **The tiny copy of the Aadhaar number** under the small second photo is now covered too.

## [1.4.0]

### PII Guard is now SENSIQ
- New name everywhere: the tray icon, the cards, the Start menu, the program (`SENSIQ.exe`), the setup file (`SENSIQ-Setup-1.4.0.exe`) and the install folder (`%LOCALAPPDATA%\Programs\SENSIQ`). The GitHub page is now **github.com/umesh-21/SENSIQ**; the old address still works.
- **Nothing to redo:** your settings, ignored items, "not private" names, safe-copies folder choice and "Start with Windows" are carried over automatically.
- **Browser extension users:** add the extension again once from its new folder. Right-click the shield → **Check uploads in my browser (set up)…**, then **Load unpacked** in `chrome://extensions` (or Edge/Brave). Remove the old "PII Guard" entry there.

## [1.3.0]

### New
- **Close a report and pick another file.** The report card now has a **✕** in its corner. In an upload window, choosing a different file replaces the report with the new file's straight away.
- **Choose where safe copies are saved.** The setup asks for a folder. You can change it any time from the tray (**Safe copies folder → Change folder…**). Copies in a folder you choose are kept, and the default folder is still cleared after 24 hours.
- **"Checking…" before you upload.** As soon as you pick a file to upload or attach, SENSIQ says it's checking and asks you to wait. Then you get the report, or "No personal information found" for a clean file.

## [1.2.0]

### New
- **Automatic updates from GitHub.** SENSIQ checks for a new version twice a day and shows what's new. Click **Update now** to download it, check it and install it. Turn this off in the tray menu ("Check for updates automatically").
- **"▸ where" on every report row.** It shows the line each name, place, date of birth or number was found in, with the page number for PDFs.
- **Undo for "not private".** Clicked it by mistake? Click **undo** on the same row and the value is flagged again.
- **Real setup file** (`SENSIQ-Setup-1.2.0.exe`) with an uninstaller. Copy it to any Windows 10/11 PC and run it, no admin rights needed.

### Better detection on ID cards
- QR codes that are too dense or blurred to read (like the Aadhaar secure QR in a phone photo) are now found and covered.
- Initials next to a name ("Ramesh Kumar N P", "K.R. Nair") are covered with the name.
- A 6-digit PIN code at the end of an address is caught even without a "PIN" label.
- The small second copy of the Aadhaar number (under the second photo) is covered too.
- Photos of ID cards are read more carefully, so fewer words are missed.

## [1.1.0]

### New
- Browser extension for Chrome, Edge and Brave: uploads, pastes and drops on any website are checked first, and the website gets the safe copy if you choose to replace details.
- Checks QR codes, faces / ID photos and signatures in pictures and PDFs.
- Reads HEIC photos, ZIP files (rebuilt with safe copies inside), and old .doc/.xls files (read only).
- Hindi text in images (tray → "Read Hindi in images").
- Names found from labels ("Name:", "S/O", "नाम:") and a **not private** link per row.
- Every page of long PDFs is checked; no 10-file limit.

## [1.0.0]

- First version: warns about Aadhaar, PAN, phone numbers, emails, card and bank numbers, names and places while you type in any app, and in images and files you copy, drag or upload.
