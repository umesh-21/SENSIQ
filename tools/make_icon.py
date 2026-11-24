"""Build-time: writes assets/icon.ico for the .exe and installer."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sensiq.icon import save_ico  # noqa: E402

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "assets/icon.ico"
    save_ico(target)
    print(f"Icon written to {target}")
