"""Pieces of the case page (F): the page that reflows by width, the episode row, and the trace swatch."""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QPointF, QRect, QSize, Qt
from PySide6.QtGui import QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from src.desktop.fonts import tabular
from src.desktop.theme import token_color

# Page width from which the inspector sits beside the chart; below it the inspector goes under the chart.
SIDE_BY_SIDE_WIDTH = 1000
INSPECTOR_WIDTH = 368
# Below this the selected minute drops under the title instead of sitting at its right.
HEADER_ROW_WIDTH = 700
EPISODE_ROW_HEIGHT = 32
SWATCH_SIZE = (14, 8)

# Item data the episode delegate paints from; the item's own text is the same row as one sentence.
GLYPH_ROLE = int(Qt.ItemDataRole.UserRole) + 1
NAME_ROLE = int(Qt.ItemDataRole.UserRole) + 2
SEVERITY_ROLE = int(Qt.ItemDataRole.UserRole) + 3
RANGE_ROLE = int(Qt.ItemDataRole.UserRole) + 4

_GLYPH_WIDTH = 24
_ROW_PADDING = 8
_SELECTED_BAR = 3


class SignalPage(QWidget):
    """The case page: a back row and header above a chart column and the inspector.

    The inspector sits at the right edge, full height, when the page is wide enough; otherwise it goes under
    the chart, so the page never scrolls sideways beside the open Rules drawer or at the window's least width.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("signalPage")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.top = QVBoxLayout()
        self.top.setContentsMargins(32, 16, 32, 14)
        self.top.setSpacing(2)
        root.addLayout(self.top)
        self.header = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.header.setSpacing(24)
        self.top.addLayout(self.header)
        self.body = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.body.setSpacing(0)
        root.addLayout(self.body, 1)
        self.chart_column = QVBoxLayout()
        self.chart_column.setSpacing(14)
        self.chart_holder = QWidget()
        self.chart_holder.setLayout(self.chart_column)
        self.body.addWidget(self.chart_holder, 1)
        self.inspector = QFrame()
        self.inspector.setObjectName("caseInspector")
        self.inspector_column = QVBoxLayout(self.inspector)
        self.inspector_column.setContentsMargins(24, 20, 24, 28)
        self.inspector_column.setSpacing(26)
        self.body.addWidget(self.inspector, 0)
        self._wide: bool | None = None
        self._header_row: bool | None = None
        self._stamp: tuple[QLabel, QHBoxLayout] | None = None

    def set_stamp(self, caption: QLabel, reading: QHBoxLayout) -> None:
        """The selected-minute block: it hangs from the right edge beside the title and from the left under it."""
        self._stamp = (caption, reading)
        reading.insertStretch(0, 0)
        self._header_row = None
        self.reflow()

    def is_wide(self) -> bool:
        return bool(self._wide)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.reflow()

    def reflow(self) -> None:
        wide = self.width() >= SIDE_BY_SIDE_WIDTH
        if wide != self._wide:
            self._wide = wide
            self.body.setDirection(QBoxLayout.Direction.LeftToRight if wide else QBoxLayout.Direction.TopToBottom)
            if wide:
                self.chart_column.setContentsMargins(32, 0, 24, 28)
                self.inspector.setFixedWidth(INSPECTOR_WIDTH)
            else:
                self.chart_column.setContentsMargins(32, 0, 32, 20)
                self.inspector.setMinimumWidth(0)
                self.inspector.setMaximumWidth(16777215)
            self.inspector.setProperty("stacked", not wide)
            self.inspector.style().unpolish(self.inspector)
            self.inspector.style().polish(self.inspector)
        header_row = self.width() >= HEADER_ROW_WIDTH
        if header_row != self._header_row:
            self._header_row = header_row
            self.header.setDirection(QBoxLayout.Direction.LeftToRight if header_row else QBoxLayout.Direction.TopToBottom)
            if self._stamp is not None:
                caption, reading = self._stamp
                caption.setAlignment((Qt.AlignmentFlag.AlignRight if header_row else Qt.AlignmentFlag.AlignLeft)
                                     | Qt.AlignmentFlag.AlignVCenter)
                reading.setStretch(0, 1 if header_row else 0)


class EpisodeList(QListWidget):
    """The rule episodes. Focus alone selects nothing; the first key that moves or picks selects the first episode.

    Qt makes row 0 current when the list gets keyboard focus with no current row, and the page reads the
    selection, so a person tabbing through the page must not change what the page shows.
    """

    def keyPressEvent(self, event) -> None:  # noqa: N802
        # Home and Page Up would leave row 0 current, and Qt then selects nothing; they mean the first row too.
        picks = (Qt.Key.Key_Down, Qt.Key.Key_Up, Qt.Key.Key_Home, Qt.Key.Key_PageUp, Qt.Key.Key_Return,
                 Qt.Key.Key_Enter, Qt.Key.Key_Space)
        first = self.item(0)
        # The number pad's Enter and arrows carry the keypad flag; it does not change what they mean.
        modifiers = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        if (event.key() in picks and modifiers == Qt.KeyboardModifier.NoModifier and not self.selectedItems()
                and first is not None and first.flags() & Qt.ItemFlag.ItemIsSelectable):
            self.setCurrentRow(0)
            event.accept()
            return
        super().keyPressEvent(event)


class EpisodeDelegate(QStyledItemDelegate):
    """One rule episode as the design writes it: shape, rule name, severity word, and its minutes at the right.

    The selected row gets the selection fill and a short accent bar at its left edge.
    """

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> QSize:  # noqa: N802
        return QSize(option.rect.width(), EPISODE_ROW_HEIGHT)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text = opt.text
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        opt.text = ""
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        rect = opt.rect
        painter.save()
        name = index.data(NAME_ROLE)
        if name is None:
            # A row that is not an episode, such as "No flags in this case", is plain text.
            painter.setFont(opt.font)
            painter.setPen(token_color("ink_2"))
            painter.drawText(rect.adjusted(_ROW_PADDING, 0, -_ROW_PADDING, 0),
                             int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), text)
            painter.restore()
            return
        if opt.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(QRect(rect.left(), rect.top(), _SELECTED_BAR, rect.height()), token_color("accent"))
        vertical = int(Qt.AlignmentFlag.AlignVCenter)
        painter.setFont(opt.font)
        painter.setPen(token_color("ink"))
        painter.drawText(QRect(rect.left() + _ROW_PADDING + 2, rect.top(), _GLYPH_WIDTH, rect.height()),
                         vertical | int(Qt.AlignmentFlag.AlignLeft), str(index.data(GLYPH_ROLE) or ""))
        quiet = QFont(opt.font)
        quiet.setPixelSize(12)
        quiet = tabular(quiet)
        painter.setFont(quiet)
        minutes = str(index.data(RANGE_ROLE) or "")
        minutes_width = painter.fontMetrics().horizontalAdvance(minutes)
        painter.setPen(token_color("ink_3"))
        painter.drawText(QRect(rect.right() - _ROW_PADDING - minutes_width, rect.top(), minutes_width, rect.height()),
                         vertical | int(Qt.AlignmentFlag.AlignRight), minutes)
        severity = str(index.data(SEVERITY_ROLE) or "")
        severity_width = painter.fontMetrics().horizontalAdvance(severity)
        name_left = rect.left() + _ROW_PADDING + _GLYPH_WIDTH + 6
        room = rect.right() - _ROW_PADDING - minutes_width - 10 - name_left - severity_width - 6
        painter.setFont(opt.font)
        shown = painter.fontMetrics().elidedText(str(name), Qt.TextElideMode.ElideRight, max(room, 24))
        shown_width = painter.fontMetrics().horizontalAdvance(shown)
        painter.setPen(token_color("ink"))
        painter.drawText(QRect(name_left, rect.top(), shown_width + 2, rect.height()),
                         vertical | int(Qt.AlignmentFlag.AlignLeft), shown)
        painter.setFont(quiet)
        painter.setPen(token_color("ink_3"))
        painter.drawText(QPointF(name_left + shown_width + 6, rect.center().y() + painter.fontMetrics().ascent() / 2 - 1),
                         severity)
        painter.restore()


def swatch_icon(token: str | None) -> QIcon:
    """A short trace-coloured line for a table row; an empty swatch of the same size keeps the labels aligned."""
    pixmap = QPixmap(*SWATCH_SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)
    if token is not None:
        painter = QPainter(pixmap)
        painter.fillRect(QRect(0, SWATCH_SIZE[1] // 2 - 1, SWATCH_SIZE[0], 3), token_color(token))
        painter.end()
    return QIcon(pixmap)
