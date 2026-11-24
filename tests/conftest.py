"""Shared test setup. On Linux the tests' "arial.ttf" is Liberation Sans (same letter widths)
or DejaVu Sans; Windows uses the real Arial, so nothing changes there."""
import os
import sys

if sys.platform != "win32":
    from PIL import ImageFont

    _FALLBACKS = {
        "arial.ttf": ["/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
                      "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
                      "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"],
    }
    _truetype = ImageFont.truetype

    def _truetype_with_fallback(font=None, size=10, *args, **kwargs):
        try:
            return _truetype(font, size, *args, **kwargs)
        except OSError:
            name = str(font).replace("\\", "/").rsplit("/", 1)[-1].lower()   # also C:\\Windows\\Fonts\\arial.ttf
            for path in _FALLBACKS.get(name, []):
                if os.path.exists(path):
                    return _truetype(path, size, *args, **kwargs)
            raise

    ImageFont.truetype = _truetype_with_fallback
