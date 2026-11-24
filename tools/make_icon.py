"""Build-time: writes assets/icon.ico for the .exe and installer, or a PNG for Linux
(python tools/make_icon.py assets/sensiq.png [size])."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sensiq.icon import make_icon_image, save_ico  # noqa: E402

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "assets/icon.ico"
    if target.lower().endswith(".png"):
        size = int(sys.argv[2]) if len(sys.argv) > 2 else 256
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        make_icon_image(size).save(target)
    else:
        save_ico(target)
    print(f"Icon written to {target}")
