"""Suite title and the single Monitoring label on the menu bar."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QMainWindow

_PRODUCT_NAME = "MOVER Suite"
_MONITOR_TITLE = "MOVER SIS Monitor v{version} — Ventilation & Anesthesia"
_MODULE_LABEL = "Monitoring"


def install_suite_chrome(window: QMainWindow, version: str) -> None:
    """Set the suite title and one non-interactive Monitoring label."""
    window.setWindowTitle(f"{_PRODUCT_NAME} — {_MONITOR_TITLE.format(version=version)}")
    bar = window.menuBar()
    current = bar.cornerWidget(Qt.Corner.TopLeftCorner)
    label = current if isinstance(current, QLabel) else QLabel(bar)
    label.setText(_MODULE_LABEL)
    label.setAccessibleName(_MODULE_LABEL)
    label.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
    if current is not label:
        bar.setCornerWidget(label, Qt.Corner.TopLeftCorner)
