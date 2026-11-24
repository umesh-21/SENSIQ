"""Kept for older imports: the Win32 helpers now live in sensiq/platform/windows.py.
Every function there is a harmless no-op on other systems."""
from .platform.windows import *  # noqa: F401,F403
from .platform.windows import IS_WIN  # noqa: F401
