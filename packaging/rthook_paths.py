"""Runtime hook: ensure frozen app can import ``src`` and find bundled config."""

from __future__ import annotations

import sys
from pathlib import Path


def _setup() -> None:
    if not getattr(sys, "frozen", False):
        return
    meipass = Path(getattr(sys, "_MEIPASS", ""))
    if meipass.is_dir() and str(meipass) not in sys.path:
        sys.path.insert(0, str(meipass))
    # Also allow loading from executable directory (user data, plugins)
    exe_dir = Path(sys.executable).resolve().parent
    if str(exe_dir) not in sys.path:
        sys.path.append(str(exe_dir))


_setup()
