<#
.SYNOPSIS
  Builds SENSIQ into dist\SENSIQ\SENSIQ.exe and an installer
  (dist\installer\SENSIQ-Setup-1.7.2.exe).

.DESCRIPTION
  Run once on a Windows 10/11 x64 machine that has internet access.
  The internet is used only here, to install Python packages and download the
  name-detection model. The resulting installer contains everything, and the
  installed app works with no internet connection.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File build.ps1
  powershell -ExecutionPolicy Bypass -File build.ps1 -Model "Xenova/bert-base-NER" -SkipInstaller
#>
param(
    [string]$Model = "Xenova/bert-base-NER",
    [string]$Python = "",
    [switch]$SkipInstaller,
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Check($what) { if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE)" } }

# 1. Python ---------------------------------------------------------------------------
Step "Checking for 64-bit Python 3.10 - 3.12"
$pyExe = $null; $pyArgs = @()
if ($Python) { $pyExe = $Python }
elseif (Get-Command py -ErrorAction SilentlyContinue) {
    # the launcher's plain "-3" picks the newest Python, which may be too new for the packages
    foreach ($v in @("3.11", "3.12", "3.10")) {
        & py "-$v" -c "import sys" 2>$null
        if ($LASTEXITCODE -eq 0) { $pyExe = "py"; $pyArgs = @("-$v"); break }
    }
}
if (-not $pyExe -and (Get-Command python -ErrorAction SilentlyContinue)) { $pyExe = "python" }
if (-not $pyExe) { throw "Python not found. Install Python 3.11 (64-bit) from python.org and tick 'Add to PATH'." }
& $pyExe @pyArgs -c "import sys,struct; v=sys.version_info; assert (3,10)<=v[:2]<=(3,12), 'Use Python 3.10-3.12, found %d.%d' % v[:2]; assert struct.calcsize('P')==8, 'Use 64-bit Python'; print(sys.version)"
Check "Python check"

# 2. Virtual environment and packages ---------------------------------------------------
Step "Creating virtual environment (.venv) and installing packages"
if (-not (Test-Path ".venv\Scripts\python.exe")) { & $pyExe @pyArgs -m venv .venv; Check "venv" }
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
& $py -m pip install --upgrade pip --quiet; Check "pip upgrade"
& $py -m pip install -r requirements.txt -r requirements-build.txt --quiet; Check "pip install"

# 3. Model (the only download the product ever needs) -------------------------------------
Step "Downloading the name-detection model ($Model)"
& $py tools\download_model.py --repo $Model --out models\ner; Check "model download"

Step "Downloading the face detector (Ultra-Light-Fast-Generic-Face-Detector-1MB, MIT)"
$face = "models\face\face.onnx"
$faceSha = "8F4C659275977E7A3BFBFA339A9C769AD793DF50F9C0BAA8C14B11BAA1646430"
if (-not (Test-Path $face) -or (Get-FileHash $face -Algorithm SHA256).Hash -ne $faceSha) {
    New-Item -ItemType Directory -Force "models\face" | Out-Null
    Invoke-WebRequest "https://github.com/Linzaer/Ultra-Light-Fast-Generic-Face-Detector-1MB/raw/master/models/onnx/version-RFB-640.onnx" -OutFile $face -UseBasicParsing
    if ((Get-FileHash $face -Algorithm SHA256).Hash -ne $faceSha) { throw "face model download doesn't match the expected checksum" }
}

Step "Installing the small spaCy model used by Presidio (en_core_web_sm)"
& $py -c "import spacy, sys; sys.exit(0 if spacy.util.is_package('en_core_web_sm') else 1)"
if ($LASTEXITCODE -ne 0) { & $py -m spacy download en_core_web_sm; Check "spaCy model download" }

Step "Checking Windows OCR (used for images and scanned PDFs)"
& $py -c "from sensiq import ocr; s = ocr.status(); print(s); import sys; sys.exit(0 if s['ocr_loaded'] else 1)"
if ($LASTEXITCODE -ne 0) { Write-Warning "Windows OCR isn't available on this build PC. Images will be skipped where OCR is missing." }

# 4. Icon and COM wrappers ----------------------------------------------------------------
Step "Creating icon"
& $py tools\make_icon.py assets\icon.ico; Check "icon"

Step "Generating UI Automation COM wrappers"
& $py -c "import uiautomation as auto; print('UI Automation OK:', auto.GetRootControl().Name)"; Check "UI Automation"

# 5. Tests ---------------------------------------------------------------------------------
if (-not $SkipTests) {
    Step "Running unit tests"
    & $py -m pytest -q tests; Check "unit tests"
    Step "Running end-to-end detector self-test (with the real model)"
    & $py -m sensiq --selftest --no-open; Check "self-test"
}

# 6. Package ------------------------------------------------------------------------------
Step "Packaging with PyInstaller"
& $py -m PyInstaller --noconfirm --clean --windowed --name SENSIQ `
    --icon assets\icon.ico `
    --add-data "models;models" `
    --add-data "assets;assets" `
    --collect-all uiautomation `
    --collect-submodules comtypes `
    --collect-binaries onnxruntime `
    --hidden-import pystray._win32 `
    --collect-all presidio_analyzer `
    --collect-all spacy `
    --collect-all thinc `
    --collect-all en_core_web_sm `
    --collect-data phonenumbers `
    --collect-submodules phonenumbers `
    --collect-all pypdfium2 `
    --collect-all pypdfium2_raw `
    --collect-all winrt `
    --collect-all zxingcpp `
    --collect-all pillow_heif `
    --hidden-import olefile `
    --add-data "browser_extension;browser_extension" `
    --exclude-module torch --exclude-module transformers --exclude-module stanza `
    --version-file version_info.txt `
    run_sensiq.py
Check "PyInstaller"

# Make sure the Visual C++ runtime that ONNX Runtime needs travels with the app,
# so it starts on PCs that never installed the VC++ redistributable.
$internal = "dist\SENSIQ\_internal"
foreach ($dll in @("msvcp140.dll", "msvcp140_1.dll", "vcruntime140.dll", "vcruntime140_1.dll")) {
    $src = Join-Path $env:WINDIR "System32\$dll"
    if ((Test-Path $src) -and -not (Test-Path (Join-Path $internal $dll))) { Copy-Item $src $internal }
}

Step "Checking the packaged app"
Start-Process -FilePath "dist\SENSIQ\SENSIQ.exe" -ArgumentList "--selftest", "--no-open" -Wait
$report = Join-Path $env:APPDATA "SENSIQ\selftest.txt"
if (Test-Path $report) { Get-Content $report | Select-Object -Last 4 }

# 7. Installer -----------------------------------------------------------------------------
if ($SkipInstaller) { Write-Host "`nDone. Portable app: dist\SENSIQ\SENSIQ.exe" -ForegroundColor Green; exit 0 }

Step "Building installer"
$iscc = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1

if ($iscc) {
    & $iscc installer.iss; Check "Inno Setup"
    Write-Host "`nDone. Installer: dist\installer\SENSIQ-Setup-1.7.2.exe" -ForegroundColor Green
} else {
    Write-Warning "Inno Setup 6 not found (free from jrsoftware.org). Making a portable zip instead."
    Copy-Item demo "dist\SENSIQ\demo" -Recurse -Force
    Compress-Archive -Path "dist\SENSIQ\*" -DestinationPath "dist\SENSIQ-portable.zip" -Force
    Write-Host "`nDone. Portable zip: dist\SENSIQ-portable.zip" -ForegroundColor Green
}
