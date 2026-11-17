"""Show aligned drug and vital rows for one case or the fictional demo: a chart, and the same rows as a table."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
from PySide6.QtCore import QEvent, QItemSelectionModel, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QBoxLayout,
    QFrame,
    QHeaderView,
    QLabel,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from src.desktop.accessibility import announce
from src.desktop.marks import MissingCellDelegate, mark_missing
from src.desktop.theme import token_color
from src.modules.medication_vitals.align import DISCLAIMER, DisplayRow, align_rows, fictional_demo
from src.modules.medication_vitals.timeline import MEASUREMENT, TRACK_SPECS, build_timeline, cursor_text
from src.modules.medication_vitals.timeline_chart import MedicationChart

DEMO_ID = "FAKE-MED-DEMO"
OPEN_CASE_NOTE = "de-identified research data, not for patient care"
OPEN_CASE_DEMO_NOTE = "fictional demo data, not for patient care"
UNREADABLE = "The rows for this record could not be read, so none are shown."
NO_ROWS = "This record has no medication or vital rows."
_COLUMNS = ("Time", "Item", "Value", "Unit", "Kind", "Source", "Record note")
_ITEM_COLUMN = _COLUMNS.index("Item")
_VALUE_COLUMN = _COLUMNS.index("Value")
# The two ways the record says a value is absent: a vital with no sample, and a dose never written down.
_MISSING_VALUES = {"missing", "not recorded"}
# Page width below which the table keeps two columns, the item with its details beneath and the value, and the
# title and its view tabs stack. A window can be about 340 px wide, which leaves a page of about 276 px.
TABLE_COMPACT_BELOW = 960
HEADER_ROW_BELOW = 640
_VIEWS = ("Timeline and rows", "Rows only")
_SWATCH = (16, 3)


def _swatch(token: str) -> QIcon:
    """A short line in a track's colour, the same mark the chart puts beside the track's name."""
    pixmap = QPixmap(16, 16)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.fillRect(0, (16 - _SWATCH[1]) // 2, _SWATCH[0], _SWATCH[1], token_color(token))
    painter.end()
    return QIcon(pixmap)


class MedicationVitalsPanel(QWidget):
    _open_case = False

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("medication-vitals")
        self._rows: tuple[DisplayRow, ...] = ()
        self._row_minutes: list[int | None] = []
        self._items: list[QTableWidgetItem] = []
        self._compact: bool | None = None
        self._stacked: bool | None = None
        self._syncing = False

        self.case_line = QLabel()
        self.case_line.setObjectName("pathHint")
        self.case_line.setWordWrap(True)
        self.title_label = QLabel()
        self.title_label.setObjectName("caseTitle")
        self.title_label.setWordWrap(True)
        self.view_tabs = QTabBar()
        self.view_tabs.setAccessibleName("Medication and vitals view")
        self.view_tabs.setExpanding(False)
        self.view_tabs.setDrawBase(False)
        self.view_tabs.setUsesScrollButtons(False)
        self.view_tabs.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        for name in _VIEWS:
            self.view_tabs.addTab(name)
        self.view_tabs.currentChanged.connect(self._set_view)
        self.title_row = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.title_row.setSpacing(16)
        self.title_row.addWidget(self.title_label, 1)
        self.title_row.addWidget(self.view_tabs, 0, Qt.AlignmentFlag.AlignBottom)

        self.notice = QLabel(DISCLAIMER)
        self.notice.setObjectName("quietText")
        self.notice.setWordWrap(True)
        self.notice.setAccessibleDescription("Medication and vitals notice")
        self.note = QLabel()
        self.note.setObjectName("quietText")
        self.note.setWordWrap(True)
        self.note.setAccessibleDescription("Time alignment")
        # Said in a plain sentence when there is nothing to draw or the rows cannot be read.
        self.empty_label = QLabel()
        self.empty_label.setObjectName("bodyText")
        self.empty_label.setWordWrap(True)
        self.empty_label.hide()

        self.chart = MedicationChart()
        self.chart.cursor_changed.connect(self._cursor_moved)
        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setObjectName("medicationTable")
        self.table.setHorizontalHeaderLabels(list(_COLUMNS))
        self.table.setAccessibleName("Medication and vital rows")
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        # Arrow keys move between cells; Tab leaves the table.
        self.table.setTabKeyNavigation(False)
        self.table.setShowGrid(False)
        self.table.setWordWrap(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setItemDelegate(MissingCellDelegate(self.table))
        self.table.setMinimumHeight(240)
        self.table.currentCellChanged.connect(self._row_chosen)
        header = self.table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setStretchLastSection(True)

        column = QVBoxLayout()
        column.setContentsMargins(32, 20, 32, 20)
        column.setSpacing(12)
        column.addWidget(self.case_line)
        column.addLayout(self.title_row)
        column.addWidget(self.notice)
        column.addWidget(self.note)
        column.addWidget(self.empty_label)
        column.addWidget(self.chart)
        column.addWidget(self.table, 1)
        self.column = column
        body = QWidget()
        body.setObjectName("medicationBody")
        body.setLayout(column)
        self.page_scroll = QScrollArea()
        self.page_scroll.setWidgetResizable(True)
        self.page_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.page_scroll.setFrameShape(QFrame.Shape.NoFrame)
        # Tab goes from control to control; an unnamed scroll area is not a stop.
        self.page_scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.page_scroll.setWidget(body)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.page_scroll)
        QWidget.setTabOrder(self.view_tabs, self.chart)
        QWidget.setTabOrder(self.chart, self.table)

        self.show_fictional()

    # —— what the page shows ——

    def shows_open_case(self) -> bool:
        """Whether the page shows a case opened in Research, not its own invented record."""
        return self._open_case

    def show_fictional(self) -> None:
        medications, timeseries = fictional_demo()
        self.show_case(DEMO_ID, medications, timeseries, origin=None)

    def show_case(
        self,
        case_id: str,
        medications: pd.DataFrame | None,
        timeseries: pd.DataFrame | None,
        origin: datetime | None = None,
        *,
        fictional_source: bool = False,
    ) -> None:
        """Show one record. ``fictional_source`` says an open case comes from the app's fictional demo data."""
        demo = case_id == DEMO_ID
        self._open_case = not demo
        if demo:
            self.case_line.setText(f"{case_id} · invented record")
        elif fictional_source:
            self.case_line.setText(f"{case_id} · open case · {OPEN_CASE_DEMO_NOTE}")
        else:
            self.case_line.setText(f"{case_id} · open case · {OPEN_CASE_NOTE}")
        try:
            rows = align_rows(medications, timeseries, origin=origin)
        except (ValueError, TypeError, KeyError):
            self._show_nothing(UNREADABLE)
            return
        shared = any(row.time_text.startswith("minute ") for row in rows if row.provenance.endswith(".csv")) and any(
            row.kind == "measurement" and row.time_text.startswith("minute ") for row in rows
        )
        if demo:
            self.note.setText("Fictional demonstration. Minutes are the shared axis.")
        elif shared:
            self.note.setText(f"Open case {case_id}. Drug minutes use the case start when it is known.")
        else:
            self.note.setText(
                f"Open case {case_id}. Drug clocks and vital minutes are both shown. "
                "They are not shifted onto one axis unless a case start is known."
            )
        self.title_label.setText("Drugs and measurements on one clock" if shared else "Drugs and measurements")
        if not rows:
            self._show_nothing(NO_ROWS)
            return
        self.empty_label.hide()
        self._rows = rows
        self._row_minutes = [row.start for row in rows]
        timeline = build_timeline(
            "Invented record" if demo else f"Open case {case_id}", "invented minute" if demo else "minute", rows
        )
        self.chart.set_timeline(timeline)
        self._fill_table()
        self._set_view(self.view_tabs.currentIndex())
        self._select_cursor_rows()

    def _show_nothing(self, sentence: str) -> None:
        self._rows = ()
        self._row_minutes = []
        self.chart.set_timeline(None)
        self.table.setRowCount(0)
        self.empty_label.setText(sentence)
        self.empty_label.show()
        self.chart.hide()
        self.table.hide()

    def _set_view(self, index: int) -> None:
        if not self._rows:
            return
        self.chart.setVisible(index == 0 and self.chart.timeline() is not None)
        self.table.show()

    # —— the table ——

    def _fill_table(self) -> None:
        compact = bool(self._compact)
        rows = self._rows
        self.table.clearSelection()
        self.table.setRowCount(len(rows))
        self._items = []
        for index, row in enumerate(rows):
            subject = row.subject
            if compact:
                details = [f"{row.time_text} · {row.kind}", row.provenance]
                if row.note:
                    details.append(row.note)
                subject = "\n".join([row.subject, *details])
                values = ("", subject, row.value_text, "", "", "", "")
            else:
                values = (row.time_text, subject, row.number_text, row.unit, row.kind, row.provenance, row.note)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == _VALUE_COLUMN:
                    item.setTextAlignment(int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter))
                mark_missing(item, column == _VALUE_COLUMN and row.value_text in _MISSING_VALUES)
                self.table.setItem(index, column, item)
                if column == _ITEM_COLUMN:
                    self._items.append(item)
        self._apply_swatches()
        header = self.table.horizontalHeader()
        for column in range(len(_COLUMNS)):
            self.table.setColumnHidden(column, compact and column not in (_ITEM_COLUMN, _VALUE_COLUMN))
        sizing = QHeaderView.ResizeMode
        for column in range(len(_COLUMNS)):
            wide = column in (0, _VALUE_COLUMN, 3, 5)
            header.setSectionResizeMode(column, sizing.ResizeToContents if wide and not compact else sizing.Stretch)
        if compact:
            header.setSectionResizeMode(_VALUE_COLUMN, sizing.ResizeToContents)
        self.table.resizeRowsToContents()

    def _apply_swatches(self) -> None:
        icons = {spec.label: _swatch(spec.token) for spec in TRACK_SPECS.values()}
        for row, item in zip(self._rows, self._items, strict=False):
            spec = TRACK_SPECS.get(row.subject)
            item.setIcon(icons[spec.label] if row.kind == MEASUREMENT and spec is not None else QIcon())

    def _select_cursor_rows(self) -> None:
        """Select the rows at the cursor's minute, so the table and the chart always show the same minute."""
        minute = self.chart.cursor_minute()
        self._syncing = True
        try:
            self.table.clearSelection()
            model = self.table.selectionModel()
            first: int | None = None
            for index, row_minute in enumerate(self._row_minutes):
                if minute is not None and row_minute == minute:
                    model.select(
                        self.table.model().index(index, 0),
                        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
                    )
                    first = index if first is None else first
            anchor = None if first is None else self.table.item(first, _ITEM_COLUMN)
            if anchor is not None:
                self.table.scrollToItem(anchor, QAbstractItemView.ScrollHint.EnsureVisible)
        finally:
            self._syncing = False

    def selected_minute(self) -> int | None:
        return self.chart.cursor_minute()

    def _cursor_moved(self, minute: object) -> None:
        self._select_cursor_rows()
        timeline = self.chart.timeline()
        if timeline is not None and isinstance(minute, int):
            announce(self.chart, cursor_text(timeline, minute))

    def _row_chosen(self, row: int, _column: int, _old_row: int, _old_column: int) -> None:
        if self._syncing or not 0 <= row < len(self._row_minutes):
            return
        minute = self._row_minutes[row]
        if minute is None:
            return
        self.chart.set_cursor(minute, emit=False)
        self._select_cursor_rows()

    # —— layout ——

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.reflow()

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.StyleChange) and self._rows:
            self._apply_swatches()

    def reflow(self) -> None:
        """Title and tabs side by side when there is room, and the table's full or two-column form by width."""
        width = self.width()
        stacked = width < HEADER_ROW_BELOW
        if stacked != self._stacked:
            self._stacked = stacked
            self.title_row.setDirection(
                QBoxLayout.Direction.TopToBottom if stacked else QBoxLayout.Direction.LeftToRight)
            self.title_row.setAlignment(
                self.view_tabs, Qt.AlignmentFlag.AlignLeft if stacked else Qt.AlignmentFlag.AlignBottom)
        side = 16 if width < 480 else 32
        self.column.setContentsMargins(side, 20, side, 20)
        compact = width < TABLE_COMPACT_BELOW
        if compact != self._compact:
            self._compact = compact
            if self._rows:
                self._fill_table()
                self._select_cursor_rows()
