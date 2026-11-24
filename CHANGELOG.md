# Changelog

Each version's section is published as its GitHub release notes and shown in the app's
"update available" card as **What's new**. Write it for the people using the app.

## [1.2.0]

### New
- **Automatic updates from GitHub.** PII Guard checks for a new version twice a day and shows what's new. Click **Update now** to download it, check it and install it. Turn this off in the tray menu ("Check for updates automatically").
- **"▸ where" on every report row.** It shows the line each name, place, date of birth or number was found in, with the page number for PDFs.
- **Undo for "not private".** Clicked it by mistake? Click **undo** on the same row and the value is flagged again.
- **Real setup file** (`PIIGuard-Setup-1.2.0.exe`) with an uninstaller. Copy it to any Windows 10/11 PC and run it, no admin rights needed.

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
