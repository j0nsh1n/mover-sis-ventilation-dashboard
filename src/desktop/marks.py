"""Shared marks: how a rule flag's severity is written, and how missing data is drawn.

Severity never uses a color of its own. A flag is one ink with a shape, and the rule
file's word stays next to the shape. Missing data is a hatch with the cell's own text.
"""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QRect, Qt
from PySide6.QtGui import QFont, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidgetItem,
)

from src.desktop.theme import token_color

# Same shapes the flag lane in the charts draws: circle, triangle, diamond.
SEVERITY_GLYPHS = {"info": "○", "warn": "△", "critical": "◇"}

# Item-data role that marks a cell as a missing value; the cell keeps its own text.
MISSING_ROLE = int(Qt.ItemDataRole.UserRole) + 1

_HATCH_SPACING = 8  # horizontal step; about 6 px between neighbouring lines
_HATCH_WIDTH = 1.2
_CELL_PADDING = 8


def severity_glyph(severity: str) -> str:
    """The shape for a severity word, or an empty string for a word the rule file does not use."""
    return SEVERITY_GLYPHS.get(str(severity).strip().lower(), "")


def severity_label(severity: str) -> str:
    """Shape and word together, for example ``◇ critical``."""
    word = str(severity).strip()
    glyph = severity_glyph(word)
    return f"{glyph} {word}" if glyph else word


def mark_missing(item: QTableWidgetItem, missing: bool = True) -> None:
    """Tell ``MissingCellDelegate`` to hatch this cell."""
    item.setData(MISSING_ROLE, bool(missing))


class MissingCellDelegate(QStyledItemDelegate):
    """Draws cells marked with ``mark_missing`` on a diagonal hatch and keeps their text."""

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        if not index.data(MISSING_ROLE):
            super().paint(painter, option, index)
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text = opt.text
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        text_rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, opt, opt.widget)
        # Row background, selection and row line come from the app's table style; only the text is held back.
        opt.text = ""
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        painter.save()
        self._draw_hatch(painter, opt.rect)
        font = QFont(opt.font)
        font.setItalic(True)
        painter.setFont(font)
        painter.setPen(token_color("ink_3"))
        painter.drawText(
            text_rect.adjusted(_CELL_PADDING // 2, 0, -_CELL_PADDING // 2, 0),
            int(opt.displayAlignment) | int(Qt.AlignmentFlag.AlignVCenter),
            painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, text_rect.width() - _CELL_PADDING),
        )
        painter.restore()

    @staticmethod
    def _draw_hatch(painter: QPainter, rect: QRect) -> None:
        painter.setClipRect(rect)
        pen = painter.pen()
        pen.setColor(token_color("hatch"))
        pen.setWidthF(_HATCH_WIDTH)
        painter.setPen(pen)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        height = rect.height()
        # Lines run from bottom-left to top-right; stepping the start across the width and the height covers the cell.
        for offset in range(-height, rect.width() + height, _HATCH_SPACING):
            x = rect.left() + offset
            painter.drawLine(x, rect.bottom() + 1, x + height + 1, rect.top())
