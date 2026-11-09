"""Ctrl+K search over every action, screen and setting.

The list is built from the window's own actions, so a command listed here is the same
object the shortcut triggers and nothing can drift between the two.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEvent, QModelIndex, QObject, QPersistentModelIndex, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QFontMetrics, QKeyEvent, QKeySequence, QMouseEvent, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from src.desktop.accessibility import announce
from src.desktop.theme import token_color

SHORTCUT_ROLE = Qt.ItemDataRole.UserRole + 1
GROUP_ROLE = Qt.ItemDataRole.UserRole + 2
ACTION_ROLE = Qt.ItemDataRole.UserRole + 3
HINT_ROLE = Qt.ItemDataRole.UserRole + 4
# Set on the first row of a group: the group's name, painted as a heading above that row.
HEADING_ROLE = Qt.ItemDataRole.UserRole + 5

MAX_WIDTH = 640
GUTTER = 24
ROW_HEIGHT = 34
HEADING_HEIGHT = 28

PLACEHOLDER = "Search actions, screens and settings"


@dataclass(frozen=True)
class Command:
    """One line in the palette. ``keywords`` are extra words the search matches."""

    action: QAction
    group: str
    keywords: str = ""
    # Shown instead of the action's own shortcut when the key is handled elsewhere (Ctrl+R opens the rules on the research pages).
    shortcut_hint: str = ""

    @property
    def title(self) -> str:
        return self.action.text().replace("&", "")

    @property
    def shortcut(self) -> str:
        if self.shortcut_hint:
            return self.shortcut_hint
        return self.action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)


def match_rank(command: Command, query: str) -> int | None:
    """Lower is better; None means no match. Every word of the query must appear somewhere.

    A command whose title holds every word comes first, those that start with the first word
    before the rest; a match found only in keywords, group or shortcut comes after.
    """
    words = query.lower().split()
    title = command.title.lower()
    haystack = " ".join((title, command.group.lower(), command.keywords.lower(), command.shortcut.lower()))
    if not all(word in haystack for word in words):
        return None
    if not words:
        return 3
    if all(word in title for word in words):
        return 0 if title.startswith(words[0]) else 1
    return 2


def _row_text(command: Command) -> str:
    parts = [command.title]
    if command.shortcut:
        parts.append(f"shortcut {command.shortcut}")
    parts.append(command.group)
    if not command.action.isEnabled() and command.action.toolTip():
        parts.append(command.action.toolTip())
    return ", ".join(parts)


class _RowDelegate(QStyledItemDelegate):
    """Title on the left; the shortcut or the reason it is unavailable on the right, muted.

    The first row of a group carries its group's name as a heading painted above it. A heading is
    not an item, so it cannot be selected and Up and Down never stop on it.
    """

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> QSize:  # noqa: N802
        extra = HEADING_HEIGHT if index.data(HEADING_ROLE) else 0
        return QSize(option.rect.width(), ROW_HEIGHT + extra)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        heading = index.data(HEADING_ROLE) or ""
        row = QRect(option.rect)
        if heading:
            row.setTop(row.top() + HEADING_HEIGHT)
        opt.rect = row
        widget = option.widget
        style = widget.style() if widget is not None else QApplication.style()
        painter.save()
        if heading:
            small = painter.font()
            small.setPointSizeF(max(7.0, small.pointSizeF() - 1.5))
            small.setWeight(small.Weight.DemiBold)
            painter.setFont(small)
            painter.setPen(token_color("ink_3"))
            painter.drawText(QRect(option.rect.left() + 14, option.rect.top(), option.rect.width() - 28, HEADING_HEIGHT),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, heading_text(index))
        painter.restore()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)
        enabled = bool(index.flags() & Qt.ItemFlag.ItemIsEnabled)
        rect = row.adjusted(14, 0, -14, 0)
        painter.save()
        side = index.data(SHORTCUT_ROLE) or index.data(HINT_ROLE) or ""
        painter.setPen(token_color("ink_3"))
        side_width = QFontMetrics(painter.font()).horizontalAdvance(side) if side else 0
        if side:
            painter.drawText(rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, side)
        painter.setPen(token_color("ink" if enabled else "rule_strong"))
        title = QFontMetrics(painter.font()).elidedText(
            index.data(Qt.ItemDataRole.DisplayRole) or "", Qt.TextElideMode.ElideRight,
            max(40, rect.width() - side_width - 16),
        )
        painter.drawText(rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, title)
        painter.restore()


def heading_text(index: QModelIndex | QPersistentModelIndex) -> str:
    """A section heading as written: the design allows no capitals or letter spacing."""
    return str(index.data(HEADING_ROLE) or "")


class _ClickRows(QObject):
    """A click runs the row under it; a click on a section heading painted above a row runs nothing."""

    def __init__(self, palette: CommandPalette):
        super().__init__(palette)
        self._palette = palette

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() != QEvent.Type.MouseButtonRelease or not isinstance(event, QMouseEvent):
            return False
        results = self._palette.results
        pos = event.position().toPoint()
        item = results.itemAt(pos)
        if item is None:
            return False
        if item.data(HEADING_ROLE) and pos.y() < results.visualItemRect(item).top() + HEADING_HEIGHT:
            return True
        results.setCurrentItem(item)
        self._palette.run_current()
        return True


class _SearchKeys(QObject):
    """Up, Down, Page keys and Enter in the search field drive the list, as in any command palette."""

    def __init__(self, palette: CommandPalette):
        super().__init__(palette)
        self._palette = palette

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() != QEvent.Type.KeyPress or not isinstance(event, QKeyEvent):
            return False
        steps = {Qt.Key.Key_Down: 1, Qt.Key.Key_Up: -1, Qt.Key.Key_PageDown: 8, Qt.Key.Key_PageUp: -8}
        step = next((value for key, value in steps.items() if key == event.key()), None)
        if step is not None:
            self._palette.step_selection(step)
            return True
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._palette.run_current()
            return True
        return False


class CommandPalette(QDialog):
    """A frameless search box over a list of commands. Enter runs the highlighted one; Escape closes."""

    def __init__(self, commands: list[Command], parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("commandPalette")
        self.setWindowTitle("Find")
        self.setModal(True)
        self._commands = commands
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(8)
        self.search = QLineEdit(self)
        self.search.setObjectName("paletteSearch")
        self.search.setPlaceholderText(PLACEHOLDER)
        self.search.setAccessibleName(PLACEHOLDER)
        self.search.setClearButtonEnabled(False)
        self._keys = _SearchKeys(self)
        self.search.installEventFilter(self._keys)
        self.search.textChanged.connect(self._refill)
        layout.addWidget(self.search)
        self.count = QLabel(self)
        self.count.setObjectName("paletteHint")
        self.count.setAccessibleName("Number of results")
        layout.addWidget(self.count)
        self.results = QListWidget(self)
        self.results.setObjectName("paletteList")
        self.results.setAccessibleName("Results")
        self.results.setItemDelegate(_RowDelegate(self.results))
        self.results.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.results.setMinimumHeight(ROW_HEIGHT * 8)
        self.results.itemActivated.connect(lambda _item: self.run_current())
        self._clicks = _ClickRows(self)
        self.results.viewport().installEventFilter(self._clicks)
        self.results.currentItemChanged.connect(self._announce_current)
        layout.addWidget(self.results, 1)
        keys = QLabel("Up and Down move · Enter runs · Esc closes · Ctrl+K opens this from any screen", self)
        keys.setObjectName("paletteHint")
        layout.addWidget(keys)
        self.setTabOrder(self.search, self.results)
        self.setFixedWidth(MAX_WIDTH)
        self._refill()

    # ----- list -----
    def visible_commands(self) -> list[Command]:
        return [self.results.item(row).data(ACTION_ROLE) for row in range(self.results.count())]

    def _refill(self) -> None:
        query = self.search.text()
        ranked = []
        for position, command in enumerate(self._commands):
            rank = match_rank(command, query)
            if rank is not None:
                ranked.append((rank, position, command))
        ranked.sort(key=lambda entry: entry[:2])
        # Each group is a section; the section holding the best match comes first, and rows keep their rank inside it
        sections: dict[str, list[Command]] = {}
        for _rank, _position, command in ranked:
            sections.setdefault(command.group, []).append(command)
        # Clearing and refilling would announce an empty list and every row on the way; announce once below
        self.results.blockSignals(True)
        self.results.clear()
        for group, commands in sections.items():
            for number, command in enumerate(commands):
                item = QListWidgetItem(command.title)
                item.setData(ACTION_ROLE, command)
                item.setData(GROUP_ROLE, command.group)
                item.setData(SHORTCUT_ROLE, command.shortcut)
                if number == 0:
                    item.setData(HEADING_ROLE, group)
                enabled = command.action.isEnabled()
                if not enabled:
                    item.setData(HINT_ROLE, command.action.toolTip())
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                elif command.action.isCheckable() and command.action.isChecked():
                    item.setData(HINT_ROLE, "active")
                item.setData(Qt.ItemDataRole.AccessibleTextRole, _row_text(command))
                self.results.addItem(item)
        total = self.results.count()
        self.count.setText("No results" if total == 0 else f"{total} result{'s' if total != 1 else ''}")
        self.step_selection(0, first=True)
        self.results.blockSignals(False)
        self._announce_current(self.results.currentItem())

    def step_selection(self, step: int, first: bool = False) -> None:
        rows = [row for row in range(self.results.count())
                if self.results.item(row).flags() & Qt.ItemFlag.ItemIsEnabled]
        if not rows:
            return
        if first:
            self.results.setCurrentRow(rows[0])
            return
        current = self.results.currentRow()
        at = rows.index(current) if current in rows else 0
        self.results.setCurrentRow(rows[max(0, min(len(rows) - 1, at + step))])

    def _announce_current(self, item: QListWidgetItem | None) -> None:
        if item is None:
            # A list whose rows are all unavailable still has results; say how many
            announce(self.results, self.count.text())
            return
        position = self.results.row(item) + 1
        announce(self.results, f"{item.data(Qt.ItemDataRole.AccessibleTextRole)}, {position} of {self.results.count()}")

    # ----- run -----
    def run_current(self) -> None:
        item = self.results.currentItem()
        if item is None or not item.flags() & Qt.ItemFlag.ItemIsEnabled:
            return
        command: Command = item.data(ACTION_ROLE)
        self.accept()
        # After the palette has closed, so a dialog the command opens does not start under it.
        QTimer.singleShot(0, command.action.trigger)

    # ----- placement -----
    def open_over(self, window: QWidget) -> None:
        frame = window.frameGeometry()
        self.setFixedWidth(max(280, min(MAX_WIDTH, window.width() - 2 * GUTTER)))
        self.adjustSize()
        self.setGeometry(frame.center().x() - self.width() // 2, frame.top() + 80, self.width(), self.sizeHint().height())
        self.search.setFocus(Qt.FocusReason.PopupFocusReason)
        self.open()
