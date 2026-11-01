"""Keyboard-operable practice for one fictional checklist card."""

from __future__ import annotations

from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.modules.who_checklist.model import (
    MARK_DONE,
    MARK_NOT_ON_CARD,
    apply_mark,
    debrief,
    open_stage,
    save_practice,
    scenarios,
)

_NOTICE = (
    "Fictional checklist practice. These cards are not patient records and this page "
    "does not read a data folder or tell you what to do for a patient."
)


class WhoPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("who-checklist")
        self._scenarios = scenarios()
        self._scenario = self._scenarios[0]
        self._marks: dict[str, str] = {}
        self._boxes: dict[str, QCheckBox] = {}

        notice = QLabel(_NOTICE)
        notice.setWordWrap(True)
        self.scenario_box = QComboBox()
        self.scenario_box.setAccessibleName("Fictional checklist card")
        for scenario in self._scenarios:
            self.scenario_box.addItem(scenario.title, scenario.scenario_id)
        self.scenario_box.currentIndexChanged.connect(self._select_scenario)

        self.briefing = QLabel()
        self.briefing.setWordWrap(True)
        self.briefing.setAccessibleName("Checklist card")
        self.stage_label = QLabel()
        self.stage_label.setAccessibleName("Checklist stage")

        self._form = QVBoxLayout()
        self.debrief_label = QLabel()
        self.debrief_label.setWordWrap(True)
        self.debrief_label.setAccessibleName("Checklist debrief")
        self.save_button = QPushButton("Save practice")
        self.save_button.setAccessibleName("Save checklist practice")
        self.save_button.clicked.connect(self._save)
        QShortcut(QKeySequence("Ctrl+Return"), self).activated.connect(self._save)

        layout = QVBoxLayout(self)
        layout.addWidget(notice)
        layout.addWidget(self.scenario_box)
        layout.addWidget(self.briefing)
        layout.addWidget(self.stage_label)
        layout.addLayout(self._form)
        layout.addWidget(self.debrief_label)
        layout.addWidget(self.save_button)
        self._build_form()

    def _select_scenario(self, index: int) -> None:
        if index < 0:
            return
        self._scenario = self._scenarios[index]
        self._marks = {}
        self._build_form()

    def _build_form(self) -> None:
        while self._form.count():
            taken = self._form.takeAt(0)
            if taken is None:
                continue
            widget = taken.widget()
            if widget is not None:
                widget.deleteLater()
        self._boxes = {}
        self.briefing.setText(self._scenario.briefing)
        for check in self._scenario.items:
            if check.required:
                box = QCheckBox(check.prompt)
                box.toggled.connect(lambda checked, item_id=check.item_id: self._set_required(item_id, checked))
            else:
                box = QCheckBox(f"Not on this card: {check.prompt}")
                box.toggled.connect(lambda checked, item_id=check.item_id: self._set_optional(item_id, checked))
            box.setAccessibleName(box.text())
            self._boxes[check.item_id] = box
            self._form.addWidget(box)
        self._refresh()

    def _set_required(self, item_id: str, checked: bool) -> None:
        if checked:
            self._marks = apply_mark(self._scenario, self._marks, item_id, MARK_DONE)
        else:
            self._marks.pop(item_id, None)
        self._refresh()

    def _set_optional(self, item_id: str, checked: bool) -> None:
        if checked:
            self._marks = apply_mark(self._scenario, self._marks, item_id, MARK_NOT_ON_CARD)
        else:
            self._marks.pop(item_id, None)
        self._refresh()

    def _refresh(self) -> None:
        stage = open_stage(self._scenario, self._marks)
        self.stage_label.setText("All stages marked." if stage is None else f"Current stage: {stage}")
        for item in self._scenario.items:
            box = self._boxes[item.item_id]
            expected = MARK_DONE if item.required else MARK_NOT_ON_CARD
            box.blockSignals(True)
            box.setChecked(self._marks.get(item.item_id) == expected)
            box.blockSignals(False)
            box.setEnabled(stage is None or item.stage == stage)
        self._show_debrief()

    def _show_debrief(self) -> None:
        result = debrief(self._scenario, self._marks)
        if result.complete:
            self.debrief_label.setText("Card complete. Save keeps this practice on this computer.")
        elif result.lines:
            self.debrief_label.setText(result.lines[0])
        else:
            self.debrief_label.setText("")

    def _save(self) -> None:
        save_practice(self._scenario, self._marks)
        result = debrief(self._scenario, self._marks)
        state = "complete" if result.complete else "still open"
        self.debrief_label.setText(f"Saved. This card is {state}.")
