"""Show aligned drug and vital rows for one case or the fictional demo."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
from PySide6.QtWidgets import QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from src.desktop.marks import MissingCellDelegate, mark_missing
from src.modules.medication_vitals.align import DISCLAIMER, align_rows, fictional_demo

_COLUMNS = ("Time", "Item", "Value", "Unit", "Kind", "Source")
_VALUE_COLUMN = _COLUMNS.index("Value")
# The two ways the record says a value is absent: a vital with no sample, and a dose never written down.
_MISSING_VALUES = {"missing", "not recorded"}


class MedicationVitalsPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("medication-vitals")
        notice = QLabel(DISCLAIMER)
        notice.setWordWrap(True)
        notice.setAccessibleDescription("Medication and vitals notice")
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setAccessibleDescription("Time alignment")
        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(list(_COLUMNS))
        self.table.setAccessibleName("Medication and vital rows")
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        # Arrow keys move between cells; Tab leaves the table.
        self.table.setTabKeyNavigation(False)
        self.table.setItemDelegate(MissingCellDelegate(self.table))
        layout = QVBoxLayout(self)
        layout.addWidget(notice)
        layout.addWidget(self.note)
        layout.addWidget(self.table)
        self.show_fictional()

    def show_fictional(self) -> None:
        medications, timeseries = fictional_demo()
        self.note.setText("Fictional demonstration. Minutes are the shared axis.")
        self.show_case("FAKE-MEDS", medications, timeseries, origin=None)

    def show_case(
        self,
        case_id: str,
        medications: pd.DataFrame | None,
        timeseries: pd.DataFrame | None,
        origin: datetime | None = None,
    ) -> None:
        rows = align_rows(medications, timeseries, origin=origin)
        shared = any(row.time_text.startswith("minute ") for row in rows if row.provenance.endswith(".csv")) and any(
            row.kind == "measurement" and row.time_text.startswith("minute ") for row in rows
        )
        if case_id != "FAKE-MEDS":
            if shared:
                self.note.setText(f"Open case {case_id}. Drug minutes use the case start when it is known.")
            else:
                self.note.setText(
                    f"Open case {case_id}. Drug clocks and vital minutes are both shown. "
                    "They are not shifted onto one axis unless a case start is known."
                )
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            values = (row.time_text, row.subject, row.value_text, row.unit, row.kind, row.provenance)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                mark_missing(item, column == _VALUE_COLUMN and value in _MISSING_VALUES)
                self.table.setItem(index, column, item)
