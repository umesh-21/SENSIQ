# PyInstaller spec for the Linux app folder (dist/sensiq/), used by build_linux.sh.
# Run from the repository root: python -m PyInstaller --noconfirm --clean packaging/linux/sensiq.spec
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = SPECPATH + "/../.."   # noqa: F821 (set by PyInstaller)

datas = [(f"{ROOT}/models", "models"), (f"{ROOT}/assets", "assets"),
         (f"{ROOT}/browser_extension", "browser_extension")]
binaries = collect_dynamic_libs("onnxruntime")
hiddenimports = ["olefile", "pystray._appindicator", "pystray._gtk", "pystray._xorg",
                 "gi.repository.AyatanaAppIndicator3", "gi.repository.Gtk", "gi.repository.Atspi"]
for pkg in ("presidio_analyzer", "spacy", "thinc", "en_core_web_sm", "pypdfium2", "pypdfium2_raw",
            "zxingcpp", "pillow_heif"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
datas += collect_data_files("phonenumbers")
hiddenimports += collect_submodules("phonenumbers")

EXCLUDES = [
    "torch", "transformers", "stanza", "uiautomation", "comtypes", "winrt",
    # optional imports of the packages above that SENSIQ never uses
    "matplotlib", "pandas", "scipy", "IPython", "notebook", "PyQt5", "PyQt6", "PySide2", "PySide6",
    "tensorflow", "cv2", "sklearn", "h5py", "soundfile", "hf_xet", "ml_dtypes", "librosa", "jax",
]

a = Analysis(  # noqa: F821
    [f"{ROOT}/run_sensiq.py"],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[f"{ROOT}/packaging/linux/hooks"],
    # GTK is only used for the tray menu (drawn by the desktop): no icon themes or GTK themes
    hooksconfig={"gi": {"icons": ["Adwaita"], "themes": ["Adwaita"], "languages": ["en_GB", "hi"],
                        "module-versions": {"Gtk": "3.0"}}},
    excludes=EXCLUDES,
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz, a.scripts, [], exclude_binaries=True, name="sensiq", console=True, strip=False, upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="sensiq")  # noqa: F821
