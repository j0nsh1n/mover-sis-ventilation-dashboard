"""A read-only list of past attempts, newest first. It offers no way to change or remove one."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone

from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.modules.handoff_trainer.history import attempt_counts

COLUMNS = ("When", "Run", "Found")
EMPTY = "No attempts are saved yet."
_STAMP = "%Y-%m-%dT%H:%M:%SZ"


def when_text(stamp: object) -> str:
    """A saved UTC stamp as local time; anything unreadable is shown as it was saved."""
    try:
        moment = datetime.strptime(str(stamp), _STAMP).replace(tzinfo=timezone.utc)
    except ValueError:
        return str(stamp)
    return moment.astimezone().strftime("%Y-%m-%d %H:%M")


def history_rows(attempts: Sequence[Mapping], titles: Mapping[str, str]) -> list[tuple[str, str, str]]:
    """(when, run, found) for each attempt, newest first; saved order is oldest first, so ties keep the later one."""
    rows: list[tuple[str, str, str]] = []
    for attempt in reversed(list(attempts)):
        case_id = str(attempt.get("case_id", ""))
        title = titles.get(case_id)
        found, total = attempt_counts(attempt)
        rows.append((when_text(attempt.get("at", "")), f"{title} ({case_id})" if title else case_id,
                     f"{found} of {total}"))
    return rows


class HistoryDialog(QDialog):
    def __init__(
        self, attempts: Sequence[Mapping], titles: Mapping[str, str], parent: QWidget | None = None, problem: str = ""
    ) -> None:
        super().__init__(parent)
        self.setObjectName("handoffHistory")
        self.setWindowTitle("Practice history")
        self.setAccessibleName("Practice history")
        self.resize(620, 420)
        rows = history_rows(attempts, titles)
        self.note = QLabel("Fictional practice attempts saved on this computer, newest first.")
        self.note.setObjectName("quietText")
        self.note.setWordWrap(True)
        self.problem = QLabel(problem)
        self.problem.setObjectName("quietText")
        self.problem.setWordWrap(True)
        self.problem.setVisible(bool(problem))
        self.table = QTableWidget(len(rows), len(COLUMNS))
        self.table.setAccessibleName("Past attempts, newest first")
        self.table.setHorizontalHeaderLabels(list(COLUMNS))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setTabKeyNavigation(False)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        for index, row in enumerate(rows):
            for column, text in enumerate(row):
                self.table.setItem(index, column, QTableWidgetItem(text))
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.empty = QLabel(EMPTY)
        self.empty.setObjectName("bodyText")
        self.empty.setVisible(not rows and not problem)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(self.note)
        layout.addWidget(self.problem)
        layout.addWidget(self.empty)
        layout.addWidget(self.table, 1)
        layout.addWidget(buttons)
        self.table.setVisible(bool(rows))
