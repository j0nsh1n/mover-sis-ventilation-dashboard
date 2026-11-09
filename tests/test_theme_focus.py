"""Every focusable control shows a 2 px accent ring (ink on the filled primary button), in both themes."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QComboBox,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.desktop.theme import tokens  # noqa: E402

MIN_RING_PIXELS = 40
# Controls whose ring is the widget's own outer border, so its width can be read off the left edge.
RING_AT_EDGE = {"default button", "primary button", "text button", "line edit", "combo box", "spin box",
                "text edit", "plain text edit", "table", "list"}


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _button(name: str = ""):
    button = QPushButton("Trace the evidence")
    if name:
        button.setObjectName(name)
    return button, button


def _checkbox():
    box = QCheckBox("Show tracks")
    return box, box


def _combo():
    combo = QComboBox()
    combo.addItems(["Default rules", "Strict rules"])
    return combo, combo


def _tabs():
    tabs = QTabWidget()
    tabs.addTab(QWidget(), "Cases")
    tabs.addTab(QWidget(), "Articles")
    return tabs, tabs.tabBar()


def _table():
    table = QTableWidget(2, 2)
    return table, table


def _list():
    widget = QListWidget()
    widget.addItems(["one", "two"])
    return widget, widget


CONTROLS = {
    "default button": (lambda: _button(), "accent"),
    "primary button": (lambda: _button("primaryBtn"), "ink"),
    "text button": (lambda: _button("textBtn"), "accent"),
    "line edit": (lambda: (lambda w: (w, w))(QLineEdit("text")), "accent"),
    "combo box": (_combo, "accent"),
    "spin box": (lambda: (lambda w: (w, w))(QSpinBox()), "accent"),
    "check box": (_checkbox, "accent"),
    "text edit": (lambda: (lambda w: (w, w))(QTextEdit("text")), "accent"),
    "plain text edit": (lambda: (lambda w: (w, w))(QPlainTextEdit("text")), "accent"),
    "tab": (_tabs, "accent"),
    "table": (_table, "accent"),
    "list": (_list, "accent"),
}


def _count(image: QImage, color: QColor) -> int:
    target = color.rgb()
    return sum(1 for y in range(image.height()) for x in range(image.width()) if image.pixel(x, y) | 0xFF000000 == target)


@pytest.mark.parametrize("name", CONTROLS)
def test_focused_control_draws_a_two_pixel_ring(qapp, applied_theme, name):
    theme = applied_theme
    build, ring_token = CONTROLS[name]
    host = QWidget()
    layout = QVBoxLayout(host)
    shown, focus_target = build()
    elsewhere = QLineEdit()
    layout.addWidget(elsewhere)
    layout.addWidget(shown)
    host.resize(360, 240)
    host.show()
    host.activateWindow()
    qapp.processEvents()
    try:
        elsewhere.setFocus()
        qapp.processEvents()
        before = shown.grab().toImage()
        focus_target.setFocus(Qt.FocusReason.TabFocusReason)
        qapp.processEvents()
        assert focus_target.hasFocus(), name
        after = shown.grab().toImage()
        ring = QColor(tokens(theme)[ring_token])
        assert _count(after, ring) - _count(before, ring) >= MIN_RING_PIXELS, (theme, name)
        if name in RING_AT_EDGE:
            middle = after.height() // 2
            ring_rgb = ring.rgb()
            assert [after.pixel(x, middle) | 0xFF000000 == ring_rgb for x in range(4)] == [True, True, False, False], (
                theme, name, "the ring must be exactly 2 px wide")
    finally:
        host.close()
        host.deleteLater()
        qapp.processEvents()
