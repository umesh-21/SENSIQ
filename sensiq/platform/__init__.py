"""Platform layer: the few things SENSIQ does differently on Windows and Linux (paths are in
sensiq/paths.py). Import it as `from . import platform as plat`; every module offers the
same names (clipboard, start-up, opening files, notifications, single instance...)."""
import sys

if sys.platform.startswith("linux"):
    from .linux import *  # noqa: F401,F403
    from . import linux as impl
else:
    from .windows import *  # noqa: F401,F403
    from . import windows as impl

IS_WIN = impl.IS_WIN
IS_LINUX = impl.IS_LINUX
NAME = impl.NAME
