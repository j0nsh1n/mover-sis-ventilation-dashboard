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
    QSize,
    QSortFilterProxyModel,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from src.pipeline.corpus_scan import ScanResult
    from src.rules import RuleCatalog

from src.desktop.charts import ChartView, flag_rules_figure, top_cases_figure

SCOPE_SAMPLE = "sample"
SCOPE_SCAN = "scan"

EMPTY_MESSAGE = "No processed cases yet. Run Data setup or a full-EMR scan."

# Lower than the default so a chart's text stays small enough for fifteen rows in its area
CHART_FIT_DPI = 100
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

_RULE_ENTRY = re.compile(r"^\s*([^\s(),]+)\s*(?:\((\d+)\))?\s*$")


def _number_text(value: float) -> str:
    return f"{int(value)}" if float(value).is_integer() else f"{value:.1f}"


class _CasesModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._cells: list[list[Any]] = [[] for _ in COLUMNS]
        self._count = 0
        self._labels: dict[str, str] = {}
        self._rule_text: dict[str, str] = {}

    def set_labels(self, labels: Mapping[str, str]) -> None:
        self.beginResetModel()
        self._labels = dict(labels)
        self._rule_text = {}
        self.endResetModel()

    def set_frame(self, frame: pd.DataFrame | None) -> None:
        self.beginResetModel()
        if frame is None or frame.empty or "PID" not in frame:
            self._cells = [[] for _ in COLUMNS]
            self._count = 0
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
        self.endResetModel()

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
        return None


class _CasesProxy(QSortFilterProxyModel):
    """Equal values break by PID ascending in both directions, so repeated sorts do not reshuffle."""

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
    """Emits ``row_activated`` once per click or Enter; a double-click is one action, not two."""

    row_activated = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._double_click_pending = False

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        # Qt sends press, release, double-click, release; the first release already opened the row
        self._double_click_pending = True
        super().mouseDoubleClickEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        super().mouseReleaseEvent(event)
        if self._double_click_pending:
            self._double_click_pending = False
            return
        index = self.indexAt(event.position().toPoint())
        if index.isValid() and event.button() == Qt.MouseButton.LeftButton:
            self.row_activated.emit(index.row())

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.currentIndex().isValid():
            self.row_activated.emit(self.currentIndex().row())
            event.accept()
            return
        super().keyPressEvent(event)


STYLE = """
QWidget#casesPage { background: #ebe9e3; }
QWidget#casesPage QLabel { color: #302b35; }
QWidget#casesPage QLabel#casesTitle { font-family: Georgia; font-size: 28px; color: #302637; }
QWidget#casesPage QLabel#casesScope { font-weight: bold; }
QWidget#casesPage QLabel#casesNote { color: #5a4f63; }
QWidget#casesPage QLabel#casesEmpty { background: #fbfaf6; border: 1px dashed #9e93a3;
    border-radius: 9px; padding: 28px; font-size: 15px; }
QWidget#casesPage QPushButton#casesBack { background: #f8f7f3; color: #4b3d5d;
    border: 1px solid #b4afba; border-radius: 5px; padding: 8px 12px; min-height: 28px; }
QWidget#casesPage QPushButton#casesBack:focus { border: 2px solid #4b3d5d; }
QWidget#casesPage QComboBox { background: #fffefa; color: #302b35; border: 1px solid #b4afba;
    border-radius: 5px; padding: 6px 10px; min-height: 28px; }
QWidget#casesPage QComboBox:focus { border: 2px solid #4b3d5d; }
QWidget#casesPage QComboBox QAbstractItemView { background: #fffefa; color: #302b35;
    selection-background-color: #332941; selection-color: #fff9f4; }
QWidget#casesPage QTableView { background: #fffefa; color: #302b35; alternate-background-color: #f6f3f5;
    gridline-color: #ddd6dd; border: 1px solid #ddd6dd; selection-background-color: #332941;
    selection-color: #fff9f4; }
QWidget#casesPage QTableView:focus { border: 2px solid #4b3d5d; }
QWidget#casesPage QTableView::item:selected { background: #332941; color: #fff9f4; }
QWidget#casesPage QTableView::item:selected:!active { background: #332941; color: #fff9f4; }
/* the sort arrow takes this colour; the app palette's text is near-white in the dark theme */
QWidget#casesPage QHeaderView { color: #4d4154; }
QWidget#casesPage QHeaderView::section { background: #f1ecef; color: #4d4154; border: 0;
    border-bottom: 1px solid #d7ccd9; padding: 7px; font-weight: bold; }
"""


class CasesOverview(QWidget):
    case_requested = Signal(str)
    back_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("casesPage")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(STYLE)
        self._cases: pd.DataFrame | None = None
        self._scan: ScanResult | None = None
        self._scope = SCOPE_SAMPLE
        self._preset: str | None = None
        self._flags: pd.DataFrame | None = None
        self._charts_dirty = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 20, 34, 16)
        layout.setSpacing(10)
        top = QHBoxLayout()
        self.back_button = QPushButton("← Back to question")
        self.back_button.setAutoDefault(True)
        self.back_button.setObjectName("casesBack")
        self.back_button.setAccessibleName("Back to the research question")
        self.back_button.clicked.connect(self.back_requested)
        top.addWidget(self.back_button)
        top.addStretch(1)
        self.scope_combo = QComboBox()
        self.scope_combo.setAccessibleName("Case scope")
        self.scope_combo.currentIndexChanged.connect(self._scope_changed)
        top.addWidget(self.scope_combo)
        layout.addLayout(top)
        title = QLabel("Cases")
        title.setObjectName("casesTitle")
        layout.addWidget(title)
        self.scope_label = QLabel()
        self.scope_label.setObjectName("casesScope")
        self.scope_label.setWordWrap(True)
        layout.addWidget(self.scope_label)

        self.charts_button = QPushButton("Charts ▸")
        self.charts_button.setAutoDefault(True)
        self.charts_button.setCheckable(True)
        self.charts_button.setAccessibleName("Charts")
        self.charts_button.clicked.connect(self._toggle_charts)
        layout.addWidget(self.charts_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.charts_panel = QWidget()
        charts = QHBoxLayout(self.charts_panel)
        charts.setContentsMargins(0, 0, 0, 0)
        self.top_chart = ChartView()
        self.rules_chart = ChartView()
        # Tall enough for fifteen bars, or twenty rotated rule names, at their normal text size
        self.top_chart.setMinimumHeight(320)
        self.rules_chart.setMinimumHeight(320)
        self.top_chart.setAccessibleName("Top cases by anomaly score chart")
        self.rules_chart.setAccessibleName("Flag counts by rule chart")
        self._chart_sizes: tuple[QSize, QSize] | None = None
        self._chart_resize_timer = QTimer(self)
        self._chart_resize_timer.setSingleShot(True)
        self._chart_resize_timer.setInterval(120)
        self._chart_resize_timer.timeout.connect(self._refit_charts)
        self.top_chart.resized.connect(self._chart_resize_timer.start)
        self.rules_chart.resized.connect(self._chart_resize_timer.start)
        charts.addWidget(self.top_chart, 1)
        charts.addWidget(self.rules_chart, 1)
        self.charts_panel.hide()
        layout.addWidget(self.charts_panel)
        self.charts_note = QLabel()
        self.charts_note.setWordWrap(True)
        self.charts_note.hide()
        layout.addWidget(self.charts_note)

        self._model = _CasesModel(self)
        self._proxy = _CasesProxy(self)
        self._proxy.setSourceModel(self._model)
        self._proxy.setSortRole(SORT_ROLE)
        self.table = _CasesTable()
        self.table.setObjectName("casesTable")
        self.table.setAccessibleName("Cases")
        self.table.setModel(self._proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.setWordWrap(False)
        # Tab must leave the table, or the scope switch and Back are unreachable from it
        self.table.setTabKeyNavigation(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setMinimumSectionSize(70)
        header.setSortIndicatorClearable(False)
        self.table.row_activated.connect(self._activate_row)
        layout.addWidget(self.table, 1)
        self.empty_label = QLabel(EMPTY_MESSAGE)
        self.empty_label.setObjectName("casesEmpty")
        self.empty_label.setWordWrap(True)
        layout.addWidget(self.empty_label)
        layout.addStretch(0)
        self.status_label = QLabel()
        self.status_label.setObjectName("casesNote")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.hint_label = QLabel(
            "Click a row or press Enter to open the case. Click a column heading to sort. Escape goes back."
        )
        self.hint_label.setObjectName("casesNote")
        self.hint_label.setWordWrap(True)
        layout.addWidget(self.hint_label)

        self.setTabOrder(self.back_button, self.scope_combo)
        self.setTabOrder(self.scope_combo, self.charts_button)
        self.setTabOrder(self.charts_button, self.top_chart._scroll)
        self.setTabOrder(self.top_chart._scroll, self.rules_chart._scroll)
        self.setTabOrder(self.rules_chart._scroll, self.table)
        self.table.sortByColumn(SCORE_COLUMN, Qt.SortOrder.DescendingOrder)
        self._fill()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        # The table and combo box ignore Escape, so it reaches the page from any child
        if event.key() == Qt.Key.Key_Escape:
            self.back_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def set_data(
        self,
        cases: pd.DataFrame | None,
        scan: ScanResult | None,
        catalog: RuleCatalog | None,
        flags: pd.DataFrame | None = None,
    ) -> None:
        """Replace everything shown; rows from before this call are never kept."""
        self._cases = cases
        self._scan = scan
        self._flags = flags
        self._preset = catalog.preset if catalog is not None else None
        self._model.set_labels(
            {spec.id: spec.label for spec in catalog.specs} if catalog is not None else {}
        )
        if scan is None:
            self._scope = SCOPE_SAMPLE
        self.status_label.setText("")
        self._fill()

    def set_message(self, text: str) -> None:
        self.status_label.setText(text)

    def scope(self) -> str:
        return self._scope

    def row_for(self, pid: str) -> dict[str, Any] | None:
        for row in range(self._model.rowCount()):
            if self._model.pid_at(row) == pid:
                return self._model.row_values(row)
        return None

    def _scan_meta(self) -> Mapping[str, Any]:
        return self._scan.meta if self._scan is not None else {}

    def _scan_label(self) -> str:
        meta = self._scan_meta()
        scored = len(self._scan.cases) if self._scan is not None else 0
        label = f"Full-EMR scan, {scored:,} scored"
        indexed, ventilated = meta.get("n_indexed_surgeries"), meta.get("n_ventilated_surgeries")
        if isinstance(indexed, int) and isinstance(ventilated, int) and indexed >= ventilated:
            label += f", {indexed - ventilated:,} without ventilator data"
        failed = sum(
            int(shard.get("n_pids", 0)) for shard in meta.get("failed_shards", []) or []
        )
        if failed:
            label += f", {failed:,} could not be scored"
        return label

    def _sample_label(self) -> str:
        count = 0 if self._cases is None else len(self._cases)
        indexed = self._scan_meta().get("n_indexed_surgeries")
        label = f"Analyzed cases, {count:,}"
        if isinstance(indexed, int) and indexed >= count > 0:
            label += f" of {indexed:,} indexed surgeries"
        return label + " (loaded sample)"

    def _fill(self) -> None:
        has_scan = self._scan is not None
        self.scope_combo.blockSignals(True)
        self.scope_combo.clear()
        self.scope_combo.addItem(self._sample_label(), SCOPE_SAMPLE)
        if has_scan:
            self.scope_combo.addItem(self._scan_label(), SCOPE_SCAN)
            if self._scope == SCOPE_SCAN:
                self.scope_combo.setCurrentIndex(1)
        self.scope_combo.blockSignals(False)
        self.scope_combo.setVisible(has_scan)
        self._show_scope()

    def _scope_changed(self, index: int) -> None:
        self._scope = self.scope_combo.itemData(index) or SCOPE_SAMPLE
        self.status_label.setText("")
        self._show_scope()

    def _show_scope(self) -> None:
        scan = self._scope == SCOPE_SCAN and self._scan is not None
        frame = self._scan.cases if scan and self._scan is not None else self._cases
        self._model.set_frame(frame)
        empty = self._model.rowCount() == 0
        if scan:
            label = self._scan_label()
            scanned = self._scan_meta().get("preset")
            if scanned and self._preset and scanned != self._preset:
                label += f" (scanned with preset {scanned}; rule names use {self._preset})"
            self.empty_label.setText(
                "The full-EMR scan scored no cases. Run File, Scan full EMR again after "
                "checking the EMR folder in Data setup."
            )
        else:
            label = self._sample_label()
            self.empty_label.setText(EMPTY_MESSAGE)
        flagged = 0
        maximum = "—"
        if frame is not None and not frame.empty:
            warn = pd.to_numeric(frame.get("n_warn", pd.Series(0, index=frame.index)), errors="coerce").fillna(0)
            critical = pd.to_numeric(frame.get("n_critical", pd.Series(0, index=frame.index)), errors="coerce").fillna(0)
            flagged = int(((warn + critical) > 0).sum())
            if "anomaly_score" in frame and frame["anomaly_score"].notna().any():
                maximum = _number_text(float(frame["anomaly_score"].max()))
        self.scope_label.setText(f"{label} · with flags: {flagged:,} · max score: {maximum}")
        self.refresh_charts()
        self.table.setVisible(not empty)
        self.empty_label.setVisible(empty)
        self.hint_label.setVisible(not empty)
        if not empty:
            self._size_columns()

    def _size_columns(self) -> None:
        widths = (130, 200, 56, 56, 112, 120, 96, 108, 460, 150, 140, 170, 170)
        for column, width in enumerate(widths):
            self.table.setColumnWidth(column, width)

    def _toggle_charts(self, expanded: bool) -> None:
        self.charts_button.setText("Charts ▾" if expanded else "Charts ▸")
        self.charts_panel.setVisible(expanded)
        self.charts_note.setVisible(expanded)
        if expanded:
            # The charts are sized to their area, which only exists once the layout has run
            if (page := self.layout()) is not None:
                page.activate()
            self._render_charts()

    def _refit_charts(self) -> None:
        sizes = (self.top_chart.size(), self.rules_chart.size())
        if self.charts_panel.isVisible() and sizes != self._chart_sizes:
            self._charts_dirty = True
            self._render_charts()

    def refresh_charts(self) -> None:
        self._charts_dirty = True
        if self.charts_button.isChecked() and self.isVisible():
            self._render_charts()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self.charts_button.isChecked():
            self._render_charts()

    def _render_charts(self) -> None:
        if not self._charts_dirty:
            return
        scan = self._scope == SCOPE_SCAN and self._scan is not None
        cases = self._scan.cases if scan and self._scan is not None else self._cases
        flags = self._scan.episodes if scan and self._scan is not None else self._flags
        cases = cases if cases is not None else pd.DataFrame()
        flags = flags if flags is not None else pd.DataFrame()
        if not flags.empty and "PID" in flags and "PID" in cases:
            flags = flags[flags["PID"].isin(cases["PID"])]
        if cases.empty or "anomaly_score" not in cases:
            self.top_chart.clear("No scored cases loaded.")
        else:
            self.top_chart.set_figure(
                top_cases_figure(cases, n=min(15, len(cases)), figsize=self.top_chart.figure_size(CHART_FIT_DPI)),
                CHART_FIT_DPI)
        self.rules_chart.set_figure(flag_rules_figure(
            flags, count_column="duration_min" if scan else None,
            figsize=self.rules_chart.figure_size(CHART_FIT_DPI)), CHART_FIT_DPI)
        self._chart_sizes = (self.top_chart.size(), self.rules_chart.size())
        self.charts_note.setText(
            "Full-EMR scan. Rule counts sum flagged-minute durations from scan episodes."
            if scan else "Loaded sample. Rule counts use recorded minute flags."
        )
        self._charts_dirty = False

    def focus_table(self) -> None:
        if self.table.isVisibleTo(self):
            self.table.setFocus()
            if not self.table.selectionModel().hasSelection() and self._proxy.rowCount():
                self.table.selectRow(0)
        else:
            self.back_button.setFocus()

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
