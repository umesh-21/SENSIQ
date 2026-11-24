"""Draws the app icon: a shield with two redaction bars. No image files needed."""
from __future__ import annotations

from pathlib import Path

ACTIVE = (14, 124, 123, 255)   # teal
PAUSED = (138, 148, 166, 255)  # slate grey


def make_icon_image(size: int = 64, active: bool = True):
    from PIL import Image, ImageDraw

    s = size * 4  # draw large, then downsample for smooth edges
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    shield = [(0.50, 0.05), (0.90, 0.18), (0.87, 0.56), (0.50, 0.95), (0.13, 0.56), (0.10, 0.18)]
    d.polygon([(x * s, y * s) for x, y in shield], fill=ACTIVE if active else PAUSED)
    r = int(s * 0.035)
    d.rounded_rectangle([0.28 * s, 0.36 * s, 0.72 * s, 0.47 * s], radius=r, fill=(255, 255, 255, 255))
    d.rounded_rectangle([0.28 * s, 0.55 * s, 0.58 * s, 0.66 * s], radius=r, fill=(255, 255, 255, 190))
    return img.resize((size, size), Image.LANCZOS)


def save_ico(path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    make_icon_image(256).save(path, format="ICO",
                              sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
