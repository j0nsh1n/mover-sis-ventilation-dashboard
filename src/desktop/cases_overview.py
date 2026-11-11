"""Sortable list of the processed cases, opened from the research workspace header."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pandas as pd
from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    QRectF,
    QSortFilterProxyModel,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QFont, QFontMetrics, QIcon, QKeyEvent, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QBoxLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from src.pipeline.corpus_scan import ScanResult
    from src.rules import RuleCatalog

from src.desktop.accessibility import announce
from src.desktop.cases_preview import (
    NO_CASES_TO_PREVIEW,
    NOTHING_TO_PREVIEW,
    PREVIEW_WIDTH,
    CasePreview,
    PreviewCase,
    PreviewSource,
)
from src.desktop.cases_summary import SummaryBlock, duration_spec, rules_spec, score_spec
from src.desktop.fonts import tabular
from src.desktop.shell import nav_icon
from src.desktop.theme import active_theme, token_color

SCOPE_SAMPLE = "sample"
SCOPE_SCAN = "scan"

EMPTY_MESSAGE = "No processed cases yet. Run Data setup or a full-EMR scan."

# List-column widths below which the header controls and the three charts stack instead of sitting in a row.
HEADER_ROW_WIDTH = 760
CHARTS_ROW_WIDTH = 600
# A list with more rows than this waits for a pause in typing before it filters: each pass over a full scan takes
# a noticeable part of a second, and a short list filters on every key
FILTER_PAUSE_ROWS = 2000
FILTER_PAUSE_MS = 250
ROW_HEIGHT = 33
HEADER_HEIGHT = 34
HEADER_HEIGHT_WRAPPED = 48
CELL_PADDING = 8
# Room at the right of a number column's heading and cells for the sort arrow, so the arrow never covers a letter
SORT_ARROW_ROOM = 14
LIST_MARGINS_WIDE = (32, 16, 28, 20)
LIST_MARGINS_STACKED = (32, 16, 32, 24)
# Where the list's rows end at the right: the vertical scroll bar's width plus the table's 2 px frame on each side
TABLE_CHROME = 14
# The page's own vertical scroll bar, which narrows the list when the page is taller than the window
PAGE_SCROLLBAR = 14
SCORE_BAR_WIDTH = 40
SCORE_BAR_HEIGHT = 5
SELECTED_BAR = 3
SORT_ROLE = Qt.ItemDataRole.UserRole
# What the table sorts on: the place of a row's value among its column's, with the case ID breaking ties
RANK_ROLE = Qt.ItemDataRole.UserRole + 1
_NO_PARENT = QModelIndex()


@dataclass(frozen=True)
class Column:
    key: str
    title: str
    kind: str  # "text" or "number"
    tooltip: str = ""
    first_sort_descending: bool = False
    # False: the field is shown as a "label: value" fact in the preview and has no column
    in_table: bool = True


COLUMNS = (
    Column("PID", "PID", "text"),
    Column("Procedure_short", "Procedure", "text"),
    Column("Age", "Age", "number"),
    Column("Gender", "Sex", "text"),
    Column("case_duration_min", "Duration (min)", "number"),
    Column("anomaly_score", "Anomaly score", "number",
           "Each flagged minute counts once at its worst severity; composites count per episode.",
           first_sort_descending=True),
    Column("n_warn", "Warn count", "number", "Minutes with a warn flag.", first_sort_descending=True),
    Column("n_critical", "Critical count", "number", "Minutes with a critical flag.",
           first_sort_descending=True),
    Column("top_rules", "Top rules", "text", "Most frequent rules with their flagged-minute counts."),
    Column("primary_agent_name", "Primary agent", "text", in_table=False),
    Column("median_TV", "Median TV (mL)", "number", in_table=False),
    Column("median_PIP", "Median PIP (cmH₂O)", "number", in_table=False),
    Column("median_ETCO2", "Median ETCO₂ (mmHg)", "number", in_table=False),
)
# The table's columns are the leading ones, so a column's number is also its place in COLUMNS.
TABLE_COLUMNS = tuple(column for column in COLUMNS if column.in_table)
PREVIEW_FIELDS = tuple(column for column in COLUMNS if not column.in_table)
assert COLUMNS[:len(TABLE_COLUMNS)] == TABLE_COLUMNS
SCORE_COLUMN = next(i for i, c in enumerate(COLUMNS) if c.key == "anomaly_score")
_COLUMN_INDEX = {column.key: i for i, column in enumerate(COLUMNS)}
_COUNT_COLUMNS = (_COLUMN_INDEX["n_warn"], _COLUMN_INDEX["n_critical"])
_MUTED_COLUMNS = (_COLUMN_INDEX["top_rules"],)
# The two columns that give up room and shorten with "…"; every other column keeps its whole text.
_FLEXIBLE = (_COLUMN_INDEX["Procedure_short"], _COLUMN_INDEX["top_rules"])
# Widths a flexible column is given when there is room, and the least it is ever given
_FLEX_IDEAL = {_FLEXIBLE[0]: 230, _FLEXIBLE[1]: 460}
_FLEX_LEAST = 64
_FLEX_READABLE = {_FLEXIBLE[0]: 150, _FLEXIBLE[1]: 260}
SCORE_BAR_LEAST = 24
_FLAG_COUNTS = ("n_info", "n_warn", "n_critical")

_RULE_ENTRY = re.compile(r"^\s*([^\s(),]+)\s*(?:\((\d+)\))?\s*$")


def _number_text(value: float) -> str:
    return f"{int(value)}" if float(value).is_integer() else f"{value:.1f}"


def _plural(count: int, word: str) -> str:
    return f"{count:,} {word}" if count == 1 else f"{count:,} {word}s"


def _sort_name(title: str) -> str:
    """A column's heading as it reads in a sentence: no unit in brackets, lower case unless an acronym."""
    name = re.sub(r"\s*\(.*\)", "", title)
    return name if name[:2].isupper() else name[:1].lower() + name[1:]


class _CasesModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._cells: list[list[Any]] = [[] for _ in COLUMNS]
        self._count = 0
        self._flagged: list[bool] = []
        self._labels: dict[str, str] = {}
        self._rule_text: dict[str, str] = {}
        self._search: dict[int, tuple[str, ...]] = {}
        self._present: set[str] = set()
        self._value_ranks: dict[int, list[int]] = {}
        self._descending = False

    def has_field(self, key: str) -> bool:
        """True when the loaded frame has this field at all; a value missing inside it is "missing" in the preview."""
        return key in self._present

    @property
    def labels(self) -> Mapping[str, str]:
        return self._labels

    def set_labels(self, labels: Mapping[str, str]) -> None:
        self.beginResetModel()
        self._labels = dict(labels)
        self._rule_text = {}
        self._search = {}
        self.endResetModel()

    def set_frame(self, frame: pd.DataFrame | None) -> None:
        self.beginResetModel()
        self._search = {}
        self._value_ranks = {}
        self._present = set()
        if frame is None or frame.empty or "PID" not in frame:
            self._cells = [[] for _ in COLUMNS]
            self._count = 0
            self._flagged = []
        else:
            self._present = {column.key for column in COLUMNS if column.key in frame.columns}
            # Equal values in a sorted column stay in source order, so the source is in case-ID order for the tie-break
            ordered = frame.assign(_pid=frame["PID"].astype(str)).sort_values(
                "_pid", kind="stable", key=lambda ids: ids.str.casefold())
            cells: list[list[Any]] = []
            for column in COLUMNS:
                if column.key not in ordered.columns:
                    cells.append([None] * len(ordered))
                elif column.kind == "number":
                    values = pd.to_numeric(ordered[column.key], errors="coerce")
                    cells.append([None if pd.isna(v) else float(v) for v in values])
                else:
                    cells.append([
                        "" if pd.isna(v) else str(v) for v in ordered[column.key]
                    ])
            self._cells = cells
            self._count = len(ordered)
            flagged = pd.Series(0.0, index=ordered.index)
            for name in _FLAG_COUNTS:
                if name in ordered:
                    flagged = flagged + pd.to_numeric(ordered[name], errors="coerce").fillna(0)
            self._flagged = [bool(v) for v in (flagged > 0)]
        self.endResetModel()

    def set_descending(self, descending: bool) -> None:
        """Which way the table is about to sort: equal values keep case-ID order ascending either way."""
        self._descending = descending

    def rank_of(self, column: int, row: int) -> int:
        """The number the table sorts a cell on: its value's place in the column, then its row's place in case-ID order.

        Qt's sort is not stable, so the tie-break has to be part of the number. The places in a column are worked out
        the first time it is sorted, so a sort compares whole numbers and never calls back into Python to compare cells.
        """
        places = self._value_ranks.get(column)
        if places is None:
            keys = [(0, "") if v is None else (1, v.casefold() if isinstance(v, str) else v) for v in self._cells[column]]
            order = {key: i for i, key in enumerate(sorted(set(keys)))}
            places = self._value_ranks[column] = [order[key] for key in keys]
        # the rows are in case-ID order, so a row's own number is its place in it
        return places[row] * self._count + (self._count - 1 - row if self._descending else row)

    def shown_texts(self, column: int) -> set[str]:
        """The distinct texts a column shows, for sizing it to the widest."""
        values = self._cells[column]
        if COLUMNS[column].kind == "number":
            return {_number_text(v) for v in values if v is not None}
        return {str(v) for v in values if v}

    def has_episodes(self, row: int) -> bool:
        """True when the case has at least one flagged minute, so it has rule episodes."""
        return self._flagged[row]

    def search_fields(self, row: int) -> tuple[str, ...]:
        """The case ID, the procedure and the rule names of a row, folded for a case-insensitive match."""
        cached = self._search.get(row)
        if cached is None:
            names = []
            for entry in str(self._cells[_COLUMN_INDEX["top_rules"]][row] or "").split(","):
                match = _RULE_ENTRY.match(entry)
                if match is not None:
                    names.append(self._labels.get(match.group(1), match.group(1)))
                elif entry.strip():
                    names.append(entry.strip())
            cached = (
                str(self._cells[_COLUMN_INDEX["PID"]][row]).casefold(),
                str(self._cells[_COLUMN_INDEX["Procedure_short"]][row] or "").casefold(),
                *(name.casefold() for name in names),
            )
            self._search[row] = cached
        return cached

    def rowCount(self, parent=_NO_PARENT) -> int:  # noqa: N802
        return 0 if parent.isValid() else self._count

    def columnCount(self, parent=_NO_PARENT) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(TABLE_COLUMNS)

    def pid_at(self, row: int) -> str:
        return str(self._cells[0][row])

    def row_values(self, row: int) -> dict[str, Any]:
        return {column.key: self._cells[i][row] for i, column in enumerate(COLUMNS)}

    def rules_text(self, raw: str) -> str:
        """Rule labels from the loaded preset's catalog; unknown ids keep their raw id."""
        cached = self._rule_text.get(raw)
        if cached is not None:
            return cached
        parts = []
        for entry in raw.split(","):
            match = _RULE_ENTRY.match(entry)
            if match is None:
                if entry.strip():
                    parts.append(entry.strip())
                continue
            rule_id, count = match.groups()
            label = self._labels.get(rule_id, rule_id)
            parts.append(f"{label} ({count})" if count else label)
        text = ", ".join(parts)
        self._rule_text[raw] = text
        return text

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        column = COLUMNS[index.column()]
        if role == RANK_ROLE:
            return self.rank_of(index.column(), index.row())
        value = self._cells[index.column()][index.row()]
        if role == SORT_ROLE:
            return value.casefold() if isinstance(value, str) else value
        if role == Qt.ItemDataRole.DisplayRole or role == Qt.ItemDataRole.ToolTipRole:
            if value is None:
                return ""
            if column.kind == "number":
                return _number_text(value)
            return self.rules_text(value) if column.key == "top_rules" else value
        if role == Qt.ItemDataRole.TextAlignmentRole and column.kind == "number":
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def headerData(self, section: int, orientation, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation != Qt.Orientation.Horizontal or not 0 <= section < len(TABLE_COLUMNS):
            return None
        column = COLUMNS[section]
        if role == Qt.ItemDataRole.DisplayRole:
            return column.title
        if role == Qt.ItemDataRole.ToolTipRole and column.tooltip:
            return column.tooltip
        if role == Qt.ItemDataRole.InitialSortOrderRole and column.first_sort_descending:
            return Qt.SortOrder.DescendingOrder
        if role == Qt.ItemDataRole.TextAlignmentRole:
            side = Qt.AlignmentFlag.AlignRight if column.kind == "number" else Qt.AlignmentFlag.AlignLeft
            # a heading wraps to a second line before it is ever cut, once the list is as narrow as it gets
            return int(side | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap)
        return None


class _CasesProxy(QSortFilterProxyModel):
    """Sorts on the model's ranks, which Qt compares without calling Python. A rank already carries the tie-break
    (equal values by case ID ascending in both directions, so repeated sorts do not reshuffle).

    Rows are kept when the typed text is part of their case ID, procedure or a rule name.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._needle = ""

    def set_filter_text(self, text: str) -> None:
        self._needle = text.strip().casefold()
        self.invalidateFilter()

    def filter_text(self) -> str:
        return self._needle

    def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:
        source = self.sourceModel()
        if isinstance(source, _CasesModel):
            source.set_descending(order == Qt.SortOrder.DescendingOrder)
        super().sort(column, order)

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex | QPersistentModelIndex) -> bool:  # noqa: N802
        if not self._needle:
            return True
        source = self.sourceModel()
        assert isinstance(source, _CasesModel)
        return any(self._needle in field for field in source.search_fields(source_row))


class _CasesTable(QTableView):
    """Emits ``row_activated`` once per opening gesture: Enter, or a double-click. One click only selects the row."""

    row_activated = Signal(int)
    resized = Signal()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        super().mouseDoubleClickEvent(event)
        index = self.indexAt(event.position().toPoint())
        if index.isValid() and event.button() == Qt.MouseButton.LeftButton:
            self.row_activated.emit(index.row())

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.currentIndex().isValid():
            self.row_activated.emit(self.currentIndex().row())
            event.accept()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.resized.emit()


def plan_column_widths(available: int, tiers: Sequence[Sequence[int]], flexible: Sequence[int]) -> list[int]:
    """Column widths that add up to ``available``, or to the first tier when even that does not fit.

    ``tiers`` are widths in the order they are wanted, each at least the one before it: the least every column needs,
    then more for the columns that matter first, and so on. Room goes to a tier completely before the next one gets
    any; inside a step it is shared by how much each column still wants. Room beyond the last tier goes to the
    ``flexible`` columns, so the widths always fill the list.
    """
    widths = [int(w) for w in tiers[0]]
    spare = available - sum(widths)
    for previous, target in zip(tiers, tiers[1:], strict=False):
        if spare <= 0:
            return widths
        wanted = [t - p for p, t in zip(previous, target, strict=True)]
        total = sum(wanted)
        if total == 0:
            continue
        given = min(spare, total)
        shares = [given * w // total for w in wanted]
        # whole pixels left over by the division go to the columns that still wanted more, one each
        for i in sorted(range(len(wanted)), key=lambda i: -wanted[i])[: given - sum(shares)]:
            shares[i] += 1
        widths = [w + share for w, share in zip(widths, shares, strict=True)]
        spare -= given
    if spare > 0:
        weights = [tiers[-1][i] for i in flexible]
        added = [spare * w // sum(weights) for w in weights]
        added[-1] += spare - sum(added)
        for i, extra in zip(flexible, added, strict=True):
            widths[i] += extra
    return widths


def score_bar_length(value: float, peak: float, room: float) -> float:
    """Length of a score's bar: the largest score in view fills the room (at most ``SCORE_BAR_WIDTH``), the rest in proportion."""
    if value <= 0 or peak <= 0 or room <= 2:
        return 0.0
    return max(2.0, min(SCORE_BAR_WIDTH, room) * value / peak)


class _CasesDelegate(QStyledItemDelegate):
    """Draws a cell's text in the tokens: a zero count in muted text, the score with a short bar, and the current
    row's accent bar at its left edge. Row fill and row line come from the app's table style."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._peak = 0.0

    def set_peak(self, peak: float) -> None:
        """The largest score in view; the score bars are drawn against it."""
        self._peak = peak

    @staticmethod
    def text_token(index: QModelIndex | QPersistentModelIndex) -> str:
        """The token a cell's text is drawn in: muted for a zero count and for the quieter columns, ink otherwise."""
        column, value = index.column(), index.data(SORT_ROLE)
        if column in _COUNT_COLUMNS and isinstance(value, float) and value == 0:
            return "ink_3"
        return "ink_2" if column in _MUTED_COLUMNS else "ink"

    @staticmethod
    def shown(text: str, metrics: QFontMetrics, column: int, width: int) -> str:
        """``text`` as a cell of ``width`` shows it: whole, or cut with an ellipsis when it does not fit."""
        number = COLUMNS[column].kind == "number"
        room = width - 2 * CELL_PADDING - (SORT_ARROW_ROOM if number else 0)
        return metrics.elidedText(text, Qt.TextElideMode.ElideRight, room)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text = opt.text
        opt.text = ""
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        rect = opt.rect
        column = index.column()
        painter.save()
        if column == 0 and opt.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(QRectF(rect.left(), rect.top(), SELECTED_BAR, rect.height()), token_color("accent"))
        value = index.data(SORT_ROLE)
        font = QFont(opt.font)
        flags = int(Qt.AlignmentFlag.AlignVCenter) | int(
            Qt.AlignmentFlag.AlignRight if COLUMNS[column].kind == "number" else Qt.AlignmentFlag.AlignLeft)
        number = COLUMNS[column].kind == "number"
        left = rect.left() + CELL_PADDING
        right = rect.right() + 1 - CELL_PADDING - (SORT_ARROW_ROOM if number else 0)
        if column == SCORE_COLUMN:
            font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font)
            width = painter.fontMetrics().horizontalAdvance(text)
            if isinstance(value, float):
                length = score_bar_length(value, self._peak, right - left - width - 8)
                painter.fillRect(
                    QRectF(left, rect.center().y() - SCORE_BAR_HEIGHT / 2 + 0.5, length, SCORE_BAR_HEIGHT),
                    token_color("ink_2"))
        painter.setFont(font)
        painter.setPen(token_color(self.text_token(index)))
        painter.drawText(left, rect.top(), right - left, rect.height(), flags,
                         self.shown(text, painter.fontMetrics(), column, rect.width()))
        painter.restore()


class _FilterField(QLineEdit):
    """The filter: a magnifier at its left, the "/" key named at its right while empty, Escape clears typed text."""

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        # Escape in an empty field is left unhandled so it reaches the page and goes back, as it does everywhere else
        if event.key() == Qt.Key.Key_Escape and self.text():
            self.clear()
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        icon: QIcon = nav_icon("find", 15, active_theme())
        size = 15
        icon.paint(painter, 11, (self.height() - size) // 2, size, size)
        if not self.text():
            font = QFont(self.font())
            font.setPixelSize(12)
            painter.setFont(font)
            painter.setPen(token_color("ink_3"))
            painter.drawText(self.rect().adjusted(0, 0, -11, 0),
                             int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight), "/")


class CasesOverview(QWidget):
    case_requested = Signal(str)
    back_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("casesPage")
        self._cases: pd.DataFrame | None = None
        self._scan: ScanResult | None = None
        self._scope = SCOPE_SAMPLE
        self._preset: str | None = None
        self._flags: pd.DataFrame | None = None
        self._preview_source = PreviewSource()
        self._wide: bool | None = None
        # the nine columns' width tiers (see plan_column_widths) and the width each whole heading needs
        self._tiers: list[list[int]] = [[], [], [], []]
        self._full: list[int] = []
        self._header_row: bool | None = None
        self._charts_row: bool | None = None
        self._applied_filter = ""
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(FILTER_PAUSE_MS)
        self._filter_timer.timeout.connect(self._apply_filter)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        # The whole page scrolls when the window is shorter than its parts; the list keeps its own scrolling
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        outer.addWidget(self._scroll)
        body = QWidget()
        self._body = QBoxLayout(QBoxLayout.Direction.LeftToRight, body)
        self._body.setContentsMargins(0, 0, 0, 0)
        self._body.setSpacing(0)
        self._scroll.setWidget(body)
        left = QWidget()
        self._column = QVBoxLayout(left)
        self._column.setSpacing(20)
        self._body.addWidget(left, 1)
        self.preview = CasePreview()
        self.preview.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self._body.addWidget(self.preview, 0)

        back_row = QHBoxLayout()
        self.back_button = QPushButton("← Back to question")
        self.back_button.setObjectName("linkBtn")
        self.back_button.setAutoDefault(True)
        self.back_button.setAccessibleName("Back to the research question")
        self.back_button.clicked.connect(self.back_requested)
        back_row.addWidget(self.back_button)
        back_row.addStretch(1)
        self._column.addLayout(back_row)

        self._header = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._header.setSpacing(16)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = QLabel("Cases")
        title.setObjectName("caseTitle")
        titles.addWidget(title)
        self.summary_label = QLabel()
        self.summary_label.setObjectName("caseFacts")
        self.summary_label.setWordWrap(True)
        titles.addWidget(self.summary_label)
        self.scope_label = QLabel()
        self.scope_label.setObjectName("quietText")
        self.scope_label.setWordWrap(True)
        titles.addWidget(self.scope_label)
        self._header.addLayout(titles, 1)
        controls = QHBoxLayout()
        controls.setSpacing(12)
        self.scope_tabs = QGroupBox()
        self.scope_tabs.setObjectName("scopeSwitch")
        self.scope_tabs.setAccessibleName("Case scope")
        self.scope_tabs.setFlat(True)
        tabs = QHBoxLayout(self.scope_tabs)
        tabs.setContentsMargins(0, 0, 0, 0)
        tabs.setSpacing(0)
        self.sample_tab = self._tab(SCOPE_SAMPLE)
        self.scan_tab = self._tab(SCOPE_SCAN)
        tabs.addWidget(self.sample_tab)
        tabs.addWidget(self.scan_tab)
        controls.addWidget(self.scope_tabs)
        self.filter_edit = _FilterField()
        self.filter_edit.setObjectName("casesFilter")
        self.filter_edit.setPlaceholderText("Filter by case, procedure, rule")
        self.filter_edit.setAccessibleName("Filter cases")
        self.filter_edit.setAccessibleDescription(
            "Shows the cases whose case ID, procedure or rule names contain the typed text. Press / to reach it."
        )
        self.filter_edit.textChanged.connect(self._filter_changed)
        controls.addWidget(self.filter_edit, 1)
        self._controls = controls
        self._header.addLayout(controls)
        self._header.setAlignment(controls, Qt.AlignmentFlag.AlignBottom)
        self._column.addLayout(self._header)

        self.charts_panel = QWidget()
        self._charts = QBoxLayout(QBoxLayout.Direction.LeftToRight, self.charts_panel)
        self._charts.setContentsMargins(0, 0, 0, 0)
        self._charts.setSpacing(32)
        self.score_block = SummaryBlock("Anomaly score per case")
        self.rules_block = SummaryBlock("Most frequent rules", "flagged minutes")
        self.duration_block = SummaryBlock("Surgery duration")
        for block, stretch in ((self.score_block, 4), (self.rules_block, 5), (self.duration_block, 4)):
            self._charts.addWidget(block, stretch)
        self._column.addWidget(self.charts_panel)

        self.filter_count_label = QLabel()
        self.filter_count_label.setObjectName("quietText")
        self.filter_count_label.setWordWrap(True)
        self._column.addWidget(self.filter_count_label)
        self.status_label = QLabel()
        self.status_label.setObjectName("quietText")
        self.status_label.setWordWrap(True)
        self.status_label.hide()
        self._column.addWidget(self.status_label)

        self._model = _CasesModel(self)
        self._proxy = _CasesProxy(self)
        self._proxy.setSourceModel(self._model)
        self._proxy.setSortRole(RANK_ROLE)
        self.table = _CasesTable()
        self.table.setObjectName("casesTable")
        self.table.setAccessibleName("Cases")
        self.table.setAccessibleDescription(
            "Click a row to preview it. Enter or a double-click opens the case. Click a column heading to sort."
        )
        self.table.setModel(self._proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setSortingEnabled(True)
        self.table.setWordWrap(False)
        # Tab must leave the table, or the controls and the preview's Open case are unreachable from it
        self.table.setTabKeyNavigation(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        # Nine columns always fit the list's width, so it never scrolls sideways; below the least width the page does
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setMinimumHeight(240)
        self._delegate = _CasesDelegate(self.table)
        self.table.setItemDelegate(self._delegate)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header.setMinimumSectionSize(1)
        header.setSortIndicatorClearable(False)
        header.setFixedHeight(HEADER_HEIGHT)
        self.table.row_activated.connect(self._activate_row)
        self.table.resized.connect(self._layout_columns)
        self.table.verticalScrollBar().rangeChanged.connect(self._layout_columns)
        header.sortIndicatorChanged.connect(self._sort_changed)
        self._column.addWidget(self.table, 1)
        self.empty_label = QLabel(EMPTY_MESSAGE)
        self.empty_label.setObjectName("bodyText")
        self.empty_label.setWordWrap(True)
        self._column.addWidget(self.empty_label)
        self.no_match_label = QLabel()
        self.no_match_label.setObjectName("bodyText")
        self.no_match_label.setWordWrap(True)
        self.no_match_label.hide()
        self._column.addWidget(self.no_match_label)
        self._column.addStretch(0)

        self.preview.open_requested.connect(self._open_current)
        self.table.selectionModel().currentRowChanged.connect(lambda *_: self._update_preview())
        self._filter_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Slash), self)
        self._filter_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._filter_shortcut.activated.connect(self.focus_filter)

        self.table.sortByColumn(SCORE_COLUMN, Qt.SortOrder.DescendingOrder)
        self._reflow()
        self._fill()

    def _tab(self, scope: str) -> QRadioButton:
        tab = QRadioButton()
        tab.setObjectName("scopeOption")
        tab.toggled.connect(lambda checked, which=scope: self._tab_toggled(which, checked))
        return tab

    # -- keys and focus ---------------------------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        # The table and the tabs ignore Escape, so it reaches the page from any child
        if event.key() == Qt.Key.Key_Escape:
            self.back_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def focus_filter(self) -> None:
        self.filter_edit.setFocus()

    def focus_table(self) -> None:
        if self.table.isVisibleTo(self):
            self.table.setFocus()
            if not self.table.selectionModel().hasSelection() and self._proxy.rowCount():
                self.table.selectRow(0)
        else:
            self.back_button.setFocus()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._reflow()

    def least_width(self) -> int:
        """The narrowest page that shows all nine columns; narrower, the page scrolls sideways instead."""
        return sum(self._tiers[0]) + TABLE_CHROME + PAGE_SCROLLBAR + sum(LIST_MARGINS_STACKED[::2])

    def side_by_side_width(self) -> int:
        """Page width from which the preview sits beside the list: the list keeps readable Procedure and Top rules
        columns beside it, so the table is never at its tightest right after the preview moves over."""
        return sum(self._tiers[1]) + TABLE_CHROME + PAGE_SCROLLBAR + sum(LIST_MARGINS_WIDE[::2]) + PREVIEW_WIDTH

    def _reflow(self) -> None:
        wide = self.width() >= self.side_by_side_width()
        if wide != self._wide:
            self._wide = wide
            self._body.setDirection(QBoxLayout.Direction.LeftToRight if wide else QBoxLayout.Direction.TopToBottom)
            self._column.setContentsMargins(*(LIST_MARGINS_WIDE if wide else LIST_MARGINS_STACKED))
            if wide:
                self.preview.setFixedWidth(PREVIEW_WIDTH)
            else:
                self.preview.setMinimumWidth(0)
                self.preview.setMaximumWidth(16777215)
            self.preview.setProperty("stacked", not wide)
            self.preview.style().unpolish(self.preview)
            self.preview.style().polish(self.preview)
        room = self.width() - (PREVIEW_WIDTH if wide else 0)
        header_row = room >= HEADER_ROW_WIDTH
        if header_row != self._header_row:
            self._header_row = header_row
            self._header.setDirection(QBoxLayout.Direction.LeftToRight if header_row else QBoxLayout.Direction.TopToBottom)
            self.filter_edit.setMinimumWidth(300 if header_row else 220)
            self.filter_edit.setMaximumWidth(300 if header_row else 16777215)
        charts_row = room >= CHARTS_ROW_WIDTH
        if charts_row != self._charts_row:
            self._charts_row = charts_row
            self._charts.setDirection(QBoxLayout.Direction.LeftToRight if charts_row else QBoxLayout.Direction.TopToBottom)

    # -- data -------------------------------------------------------------------------------------------

    def set_data(
        self,
        cases: pd.DataFrame | None,
        scan: ScanResult | None,
        catalog: RuleCatalog | None,
        flags: pd.DataFrame | None = None,
        timeseries: pd.DataFrame | None = None,
        episodes: pd.DataFrame | None = None,
    ) -> None:
        """Replace everything shown; rows from before this call are never kept.

        ``timeseries`` and ``episodes`` are the loaded sample's minute data and rule episodes, already in memory;
        the preview draws from them and never reads anything itself.
        """
        self._cases = cases
        self._scan = scan
        self._flags = flags
        self._preset = catalog.preset if catalog is not None else None
        self._preview_source = PreviewSource(timeseries, episodes, scan.episodes if scan is not None else None)
        self._model.set_labels(
            {spec.id: spec.label for spec in catalog.specs} if catalog is not None else {}
        )
        if scan is None:
            self._scope = SCOPE_SAMPLE
        self.set_message("")
        self._fill()

    def set_message(self, text: str) -> None:
        self.status_label.setText(text)
        self.status_label.setVisible(bool(text))
        if text:
            announce(self, text)
        else:
            # a message that comes back later is news again
            self.setProperty("lastAnnouncement", "")

    def scope(self) -> str:
        return self._scope

    def set_scope(self, scope: str) -> None:
        """Show the loaded sample or, when a full-EMR scan exists, the scan's scored cases."""
        if scope == SCOPE_SCAN and self._scan is not None:
            self.scan_tab.setChecked(True)
        elif scope == SCOPE_SAMPLE:
            self.sample_tab.setChecked(True)

    def row_for(self, pid: str) -> dict[str, Any] | None:
        for row in range(self._model.rowCount()):
            if self._model.pid_at(row) == pid:
                return self._model.row_values(row)
        return None

    def _scan_meta(self) -> Mapping[str, Any]:
        return self._scan.meta if self._scan is not None else {}

    def _scan_on_screen(self) -> bool:
        return self._scope == SCOPE_SCAN and self._scan is not None

    def _indexed_count(self) -> int | None:
        indexed = self._scan_meta().get("n_indexed_surgeries")
        return indexed if isinstance(indexed, int) else None

    def _scan_statement(self) -> str:
        meta = self._scan_meta()
        scored = len(self._scan.cases) if self._scan is not None else 0
        indexed, ventilated = self._indexed_count(), meta.get("n_ventilated_surgeries")
        text = f"Full-EMR scan: {scored:,} scored"
        if indexed is not None:
            text += f" of {indexed:,} indexed surgeries"
        if indexed is not None and isinstance(ventilated, int) and indexed >= ventilated:
            text += f", {indexed - ventilated:,} without ventilator data"
        failed = sum(
            int(shard.get("n_pids", 0)) for shard in meta.get("failed_shards", []) or []
        )
        if failed:
            text += f", {failed:,} could not be scored"
        scanned = meta.get("preset")
        if scanned and self._preset and scanned != self._preset:
            text += f" (scanned with preset {scanned}; rule names use {self._preset})"
        return text + "."

    def _sample_statement(self) -> str:
        count = 0 if self._cases is None else len(self._cases)
        indexed = self._indexed_count()
        text = f"Loaded sample: {_plural(count, 'analyzed case')}"
        if indexed is not None and indexed >= count > 0:
            text += f" of {indexed:,} indexed surgeries"
        return text + "."

    def _fill(self) -> None:
        has_scan = self._scan is not None
        count = 0 if self._cases is None else len(self._cases)
        self.sample_tab.setText(f"Analyzed {count:,}")
        self.sample_tab.setAccessibleName(f"Analyzed {count:,}")
        self.sample_tab.setToolTip("The analyzed cases of the loaded sample")
        indexed = self._indexed_count()
        scored = len(self._scan.cases) if self._scan is not None else 0
        scan_text = f"Indexed {indexed:,}" if indexed is not None else f"Scanned {scored:,}"
        self.scan_tab.setText(scan_text)
        # the label gives the indexed count; what the tab lists is the cases the scan scored, so a screen reader hears that
        self.scan_tab.setAccessibleName(
            f"Full-EMR scan, {scored:,} scored of {indexed:,} indexed" if indexed is not None
            else f"Full-EMR scan, {scored:,} scored")
        self.scan_tab.setToolTip("The cases the full-EMR scan scored")
        self.scan_tab.setVisible(has_scan)
        # A lone tab is a label for the sample's size, not a choice, so it is not a stop on the Tab path
        policy = Qt.FocusPolicy.StrongFocus if has_scan else Qt.FocusPolicy.NoFocus
        self.sample_tab.setFocusPolicy(policy)
        self.scan_tab.setFocusPolicy(policy)
        for tab, scope in ((self.sample_tab, SCOPE_SAMPLE), (self.scan_tab, SCOPE_SCAN)):
            tab.blockSignals(True)
            tab.setChecked(self._scope == scope)
            tab.blockSignals(False)
        self._show_scope()

    def _tab_toggled(self, scope: str, checked: bool) -> None:
        if not checked or scope == self._scope:
            return
        self._scope = scope
        self.set_message("")
        self._show_scope()

    def _show_scope(self) -> None:
        scan = self._scan_on_screen()
        frame = self._scan.cases if scan and self._scan is not None else self._cases
        self._model.set_frame(frame)
        empty = self._model.rowCount() == 0
        if scan:
            self.empty_label.setText(
                "The full-EMR scan scored no cases. Press Ctrl+K and choose Scan full EMR again after "
                "checking the EMR folder in Data setup."
            )
        else:
            self.empty_label.setText(EMPTY_MESSAGE)
        self.scope_label.setText(self._scan_statement() if scan else self._sample_statement())
        self._update_charts()
        self.charts_panel.setVisible(not empty)
        self.empty_label.setVisible(empty)
        self._measure_columns()
        self._refresh_view()
        self._reflow()
        self._layout_columns()

    def _update_charts(self) -> None:
        """The charts describe every case in the scope on screen; the filter narrows the list, not the charts."""
        scan = self._scan_on_screen()
        cases = self._scan.cases if scan and self._scan is not None else self._cases
        flags = self._scan.episodes if scan and self._scan is not None else self._flags
        if flags is not None and not flags.empty and "PID" in flags and cases is not None and "PID" in cases:
            flags = flags[flags["PID"].isin(cases["PID"])]
        labels = self._model.labels
        self.score_block.set_spec(score_spec(cases))
        self.rules_block.set_spec(rules_spec(flags, labels, minutes_column="duration_min" if scan else None))
        self.duration_block.set_spec(duration_spec(cases))
        self._set_tab_order()

    def _set_tab_order(self) -> None:
        """Tab follows the screen: back, scope, filter, the charts that hold a picture, the list, then Open case.

        Run after the charts are set, because a widget that cannot take focus is out of the focus chain until it can.
        """
        order = [self.back_button, self.sample_tab, self.scan_tab, self.filter_edit,
                 self.score_block.chart, self.rules_block.chart, self.duration_block.chart,
                 self.table, self.preview.open_button]
        for first, second in zip(order, order[1:], strict=False):
            self.setTabOrder(first, second)

    def refresh_charts(self) -> None:
        """Draw the three charts again in the current theme."""
        for block in (self.score_block, self.rules_block, self.duration_block):
            block.refresh()

    # -- filter, summary and rows in view ---------------------------------------------------------------

    def _filter_changed(self, _text: str) -> None:
        if self._model.rowCount() > FILTER_PAUSE_ROWS:
            self._filter_timer.start()
        else:
            self._apply_filter()

    def _apply_filter(self) -> None:
        """Filter by the text now in the field; the words on screen always describe the rows of the last pass."""
        self._filter_timer.stop()
        self._applied_filter = self.filter_edit.text().strip()
        self._proxy.set_filter_text(self._applied_filter)
        self._refresh_view()

    def _refresh_view(self) -> None:
        """Everything that depends on which rows are in view: the current row, the bars, the words and the preview."""
        shown, total = self._proxy.rowCount(), self._model.rowCount()
        self.table.setVisible(shown > 0)
        self.no_match_label.setVisible(total > 0 and shown == 0)
        if total and not shown:
            needle = self._applied_filter
            self.no_match_label.setText(
                f"No case matches “{needle}”. Clear the filter to see all {total:,} cases."
            )
        if shown and not self.table.currentIndex().isValid():
            self.table.selectRow(0)
        scores = [self._proxy.index(row, SCORE_COLUMN).data(SORT_ROLE) for row in range(shown)]
        self._delegate.set_peak(max((v for v in scores if v is not None), default=0.0))
        self.table.viewport().update()
        filtering = bool(self._proxy.filter_text())
        self.filter_count_label.setVisible(filtering and total > 0)
        self.filter_count_label.setText(
            f"{shown:,} of {total:,} shown. The charts above still count all {total:,}." if filtering else ""
        )
        self._refresh_summary()
        self._update_preview()

    def _sort_changed(self, section: int, _order: Qt.SortOrder) -> None:
        """A sort on a field the table does not show falls back to the anomaly score, highest first."""
        if not 0 <= section < len(TABLE_COLUMNS):
            self.table.sortByColumn(SCORE_COLUMN, Qt.SortOrder.DescendingOrder)
            return
        self._refresh_summary()

    def _sort_phrase(self) -> str:
        header = self.table.horizontalHeader()
        column = TABLE_COLUMNS[header.sortIndicatorSection()]
        descending = header.sortIndicatorOrder() == Qt.SortOrder.DescendingOrder
        phrase = f"sorted by {_sort_name(column.title)}"
        if descending != column.first_sort_descending:
            if column.kind == "number":
                phrase += ", highest first" if descending else ", lowest first"
            else:
                phrase += ", Z to A"
        return phrase

    def _refresh_summary(self, *_args: Any) -> None:
        scan = self._scan_on_screen()
        noun = "scored case" if scan else "analyzed case"
        shown, total = self._proxy.rowCount(), self._model.rowCount()
        if not total:
            self.summary_label.setText("No cases in the full-EMR scan." if scan else "No analyzed cases yet.")
            return
        needle = self._applied_filter
        if not shown:
            self.summary_label.setText(f"0 of {_plural(total, noun)} match “{needle}”.")
            return
        flagged = sum(
            self._model.has_episodes(self._proxy.mapToSource(self._proxy.index(row, 0)).row())
            for row in range(shown)
        )
        what = noun if shown == 1 else f"{noun}s"
        if needle:
            what += f" matching “{needle}”"
        verb = "has" if flagged == 1 else "have"
        self.summary_label.setText(f"{flagged:,} of {shown:,} {what} {verb} rule episodes · {self._sort_phrase()}")

    def _measure_columns(self) -> None:
        """What each column needs for the rows loaded: its whole text, and its heading whole or one word to a line."""
        body = tabular(QFont(self.table.font()))
        body.setPixelSize(13)
        semibold = QFont(body)
        semibold.setWeight(QFont.Weight.DemiBold)
        heading = QFont(self.table.horizontalHeader().font())
        heading.setPixelSize(13)
        heading.setWeight(QFont.Weight.DemiBold)
        body_metrics, semibold_metrics, heading_metrics = QFontMetrics(body), QFontMetrics(semibold), QFontMetrics(heading)
        ideal: list[int] = []
        least: list[int] = []
        full: list[int] = []
        whole_heading: list[int] = []
        for index, column in enumerate(TABLE_COLUMNS):
            number = column.kind == "number"
            heading_pad = 2 * CELL_PADDING + SORT_ARROW_ROOM
            whole = heading_metrics.horizontalAdvance(column.title) + heading_pad
            word = max(heading_metrics.horizontalAdvance(w) for w in column.title.split()) + heading_pad
            full.append(whole)
            if index in _FLEXIBLE:
                ideal.append(max(_FLEX_IDEAL[index], whole))
                least.append(max(_FLEX_LEAST, word))
                whole_heading.append(least[-1])
                continue
            # two spare pixels: the ellipsis test of the painter measures a hair wider than the layout does
            pad = 2 * CELL_PADDING + (SORT_ARROW_ROOM if number else 0) + 2
            texts = self._model.shown_texts(index)
            if index == SCORE_COLUMN:
                number_width = max((semibold_metrics.horizontalAdvance(t) for t in texts), default=0) + 8
                cell_ideal, cell_least = number_width + SCORE_BAR_WIDTH + pad, number_width + SCORE_BAR_LEAST + pad
            else:
                cell_ideal = cell_least = max((body_metrics.horizontalAdvance(t) for t in texts), default=0) + pad
            ideal.append(max(cell_ideal, whole) + 8)
            least.append(max(cell_least, word))
            whole_heading.append(max(least[-1], whole))
        # least, then the flexible columns to a readable width, then every heading on one line, then everything
        readable = [min(_FLEX_READABLE[i], ideal[i]) if i in _FLEXIBLE else least[i] for i in range(len(least))]
        readable = [max(r, floor) for r, floor in zip(readable, least, strict=True)]
        headings = [max(r, h) for r, h in zip(readable, whole_heading, strict=True)]
        ideal = [max(i, h) for i, h in zip(ideal, headings, strict=True)]
        self._tiers = [least, readable, headings, ideal]
        self._full = full
        self.table.setMinimumWidth(sum(least) + TABLE_CHROME)

    def _layout_columns(self, *_args: Any) -> None:
        """Fit the nine columns into the list's width: Procedure and Top rules shorten first, the rest keep their text."""
        available = self.table.viewport().width()
        if not self._full or available <= 0:
            return
        widths = plan_column_widths(available, self._tiers, _FLEXIBLE)
        for column, width in enumerate(widths):
            if self.table.columnWidth(column) != width:
                self.table.setColumnWidth(column, width)
        wrapped = any(width < whole for width, whole in zip(widths, self._full, strict=True))
        height = HEADER_HEIGHT_WRAPPED if wrapped else HEADER_HEIGHT
        header = self.table.horizontalHeader()
        if header.height() != height:
            header.setFixedHeight(height)

    # -- the preview ------------------------------------------------------------------------------------

    def _update_preview(self) -> None:
        if not self._proxy.rowCount():
            self.preview.show_message(NO_CASES_TO_PREVIEW)
            return
        index = self.table.currentIndex()
        if not index.isValid():
            self.preview.show_message(NOTHING_TO_PREVIEW)
            return
        source = self._proxy.mapToSource(self._proxy.index(index.row(), 0))
        row = self._model.row_values(source.row())
        pid = str(row["PID"])
        episodes = self._preview_source.episodes(pid, self._model.labels, scan=self._scan_on_screen())
        facts = []
        duration, age, sex = row.get("case_duration_min"), row.get("Age"), row.get("Gender")
        if duration is not None:
            facts.append(f"{duration:g} min surgery")
        if age is not None:
            facts.append(f"age {int(age) if float(age).is_integer() else age:g}")
        if sex:
            facts.append(str(sex))
        if episodes is not None:
            facts.append(f"{len(episodes)} rule episode{'s' if len(episodes) != 1 else ''}" if episodes
                         else "no rule episodes")
        fields = tuple(
            (column.title, self._field_text(column, row.get(column.key)))
            for column in PREVIEW_FIELDS if self._model.has_field(column.key)
        )
        self.preview.show_case(PreviewCase(
            pid, str(row.get("Procedure_short") or ""), " · ".join(facts),
            self._preview_source.traces(pid), episodes, fields,
        ))

    @staticmethod
    def _field_text(column: Column, value: Any) -> str:
        if value is None or value == "":
            return "missing"
        return _number_text(value) if column.kind == "number" else str(value)

    def _open_current(self) -> None:
        index = self.table.currentIndex()
        if index.isValid():
            self._activate_row(index.row())

    def _activate_row(self, proxy_row: int) -> None:
        source = self._proxy.mapToSource(self._proxy.index(proxy_row, 0))
        if source.isValid():
            self.case_requested.emit(self._model.pid_at(source.row()))

    def visible_rows(self, limit: int = 5) -> list[dict[str, Any]]:
        """First rows in the order shown, for tests and the desktop driver."""
        rows = []
        for proxy_row in range(min(limit, self._proxy.rowCount())):
            source = self._proxy.mapToSource(self._proxy.index(proxy_row, 0))
            rows.append(self._model.row_values(source.row()))
        return rows

    def cell_text(self, proxy_row: int, column: int) -> str:
        return str(self._proxy.index(proxy_row, column).data() or "")

    def shown_text(self, proxy_row: int, column: int) -> str:
        """What the cell draws: its text, cut with an ellipsis if the column is too narrow for it."""
        index = self._proxy.index(proxy_row, column)
        font = self.table.fontMetrics()
        if column == SCORE_COLUMN:
            bold = QFont(self.table.font())
            bold.setWeight(QFont.Weight.DemiBold)
            font = QFontMetrics(bold)
        return self._delegate.shown(str(index.data() or ""), font, column, self.table.columnWidth(column))
