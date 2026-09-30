"""PyInstaller entry point for the desktop app."""

from __future__ import annotations

import os


def main() -> int:
    # Must set platform env BEFORE importing Qt / WebEngine
    if os.environ.get("XDG_SESSION_TYPE") == "wayland" and not os.environ.get(
        "QT_QPA_PLATFORM"
    ):
        os.environ["QT_QPA_PLATFORM"] = "xcb"
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
    os.environ.setdefault(
        "QTWEBENGINE_CHROMIUM_FLAGS",
        "--no-sandbox --disable-gpu-sandbox --disable-seccomp-filter-sandbox",
    )

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

    from src.desktop.app import main as desktop_main

    return desktop_main()


if __name__ == "__main__":
    raise SystemExit(main())
