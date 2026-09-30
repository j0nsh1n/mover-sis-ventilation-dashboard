"""PyInstaller entry point for the desktop app."""

from __future__ import annotations

import os


def main() -> int:
    # Must set platform env BEFORE importing Qt
    if os.environ.get("XDG_SESSION_TYPE") == "wayland" and not os.environ.get(
        "QT_QPA_PLATFORM"
    ):
        os.environ["QT_QPA_PLATFORM"] = "xcb"

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

    from src.desktop.app import main as desktop_main

    return desktop_main()


if __name__ == "__main__":
    # First thing in the frozen app: the full-EMR scan starts worker processes
    # that re-launch this executable, and freeze_support() lets those children
    # run their job instead of opening a second window.
    import multiprocessing

    multiprocessing.freeze_support()
    raise SystemExit(main())
