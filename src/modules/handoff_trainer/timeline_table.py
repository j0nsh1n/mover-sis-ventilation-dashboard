"""The same timeline as rows: every reading by minute, and each dose on the minute it starts."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem

from src.desktop.marks import MISSING_ROLE, MissingCellDelegate, mark_missing
from src.modules.handoff_trainer.grading import PracticeCase, format_number
from src.modules.handoff_trainer.timeline_chart import TRACKS, dose_line, is_unusable

MISSING_WORD = "missing"
UNUSABLE_WORD = "outside usable range, not drawn"
NO_ROWS = "No readings or doses on this timeline."
_MINUTE_ROLE = int(MISSING_ROLE) + 1


@dataclass(frozen=True)
class TableRow:
    minute: int
    values: tuple[float | None, ...]  # one per track, None where the minute has no reading
    doses: tuple[str, ...]


def table_rows(case: PracticeCase) -> list[TableRow]:
    """One row per sample, plus a row for a dose minute no sample covers, in minute order."""
    rows = [TableRow(sample.minute, tuple(getattr(sample, track.key) for track in TRACKS), ())
            for sample in sorted(case.samples, key=lambda item: item.minute)]
    sampled = {row.minute for row in rows}
    for minute in sorted({dose.minute for dose in case.doses} - sampled):
        rows.append(TableRow(minute, (None,) * len(TRACKS), ()))
    rows.sort(key=lambda row: row.minute)
    attached: set[int] = set()
    out: list[TableRow] = []
    for row in rows:
        # A dose goes on the first row of its minute, so two readings in one minute do not repeat it.
        if row.minute in attached:
            out.append(row)
            continue
        attached.add(row.minute)
        lines = tuple(dose_line(dose) for dose in case.doses if dose.minute == row.minute)
        out.append(TableRow(row.minute, row.values, lines))
    return out


def two_line_heading(heading: str, metrics: QFontMetrics) -> str:
    """``heading`` broken at the space that leaves its longer line shortest, or unchanged when it is one word."""
    words = heading.split(" ")
    if len(words) < 2:
        return heading
    splits = [(" ".join(words[:i]), " ".join(words[i:])) for i in range(1, len(words))]
    first, second = min(splits, key=lambda pair: max(metrics.horizontalAdvance(pair[0]),
                                                     metrics.horizontalAdvance(pair[1])))
    return f"{first}\n{second}"


class TimelineTable(QTableWidget):
    """Read-only. Arrow keys move between cells; Tab leaves the table."""

    def __init__(self) -> None:
        super().__init__(0, 2 + len(TRACKS))
        self.setObjectName("handoffTable")
        self.setAccessibleName("Invented timeline, minute by minute")
        headers = ["Minute", *[f"{track.label} {track.unit}" for track in TRACKS], "Drug doses"]
        self.setHorizontalHeaderLabels(headers)
        self._headings = headers
        # Headings go onto two lines only where one line would make the table scroll sideways; the length the
        # one-line headings needed is kept so they come back once there is room.
        self._two_lines = False
        self._one_line_length: int | None = None
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setTabKeyNavigation(False)
        self.setShowGrid(False)
        self.verticalHeader().setVisible(False)
        self.setItemDelegate(MissingCellDelegate(self))
        self.setWordWrap(True)
        header = self.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setStretchLastSection(True)
        for column in range(self.columnCount() - 1):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit_headings()

    def _set_two_lines(self, on: bool) -> None:
        metrics = self.horizontalHeader().fontMetrics()
        for column, heading in enumerate(self._headings):
            item = self.horizontalHeaderItem(column)
            if item is not None:
                item.setText(two_line_heading(heading, metrics) if on else heading)
        self._two_lines = on
        self.horizontalHeader().resizeSections()
        self.updateGeometries()

    def _fit_headings(self) -> None:
        available = self.viewport().width()
        if available <= 0:
            return
        header = self.horizontalHeader()
        if not self._two_lines:
            header.resizeSections()
            # The stretched last column still has its old width during a resize, so the need is the other
            # columns plus the least the last one takes (the header's default section size), not its length.
            last = self.columnCount() - 1
            least_last = max(header.minimumSectionSize(), header.defaultSectionSize())
            need = sum(header.sectionSize(column) for column in range(last)) + least_last
            if need > available:
                self._one_line_length = need
                self._set_two_lines(True)
        elif self._one_line_length is not None and self._one_line_length <= available:
            self._set_two_lines(False)

    def set_case(self, case: PracticeCase) -> None:
        self.clearSelection()
        rows = table_rows(case)
        self.clearSpans()
        self.setRowCount(max(len(rows), 1))
        if not rows:
            item = QTableWidgetItem(NO_ROWS)
            self.setItem(0, 0, item)
            self.setSpan(0, 0, 1, self.columnCount())
            return
        for index, row in enumerate(rows):
            minute = QTableWidgetItem(str(row.minute))
            minute.setData(_MINUTE_ROLE, row.minute)
            self.setItem(index, 0, minute)
            for column, value in enumerate(row.values, start=1):
                # A reading outside the sensor's range is left out of the chart and the grading, so it is
                # hatched here like a missing one, with its number and why in words.
                unusable = is_unusable(TRACKS[column - 1], value)
                if value is None:
                    text = MISSING_WORD
                elif unusable:
                    text = f"{format_number(value)} ({UNUSABLE_WORD})"
                else:
                    text = format_number(value)
                item = QTableWidgetItem(text)
                mark_missing(item, value is None or unusable)
                self.setItem(index, column, item)
            doses = QTableWidgetItem("; ".join(row.doses))
            self.setItem(index, self.columnCount() - 1, doses)
        self.resizeRowsToContents()
        # New readings change the columns' widths, so the headings are measured again on one line.
        if self._two_lines:
            self._set_two_lines(False)
        self._one_line_length = None
        self._fit_headings()

    def select_minutes(self, start: int, end: int) -> None:
        """Select the rows whose minute is inside ``start``..``end`` and scroll to the first."""
        self.clearSelection()
        model = self.selectionModel()
        first = None
        for row in range(self.rowCount()):
            item = self.item(row, 0)
            minute = item.data(_MINUTE_ROLE) if item is not None else None
            if minute is not None and start <= minute <= end:
                model.select(
                    self.model().index(row, 0),
                    QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
                )
                first = row if first is None else first
        anchor = None if first is None else self.item(first, 0)
        if anchor is not None:
            self.scrollToItem(anchor, QAbstractItemView.ScrollHint.PositionAtTop)

    def selected_minutes(self) -> list[int]:
        rows = {index.row() for index in self.selectedIndexes()}
        return sorted(int(item.data(_MINUTE_ROLE)) for row in rows if (item := self.item(row, 0)) is not None)
