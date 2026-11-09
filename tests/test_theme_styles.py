"""Base stylesheet: button kinds, quiet tables, color-only hover and press, public tokens."""

from __future__ import annotations

import os
import re

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget  # noqa: E402

from src.desktop import theme  # noqa: E402
from src.desktop.theme import tokens  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _rgb(image, x: int, y: int) -> str:
    return QColor(image.pixel(x, y)).name().upper()


def _shown(widget: QWidget, qapp: QApplication) -> None:
    widget.show()
    qapp.processEvents()


def test_tokens_are_the_active_theme_and_a_copy(qapp, applied_theme):
    name = applied_theme
    palette = tokens()
    assert palette == tokens(name)
    assert theme.active_theme() == name
    palette["paper"] = "#000000"
    assert tokens()["paper"] != "#000000"
    assert qapp.palette().color(QPalette.ColorRole.Window).name().upper() == tokens()["paper"]
    assert theme.token_color("hatch").alpha() in (round(0.30 * 255), round(0.24 * 255))


def test_light_and_dark_use_the_designs_signal_and_practice_colors():
    assert tokens("light")["s_pip"] == "#B0502A" and tokens("dark")["s_pip"] == "#EE8A5E"
    assert tokens("light")["practice_bar"] == "#A27C12" and tokens("dark")["practice_bar"] == "#8A7429"
    assert tokens("light")["sel"] == "#E2E5F4" and tokens("dark")["sel"] == "#252B48"


def test_buttons_are_outlined_filled_or_text(qapp, applied_theme):
    name = applied_theme
    colors = tokens(name)
    host = QWidget()
    layout = QVBoxLayout(host)
    default, primary, text = QPushButton("Cancel"), QPushButton("Trace"), QPushButton("New question")
    primary.setObjectName("primaryBtn")
    text.setObjectName("textBtn")
    for button in (default, primary, text):
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # the ring has its own test; this one reads resting borders
        layout.addWidget(button)
    _shown(host, qapp)
    try:
        image = host.grab().toImage()

        def pixel(button: QPushButton, x: int, y: int | None = None) -> str:
            origin = button.geometry().topLeft()
            return _rgb(image, origin.x() + x, origin.y() + (button.height() // 2 if y is None else y))

        assert pixel(default, 0) == colors["rule_strong"].upper()
        assert pixel(default, 4) == colors["paper"].upper(), "the default button has no fill"
        assert pixel(primary, 4) == colors["accent"].upper()
        assert pixel(primary, 0) == colors["accent"].upper()
        assert pixel(text, 0) == colors["paper"].upper(), "the text button has no border"
        assert pixel(text, 4) == colors["paper"].upper()
        t = image.copy(text.geometry())
        accent = QColor(colors["accent"]).rgb()
        assert any(t.pixel(x, y) | 0xFF000000 == accent for x in range(t.width()) for y in range(t.height()))
    finally:
        host.close()
        host.deleteLater()


def test_tables_have_one_header_line_faint_row_lines_and_a_selected_wash(qapp, applied_theme):
    name = applied_theme
    colors = tokens(name)
    table = QTableWidget(3, 2)
    table.setHorizontalHeaderLabels(["Minute", "Value"])
    table.verticalHeader().hide()
    for row in range(3):
        for column in range(2):
            table.setItem(row, column, QTableWidgetItem(f"{row}{column}"))
    table.resize(320, 200)
    table.selectRow(1)
    _shown(table, qapp)
    try:
        view = table.viewport().grab().toImage()
        faint = colors["rule_faint"].upper()
        rows_with_line = {y for y in range(view.height()) if _rgb(view, 3, y) == faint}
        assert len(rows_with_line) == 3, "one faint line under each of the three rows"
        first = table.rowViewportPosition(0)
        middle = first + table.rowHeight(0) // 2
        edge = table.columnViewportPosition(1)
        assert {_rgb(view, x, middle) for x in (edge - 2, edge - 1, edge)} == {colors["paper"].upper()}, "no column lines"
        assert _rgb(view, 3, table.rowViewportPosition(1) + 2) == colors["sel"].upper()
        header = table.horizontalHeader().grab().toImage()
        lines = {y for y in range(header.height()) if _rgb(header, 3, y) == colors["rule"].upper()}
        assert lines == {header.height() - 1}, "exactly one line, under the header"
    finally:
        table.close()
        table.deleteLater()


def test_hover_and_press_change_color_only():
    properties_allowed = {"background", "color", "border-color"}
    for selector, body in re.findall(r"([^{}]+)\{([^}]*)\}", theme.LIGHT_STYLESHEET):
        if not re.search(r":(hover|pressed)\b", selector):
            continue
        names = {line.split(":", 1)[0].strip() for line in body.strip().split(";") if line.strip()}
        assert names <= properties_allowed, (selector.strip(), names - properties_allowed)
