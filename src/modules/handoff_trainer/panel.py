"""Practice form for a fictional handoff."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QLabel,
    QPushButton,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.desktop.accessibility import name_combo
from src.modules.handoff_trainer.cases import practice_cases
from src.modules.handoff_trainer.grading import PracticeCase, grade, timeline_text
from src.modules.handoff_trainer.history import load_attempts, record_attempt

_NOTICE = (
    "Educational practice on a fictional timeline. "
    "This is not clinical decision support and it does not say what to do for a patient. "
    "Name the timeline finding and one concrete detail. A keyword alone is not enough."
)


class HandoffPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("handoff-practice")
        self._cases = practice_cases()
        self._case = self._cases[0]

        notice = QLabel(_NOTICE)
        notice.setWordWrap(True)
        self.case_box = QComboBox()
        name_combo(self.case_box, "Fictional scenario")
        for case in self._cases:
            self.case_box.addItem(case.title, case.case_id)
        self.case_box.currentIndexChanged.connect(self._select_case)

        self.timeline = QPlainTextEdit()
        self.timeline.setReadOnly(True)
        self.timeline.setTabChangesFocus(True)
        self.timeline.setAccessibleName("Fictional timeline")

        self.handoff = QPlainTextEdit()
        self.handoff.setAccessibleName("I-PASS handoff")
        # Tab moves on to Grade; a handoff has no use for tab characters.
        self.handoff.setTabChangesFocus(True)
        self.handoff.setPlaceholderText(
            "Illness severity, patient summary, actions, awareness, and synthesis."
        )

        self.grade_button = QPushButton("Grade this handoff")
        self.grade_button.setAccessibleName("Grade this handoff")
        self.grade_button.clicked.connect(self._grade)

        self.result = QPlainTextEdit()
        self.result.setReadOnly(True)
        self.result.setTabChangesFocus(True)
        self.result.setAccessibleName("Handoff grade")
        self.history_label = QLabel()
        # A label reads out its text; the description says what it is.
        self.history_label.setAccessibleDescription("Practice history")

        layout = QVBoxLayout(self)
        layout.addWidget(notice)
        layout.addWidget(self.case_box)
        layout.addWidget(self.timeline, 2)
        layout.addWidget(self.handoff, 1)
        layout.addWidget(self.grade_button)
        layout.addWidget(self.result, 1)
        layout.addWidget(self.history_label)
        self._show_case(self._case)
        self._refresh_history()

    def _select_case(self, index: int) -> None:
        if index < 0:
            return
        self._show_case(self._cases[index])

    def _show_case(self, case: PracticeCase) -> None:
        self._case = case
        self.timeline.setPlainText(timeline_text(case))
        self.result.clear()

    def _grade(self) -> None:
        grades = grade(self._case, self.handoff.toPlainText())
        lines = [f"{item.status}: {item.evidence}" for item in grades] or ["Nothing on this timeline needs a handoff line."]
        self.result.setPlainText("\n".join(lines))
        record_attempt(self._case.case_id, self.handoff.toPlainText(), grades)
        self._refresh_history()

    def _refresh_history(self) -> None:
        count = len(load_attempts())
        self.history_label.setText(f"Saved practice attempts: {count}")
