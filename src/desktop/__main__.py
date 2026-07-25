"""python -m src.desktop"""

from __future__ import annotations

import sys


def _bootstrap() -> int:
    # QtWebEngine requires this before QApplication on many platforms
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

    from src.desktop.app import main

    return main()


if __name__ == "__main__":
    raise SystemExit(_bootstrap())
