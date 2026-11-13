"""Suite title. The page name rides in the window title because screen readers read the title."""

from __future__ import annotations

from PySide6.QtWidgets import QMainWindow

_PRODUCT_NAME = "MOVER Suite"


def install_suite_chrome(window: QMainWindow, version: str) -> None:
    """Set the suite title."""
    title = f"{_PRODUCT_NAME} v{version}"
    window.setProperty("suiteTitle", title)
    window.setWindowTitle(title)


def set_page_title(window: QMainWindow, page: str | None) -> None:
    """Put the open page in the window title."""
    base = window.property("suiteTitle") or window.windowTitle()
    window.setWindowTitle(f"{page} — {base}" if page else base)
