"""python -m src.desktop"""

from __future__ import annotations

import os
import sys


def _bootstrap() -> int:
    if os.environ.get("XDG_SESSION_TYPE") == "wayland" and not os.environ.get(
        "QT_QPA_PLATFORM"
    ):
        os.environ["QT_QPA_PLATFORM"] = "xcb"
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
    os.environ.setdefault(
        "QTWEBENGINE_CHROMIUM_FLAGS",
        "--no-sandbox --disable-gpu-sandbox --disable-seccomp-filter-sandbox",
    )

    # QtWebEngine requires this before QApplication on many platforms
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

    from src.desktop.app import main

    return main()


if __name__ == "__main__":
    raise SystemExit(_bootstrap())
