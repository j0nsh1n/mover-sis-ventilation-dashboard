"""Sortable list of the processed cases, opened from the research workspace header."""

from __future__ import annotations

import re
from collections.abc import Mapping
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
from src.desktop.shell import nav_icon
from src.desktop.theme import active_theme, token_color

SCOPE_SAMPLE = "sample"
SCOPE_SCAN = "scan"

EMPTY_MESSAGE = "No processed cases yet. Run Data setup or a full-EMR scan."

# Page width from which the preview sits beside the list; below it the preview goes under the list, so the
# page never scrolls sideways beside the open Rules drawer or at the window's least width.
SIDE_BY_SIDE_WIDTH = 980
# List-column widths below which the header controls and the three charts stack instead of sitting in a row.
HEADER_ROW_WIDTH = 760
CHARTS_ROW_WIDTH = 600
ROW_HEIGHT = 33
CELL_PADDING = 10
# Room at the right of a number column's heading and cells for the sort arrow, so the arrow never covers a letter
SORT_ARROW_ROOM = 16
SCORE_BAR_WIDTH = 56
SCORE_BAR_HEIGHT = 5
SELECTED_BAR = 3
SORT_ROLE = Qt.ItemDataRole.UserRole
_NO_PARENT = QModelIndex()


@dataclass(frozen=True)
class Column:
    key: str
    title: str
    kind: str  # "text" or "number"
    tooltip: str = ""
    first_sort_descending: bool = False


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
    Column("primary_agent_name", "Primary agent", "text"),
    Column("median_TV", "Median TV (mL)", "number"),
    Column("median_PIP", "Median PIP (cmH₂O)", "number"),
    Column("median_ETCO2", "Median ETCO₂ (mmHg)", "number"),
)
SCORE_COLUMN = next(i for i, c in enumerate(COLUMNS) if c.key == "anomaly_score")
_COLUMN_INDEX = {column.key: i for i, column in enumerate(COLUMNS)}
_COUNT_COLUMNS = (_COLUMN_INDEX["n_warn"], _COLUMN_INDEX["n_critical"])
_MUTED_COLUMNS = (_COLUMN_INDEX["top_rules"], _COLUMN_INDEX["primary_agent_name"])
# Column widths for the page's text at its size; a heading that needs more than this widens its column.
_COLUMN_WIDTHS = (148, 200, 60, 60, 124, 150, 110, 120, 440, 150, 150, 180, 190)
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
        if frame is None or frame.empty or "PID" not in frame:
            self._cells = [[] for _ in COLUMNS]
            self._count = 0
            self._flagged = []
        else:
            ordered = frame.assign(_pid=frame["PID"].astype(str)).sort_values("_pid", kind="stable")
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
        return 0 if parent.isValid() else len(COLUMNS)

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
        if orientation != Qt.Orientation.Horizontal or not 0 <= section < len(COLUMNS):
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
            return int(side | Qt.AlignmentFlag.AlignVCenter)
        return None


class _CasesProxy(QSortFilterProxyModel):
    """Equal values break by PID ascending in both directions, so repeated sorts do not reshuffle.

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

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex | QPersistentModelIndex) -> bool:  # noqa: N802
        if not self._needle:
            return True
        source = self.sourceModel()
        assert isinstance(source, _CasesModel)
        return any(self._needle in field for field in source.search_fields(source_row))

    def lessThan(self, left: QModelIndex | QPersistentModelIndex, right: QModelIndex | QPersistentModelIndex) -> bool:  # noqa: N802
        a, b = left.data(SORT_ROLE), right.data(SORT_ROLE)
        if a is not None and b is not None and a != b:
            return bool(a < b)
        if (a is None) != (b is None):
            return a is None
        source = self.sourceModel()
        first, second = source.index(left.row(), 0).data(SORT_ROLE), source.index(right.row(), 0).data(SORT_ROLE)
        # Qt flips the comparison for a descending sort, so flip the tie-break back
        ascending = self.sortOrder() == Qt.SortOrder.AscendingOrder
        return bool(first < second) if ascending else bool(first > second)


class _CasesTable(QTableView):
    """Emits ``row_activated`` once per opening gesture: Enter, and a click or a double-click as ``CLICK_OPENS`` says."""

    row_activated = Signal(int)

    # The one place that decides what a click does. True: a click opens the row's case, as the spec says today.
    # False: a click only selects the row, so the preview follows it, and a double-click or Enter opens it.
    CLICK_OPENS = True

    def __init__(self, parent=None):
        super().__init__(parent)
        self._double_click_pending = False

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        # Qt sends press, release, double-click, release; with CLICK_OPENS the first release already opened the row
        self._double_click_pending = True
        super().mouseDoubleClickEvent(event)
        index = self.indexAt(event.position().toPoint())
        if not self.CLICK_OPENS and index.isValid() and event.button() == Qt.MouseButton.LeftButton:
            self.row_activated.emit(index.row())

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        super().mouseReleaseEvent(event)
        if self._double_click_pending:
            self._double_click_pending = False
            return
        index = self.indexAt(event.position().toPoint())
        if self.CLICK_OPENS and index.isValid() and event.button() == Qt.MouseButton.LeftButton:
            self.row_activated.emit(index.row())

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.currentIndex().isValid():
            self.row_activated.emit(self.currentIndex().row())
            event.accept()
            return
        super().keyPressEvent(event)


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
        shown = painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, right - left)
        painter.drawText(left, rect.top(), right - left, rect.height(), flags, shown)
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
        self._header_row: bool | None = None
        self._charts_row: bool | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        # The whole page scrolls when the window is shorter than its parts; the list keeps its own scrolling
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
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
        self._proxy.setSortRole(SORT_ROLE)
        self.table = _CasesTable()
        self.table.setObjectName("casesTable")
        self.table.setAccessibleName("Cases")
        self.table.setAccessibleDescription(
            "Click a column heading to sort. Enter opens the current case; the preview beside it follows the arrow keys."
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
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setMinimumHeight(240)
        self._delegate = _CasesDelegate(self.table)
        self.table.setItemDelegate(self._delegate)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setMinimumSectionSize(56)
        header.setSortIndicatorClearable(False)
        header.setFixedHeight(34)
        self.table.row_activated.connect(self._activate_row)
        header.sortIndicatorChanged.connect(self._refresh_summary)
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

    def _reflow(self) -> None:
        wide = self.width() >= SIDE_BY_SIDE_WIDTH
        if wide != self._wide:
            self._wide = wide
            self._body.setDirection(QBoxLayout.Direction.LeftToRight if wide else QBoxLayout.Direction.TopToBottom)
            self._column.setContentsMargins(*((32, 16, 28, 20) if wide else (32, 16, 32, 24)))
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
        self.scan_tab.setAccessibleName(scan_text)
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
                "The full-EMR scan scored no cases. Run File, Scan full EMR again after "
                "checking the EMR folder in Data setup."
            )
        else:
            self.empty_label.setText(EMPTY_MESSAGE)
        self.scope_label.setText(self._scan_statement() if scan else self._sample_statement())
        self._update_charts()
        self.charts_panel.setVisible(not empty)
        self.empty_label.setVisible(empty)
        if not empty:
            self._size_columns()
        self._refresh_view()

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

    def _filter_changed(self, text: str) -> None:
        self._proxy.set_filter_text(text)
        self._refresh_view()

    def _refresh_view(self) -> None:
        """Everything that depends on which rows are in view: the current row, the bars, the words and the preview."""
        shown, total = self._proxy.rowCount(), self._model.rowCount()
        self.table.setVisible(shown > 0)
        self.no_match_label.setVisible(total > 0 and shown == 0)
        if total and not shown:
            needle = self.filter_edit.text().strip()
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

    def _sort_phrase(self) -> str:
        header = self.table.horizontalHeader()
        column = COLUMNS[header.sortIndicatorSection()]
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
        needle = self.filter_edit.text().strip()
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

    def _size_columns(self) -> None:
        header = self.table.horizontalHeader()
        font = QFont(header.font())
        font.setPixelSize(13)
        font.setWeight(QFont.Weight.DemiBold)
        metrics = QFontMetrics(font)
        for column, width in enumerate(_COLUMN_WIDTHS):
            # a heading keeps its whole name, with room for its padding and the sort arrow
            needed = metrics.horizontalAdvance(COLUMNS[column].title) + CELL_PADDING + SORT_ARROW_ROOM + 12
            self.table.setColumnWidth(column, max(width, needed))

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
        self.preview.show_case(PreviewCase(
            pid, str(row.get("Procedure_short") or ""), " · ".join(facts),
            self._preview_source.traces(pid), episodes,
        ))

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
