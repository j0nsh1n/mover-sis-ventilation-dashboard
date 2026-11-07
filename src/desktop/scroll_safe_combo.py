"""A combo box that lets the page keep scrolling when the wheel passes over it."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QComboBox, QWidget


class ScrollSafeComboBox(QComboBox):
    """Changes on the wheel only once focused, so scrolling a form past it keeps the choice."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Tab or a click focuses it; the wheel alone does not (Qt's default is WheelFocus).
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()
