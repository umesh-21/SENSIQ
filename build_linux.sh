#!/usr/bin/env bash
# Builds SENSIQ for Linux: dist/SENSIQ/ (PyInstaller one-folder app) and
# dist/sensiq_X.Y.Z_amd64.deb (+ .sha256), the Linux counterpart of build.ps1.
#
# Build on Ubuntu 22.04 so the package runs on 22.04 and 24.04 (older glibc). The internet is
# used only here (Python packages, models); the installed app works offline.
#
#   ./build_linux.sh                  everything
#   ./build_linux.sh --skip-tests     no tests (CI runs them separately)
#   ./build_linux.sh --skip-deb       app folder only
#
# System packages needed to build (the script tells you what's missing):
#   sudo apt install python3-venv python3-dev python3-tk python3-gi gir1.2-gtk-3.0 \
#     gir1.2-ayatanaappindicator3-0.1 gir1.2-atspi-2.0 tesseract-ocr tesseract-ocr-eng \
#     tesseract-ocr-hin xvfb xclip libnotify-bin
set -euo pipefail
cd "$(dirname "$0")"

MODEL="Xenova/bert-base-NER"
FACE_URL="https://github.com/Linzaer/Ultra-Light-Fast-Generic-Face-Detector-1MB/raw/master/models/onnx/version-RFB-640.onnx"
FACE_SHA="8f4c659275977e7a3bfbfa339a9c769ad793df50f9c0baa8c14b11baa1646430"
SKIP_TESTS=0
SKIP_DEB=0
for a in "$@"; do
  case "$a" in
    --skip-tests) SKIP_TESTS=1 ;;
    --skip-deb) SKIP_DEB=1 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done

step() { printf '\n==> %s\n' "$*"; }
die() { echo "ERROR: $*" >&2; exit 1; }

VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' sensiq/__init__.py)
[ -n "$VERSION" ] || die "couldn't read __version__ from sensiq/__init__.py"
ARCH=$(dpkg --print-architecture 2>/dev/null || echo amd64)
[ "$ARCH" = "amd64" ] || die "only amd64 is built (this machine: $ARCH)"

# 1. System packages ------------------------------------------------------------------------
step "Checking system packages"
PY=/usr/bin/python3   # the system Python: python3-gi (tray, typing checks) is built for it
$PY -c 'import sys; v=sys.version_info[:2]; assert (3,10)<=v<=(3,12), "Python 3.10-3.12 needed, found %d.%d" % v' \
  || die "unsupported Python"
missing=()
$PY -c 'import tkinter' 2>/dev/null || missing+=(python3-tk)
$PY -c 'import ensurepip' 2>/dev/null || missing+=(python3-venv)
$PY -c 'import gi; gi.require_version("Gtk", "3.0")' 2>/dev/null || missing+=(python3-gi gir1.2-gtk-3.0)
$PY -c 'import gi; gi.require_version("AyatanaAppIndicator3", "0.1")' 2>/dev/null || missing+=(gir1.2-ayatanaappindicator3-0.1)
$PY -c 'import gi; gi.require_version("Atspi", "2.0")' 2>/dev/null || missing+=(gir1.2-atspi-2.0)
command -v dpkg-deb >/dev/null || missing+=(dpkg)
if [ ${#missing[@]} -gt 0 ]; then
  die "missing: sudo apt install ${missing[*]}"
fi
command -v tesseract >/dev/null || echo "WARNING: tesseract-ocr isn't installed: image tests will be skipped"

# 2. Virtual environment --------------------------------------------------------------------
step "Creating virtual environment (.venv, with the system's python3-gi) and installing packages"
if [ ! -x .venv/bin/python ]; then
  $PY -m venv --system-site-packages .venv
elif ! grep -q "include-system-site-packages = true" .venv/pyvenv.cfg; then
  sed -i 's/include-system-site-packages = false/include-system-site-packages = true/' .venv/pyvenv.cfg
fi
VPY="$PWD/.venv/bin/python"
"$VPY" -m pip install --upgrade pip --quiet
"$VPY" -m pip install -r requirements.txt -r requirements-build.txt --quiet

# 3. Models ---------------------------------------------------------------------------------
step "Downloading the name-detection model ($MODEL)"
"$VPY" tools/download_model.py --repo "$MODEL" --out models/ner

step "Downloading the face detector (Ultra-Light-Fast-Generic-Face-Detector-1MB, MIT)"
FACE=models/face/face.onnx
if [ ! -f "$FACE" ] || [ "$(sha256sum "$FACE" | cut -d' ' -f1)" != "$FACE_SHA" ]; then
  mkdir -p models/face
  curl -fsSL -o "$FACE" "$FACE_URL"
  [ "$(sha256sum "$FACE" | cut -d' ' -f1)" = "$FACE_SHA" ] || die "face model doesn't match the expected checksum"
fi

step "Installing the small spaCy model used by Presidio (en_core_web_sm)"
"$VPY" -c "import spacy, sys; sys.exit(0 if spacy.util.is_package('en_core_web_sm') else 1)" \
  || "$VPY" -m spacy download en_core_web_sm

step "Creating icon"
"$VPY" tools/make_icon.py assets/sensiq.png 256

# 4. Tests ----------------------------------------------------------------------------------
if [ "$SKIP_TESTS" = 0 ]; then
  step "Running unit tests (on a virtual display when xvfb-run is there)"
  if command -v xvfb-run >/dev/null; then
    xvfb-run -a "$VPY" -m pytest -q tests
  else
    "$VPY" -m pytest -q tests
  fi
  step "Running the self-test (with the real models)"
  "$VPY" -m sensiq --selftest --no-open
fi

# 5. Package the app ------------------------------------------------------------------------
step "Packaging with PyInstaller"
"$VPY" -m PyInstaller --noconfirm --clean --distpath dist --workpath build/pyinstaller \
  packaging/linux/sensiq.spec

step "Checking the packaged app"
dist/sensiq/sensiq --version
dist/sensiq/sensiq --selftest --no-open | tail -12 || echo "WARNING: the packaged self-test failed (see above)"

[ "$SKIP_DEB" = 1 ] && { echo; echo "Done. App folder: dist/sensiq/"; exit 0; }

# 6. Debian package -------------------------------------------------------------------------
step "Building the .deb"
PKG=build/deb/sensiq_${VERSION}_${ARCH}
rm -rf "$PKG"
mkdir -p "$PKG/DEBIAN" "$PKG/opt" "$PKG/usr/bin" "$PKG/usr/share/applications" \
         "$PKG/usr/share/icons/hicolor/256x256/apps" "$PKG/usr/share/doc/sensiq"
cp -a dist/sensiq "$PKG/opt/sensiq"
cp assets/sensiq.png "$PKG/opt/sensiq/sensiq.png"
ln -s /opt/sensiq/sensiq "$PKG/usr/bin/sensiq"
install -m 644 packaging/linux/sensiq.desktop "$PKG/usr/share/applications/sensiq.desktop"
install -m 644 assets/sensiq.png "$PKG/usr/share/icons/hicolor/256x256/apps/sensiq.png"
for s in 48 128; do
  mkdir -p "$PKG/usr/share/icons/hicolor/${s}x${s}/apps"
  "$VPY" tools/make_icon.py "$PKG/usr/share/icons/hicolor/${s}x${s}/apps/sensiq.png" "$s" >/dev/null
done
cat > "$PKG/usr/share/doc/sensiq/copyright" <<'EOF'
SENSIQ - Local AI Privacy Intelligence. https://github.com/umesh-21/SENSIQ
Bundled models: bert-base-NER (Xenova/dslim, MIT), Ultra-Light-Fast-Generic-Face-Detector-1MB (MIT),
spaCy en_core_web_sm (MIT). Python packages keep their own licences (see /opt/sensiq/_internal).
EOF
install -m 755 packaging/linux/debian/postinst "$PKG/DEBIAN/postinst"
install -m 755 packaging/linux/debian/postrm "$PKG/DEBIAN/postrm"
SIZE=$(du -sk --exclude=DEBIAN "$PKG" | cut -f1)
cat > "$PKG/DEBIAN/control" <<EOF
Package: sensiq
Version: $VERSION
Architecture: $ARCH
Maintainer: Umesh Kashyap <61264950+umesh-21@users.noreply.github.com>
Installed-Size: $SIZE
Section: utils
Priority: optional
Homepage: https://github.com/umesh-21/SENSIQ
Depends: tesseract-ocr, tesseract-ocr-eng, libnotify-bin, xclip, gir1.2-ayatanaappindicator3-0.1 | gir1.2-appindicator3-0.1, pkexec | policykit-1
Recommends: tesseract-ocr-hin, wl-clipboard, at-spi2-core, zenity, gnome-shell-extension-appindicator
Description: Local AI privacy guard - discover before you disclose
 SENSIQ checks images, PDFs, Office files and ZIPs you are about to share for
 private details (Aadhaar, PAN, phone numbers, bank details, names, faces, QR
 codes, signatures) and makes a safe copy with the chosen details covered.
 Everything runs on this computer; nothing is uploaded.
 .
 Check a file from Files (right-click > Scripts > Check with SENSIQ), the tray
 menu, copied images and files, or web uploads through the browser extension.
EOF
mkdir -p dist
DEB="dist/sensiq_${VERSION}_${ARCH}.deb"
dpkg-deb --build --root-owner-group -Zxz "$PKG" "$DEB"
(cd dist && sha256sum "$(basename "$DEB")" > "$(basename "$DEB").sha256")
dpkg-deb --info "$DEB" | sed -n '1,12p'
echo
echo "Done: $DEB ($(du -h "$DEB" | cut -f1))"
echo "Install:  sudo apt install ./$DEB"
