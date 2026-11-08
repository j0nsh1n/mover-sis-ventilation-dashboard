"""Suite title and the single Monitoring label on the menu bar."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QMainWindow

_PRODUCT_NAME = "MOVER Suite"
_MODULE_LABEL = "Monitoring"


def install_suite_chrome(window: QMainWindow, version: str) -> None:
    """Set the suite title and the Monitoring label."""
    title = f"{_PRODUCT_NAME} v{version}"
    window.setProperty("suiteTitle", title)
    window.setWindowTitle(title)
    set_module_label(window, _MODULE_LABEL)


def set_page_title(window: QMainWindow, page: str | None) -> None:
    """Put the open practice page in the window title. Screen readers read the title, not the corner label."""
    base = window.property("suiteTitle") or window.windowTitle()
    window.setWindowTitle(f"{page} — {base}" if page else base)


def set_module_label(window: QMainWindow, label_text: str) -> None:
    """Show which suite module is open. The label is not a control."""
    bar = window.menuBar()
    current = bar.cornerWidget(Qt.Corner.TopLeftCorner)
    label = current if isinstance(current, QLabel) else QLabel(bar)
    label.setText(label_text)
    label.setAccessibleName(label_text)
    label.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
    if current is not label:
        bar.setCornerWidget(label, Qt.Corner.TopLeftCorner)
