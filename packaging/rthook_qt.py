"""Prefer bundled PySide6 Qt libraries and plugins at runtime."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _setup_qt_env() -> None:
    if not getattr(sys, "frozen", False):
        return
    meipass = Path(getattr(sys, "_MEIPASS", ""))
    if not meipass.is_dir():
        return

    qt_root = meipass / "PySide6" / "Qt"
    qt_lib = qt_root / "lib"
    qt_plugins = qt_root / "plugins"
    qt_libexec = qt_root / "libexec"

    lib_dirs = [
        str(meipass),
        str(meipass / "shiboken6"),
        str(meipass / "PySide6"),
        str(meipass / "numpy.libs"),
        str(meipass / "scipy.libs"),
        str(meipass / "pandas.libs"),
    ]
    if qt_lib.is_dir():
        lib_dirs.insert(0, str(qt_lib))

    prev = os.environ.get("LD_LIBRARY_PATH", "")
    os.environ["LD_LIBRARY_PATH"] = os.pathsep.join(
        [d for d in lib_dirs if Path(d).is_dir()] + ([prev] if prev else [])
    )

    if qt_plugins.is_dir():
        os.environ.setdefault("QT_PLUGIN_PATH", str(qt_plugins))
        os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", str(qt_plugins / "platforms"))

    webengine = qt_libexec / "QtWebEngineProcess"
    if webengine.is_file():
        os.environ.setdefault("QTWEBENGINEPROCESS_PATH", str(webengine))
        os.environ.setdefault(
            "QTWEBENGINE_RESOURCES_PATH",
            str(qt_root / "resources"),
        )
        os.environ.setdefault(
            "QTWEBENGINE_LOCALES_PATH",
            str(qt_root / "translations" / "qtwebengine_locales"),
        )


_setup_qt_env()
