"""Question-led evidence workspace and focused signal inspection for the desktop."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.desktop.charts import ChartView, focused_signals_figure
from src.services.retrieval import RetrievalResult, SearchCandidate


@dataclass
class CaseView:
    candidate: SearchCandidate
    timeseries: pd.DataFrame
    flags: pd.DataFrame


def _card(title: str) -> tuple[QFrame, QVBoxLayout]:
    card = QFrame()
    card.setFrameShape(QFrame.Shape.StyledPanel)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(9)
    heading = QLabel(title)
    heading.setObjectName("sectionLabel")
    layout.addWidget(heading)
    return card, layout


def _wrap(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def _button(text: str, *, primary: bool = False) -> QPushButton:
    button = QPushButton(text)
    button.setMinimumHeight(44)
    button.setObjectName("primaryBtn" if primary else "secondaryBtn")
    return button


class ResearchWorkspace(QWidget):
    question_submitted = Signal(str)
    case_requested = Signal(str, bool, bool)
    setup_requested = Signal()
    model_start_requested = Signal()
    model_refresh_requested = Signal()
    model_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.result: RetrievalResult | None = None
        self._question = ""
        self._selected_pid: str | None = None
        self._primary_pid: str | None = None
        self._comparison_pid: str | None = None
        self._current_pid: str | None = None
        self._case_views: dict[str, CaseView] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        root.addWidget(self.stack)
        self._build_canvas()
        self._build_signal_detail()

    def _build_canvas(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(18, 14, 18, 18)
        layout.setSpacing(14)
        eyebrow = _wrap("G  /  EVIDENCE WORKSPACE")
        layout.addWidget(eyebrow)
        title = QLabel("An answer you can take apart.")
        title.setObjectName("heroTitle")
        title.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(_wrap("Ask a research question, inspect the cases behind the answer, and keep uncertainty visible."))

        question_card, question_layout = _card("Your question")
        question_row = QHBoxLayout()
        self.question_edit = QLineEdit()
        self.question_edit.setPlaceholderText("Try: Where does pressure peak in laparoscopic cases?")
        self.question_edit.setMinimumHeight(44)
        self.question_edit.returnPressed.connect(self._submit_question)
        question_row.addWidget(self.question_edit, stretch=1)
        self.ask_button = _button("Search and ask AI", primary=True)
        self.ask_button.clicked.connect(self._submit_question)
        question_row.addWidget(self.ask_button)
        question_layout.addLayout(question_row)

        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("Local chat model"))
        self.model_combo = QComboBox()
        self.model_combo.setMinimumHeight(44)
        self.model_combo.currentTextChanged.connect(self.model_changed)
        model_row.addWidget(self.model_combo, stretch=1)
        start = _button("Start")
        start.clicked.connect(self.model_start_requested)
        model_row.addWidget(start)
        refresh = _button("Refresh")
        refresh.clicked.connect(self.model_refresh_requested)
        model_row.addWidget(refresh)
        self.setup_button = _button("Data setup")
        self.setup_button.clicked.connect(self.setup_requested)
        model_row.addWidget(self.setup_button)
        question_layout.addLayout(model_row)
        self.status_label = _wrap("Set up an EMR folder to search indexed surgeries.")
        self.status_label.setAccessibleName("Research search status")
        question_layout.addWidget(self.status_label)
        self.scope_label = _wrap("Indexed surgeries: — · analyzed cases: —")
        question_layout.addWidget(self.scope_label)
        layout.addWidget(question_card)

        content = QGridLayout()
        content.setSpacing(14)
        answer_card, answer_layout = _card("AI reading · source linked")
        self.answer_text = QTextEdit()
        self.answer_text.setReadOnly(True)
        self.answer_text.setMinimumHeight(150)
        self.answer_text.setPlaceholderText("The local model will summarize loaded source extracts here. Case search still works when the model is unavailable.")
        answer_layout.addWidget(self.answer_text)
        content.addWidget(answer_card, 0, 0)

        candidate_card, candidate_layout = _card("Retrieved cases")
        self.candidate_table = QTableWidget(0, 3)
        self.candidate_table.setHorizontalHeaderLabels(["Case", "Procedure", "Scope"])
        self.candidate_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.candidate_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.candidate_table.setMinimumHeight(190)
        self.candidate_table.cellClicked.connect(lambda row, _column: self._select_candidate(row))
        self.candidate_table.itemActivated.connect(lambda item: self._select_candidate(item.row()))
        candidate_layout.addWidget(self.candidate_table)
        actions = QHBoxLayout()
        self.inspect_button = _button("Inspect selected signal", primary=True)
        self.inspect_button.clicked.connect(self._inspect_selected)
        actions.addWidget(self.inspect_button)
        self.compare_button = _button("Add as comparison")
        self.compare_button.clicked.connect(self._compare_selected)
        actions.addWidget(self.compare_button)
        candidate_layout.addLayout(actions)
        self.selection_label = _wrap("Choose a case with one click or Enter.")
        candidate_layout.addWidget(self.selection_label)
        content.addWidget(candidate_card, 1, 0)

        finding_card, finding_layout = _card("Observation")
        self.finding_title = _wrap("Select a retrieved case")
        finding_layout.addWidget(self.finding_title)
        self.finding_copy = _wrap("Detailed measurements load only when you inspect a case.")
        finding_layout.addWidget(self.finding_copy)
        self.finding_open = _button("Explore this signal in F")
        self.finding_open.clicked.connect(self._inspect_primary)
        finding_layout.addWidget(self.finding_open)
        content.addWidget(finding_card, 0, 1)

        source_card, source_layout = _card("Original source")
        self.source_label = _wrap("No case selected.")
        source_layout.addWidget(self.source_label)
        source_button = _button("Open source values")
        source_button.clicked.connect(self._show_primary_source)
        source_layout.addWidget(source_button)
        content.addWidget(source_card, 1, 1)

        self.comparison_card, compare_layout = _card("Comparison")
        self.comparison_label = _wrap()
        compare_layout.addWidget(self.comparison_label)
        compare_button = _button("Explore comparison in F")
        compare_button.clicked.connect(self._inspect_comparison)
        compare_layout.addWidget(compare_button)
        content.addWidget(self.comparison_card, 2, 0)
        self.comparison_card.hide()

        limit_card, limit_layout = _card("What this does not establish")
        self.limit_label = _wrap("A retrieved case can show a pattern, but its measurements alone do not establish a cause or a treatment decision. Missing values remain unknown.")
        limit_layout.addWidget(self.limit_label)
        content.addWidget(limit_card, 2, 1)
        content.setColumnStretch(0, 1)
        content.setColumnStretch(1, 1)
        layout.addLayout(content)
        layout.addStretch(1)
        scroll.setWidget(page)
        self.stack.addWidget(scroll)
        self._set_case_actions(False)

    def _build_signal_detail(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(18, 14, 18, 18)
        layout.setSpacing(12)
        back = _button("← Back to evidence workspace")
        back.clicked.connect(self.return_to_canvas)
        layout.addWidget(back, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(_wrap("F  /  SIGNAL DETAIL"))
        self.detail_title = QLabel("Select a case")
        self.detail_title.setObjectName("heroTitle")
        layout.addWidget(self.detail_title)
        self.detail_context = _wrap()
        layout.addWidget(self.detail_context)
        self.detail_chart = ChartView()
        layout.addWidget(self.detail_chart, stretch=1)
        self.minute_label = _wrap()
        layout.addWidget(self.minute_label)
        self.minute_slider = QSlider(Qt.Orientation.Horizontal)
        self.minute_slider.setMinimumHeight(44)
        self.minute_slider.setAccessibleName("Selected minute in case")
        self.minute_slider.valueChanged.connect(self._render_minute)
        layout.addWidget(self.minute_slider)
        self.sample_label = _wrap()
        layout.addWidget(self.sample_label)
        source = _button("Inspect selected source row")
        source.clicked.connect(self._show_current_source)
        layout.addWidget(source, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(_wrap("Historical de-identified research data · not for clinical care"))
        self.stack.addWidget(page)

    def selected_model(self) -> str | None:
        value = self.model_combo.currentText().strip()
        return value if value and not value.startswith("(") else None

    def set_models(self, models: list[str], current: str | None = None) -> None:
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        if models:
            self.model_combo.addItems(models)
            index = self.model_combo.findText(current or "")
            if index >= 0:
                self.model_combo.setCurrentIndex(index)
        else:
            self.model_combo.addItem("(AI unavailable · search still works)")
        self.model_combo.blockSignals(False)

    def set_setup_required(self, required: bool) -> None:
        self.ask_button.setEnabled(not required)
        self.question_edit.setEnabled(not required)
        self.status_label.setText(
            "Setup required. Choose a valid EMR folder before searching."
            if required else "Ready to search indexed surgeries."
        )

    def set_searching(self, active: bool) -> None:
        self.ask_button.setEnabled(not active)
        self.question_edit.setEnabled(not active)
        self.inspect_button.setEnabled(not active and self._selected_pid is not None)
        self.compare_button.setEnabled(not active and self._selected_pid is not None)

    def set_status(self, message: str) -> None:
        self.status_label.setText(message)

    def _submit_question(self) -> None:
        question = self.question_edit.text().strip()
        if question:
            self._question = question
            self.question_submitted.emit(question)

    def set_results(self, result: RetrievalResult) -> None:
        self.result = result
        self._primary_pid = None
        self._comparison_pid = None
        self._case_views.clear()
        self.comparison_card.hide()
        self.scope_label.setText(
            f"Indexed surgeries: {result.indexed_count:,} · analyzed cases: {result.analyzed_count:,} · ranking: {result.rank_method}"
        )
        self.candidate_table.setRowCount(len(result.candidates))
        for row, candidate in enumerate(result.candidates):
            for column, value in enumerate((candidate.pid, candidate.procedure, candidate.source_label)):
                self.candidate_table.setItem(row, column, QTableWidgetItem(str(value)))
        self.candidate_table.resizeColumnsToContents()
        self._selected_pid = None
        if result.candidates:
            self.candidate_table.setCurrentCell(0, 0)
            self._select_candidate(0)
            self.finding_title.setText("Open a case to inspect its measurements")
            self.finding_copy.setText("The shortlist is based on indexed surgery metadata. Detailed signal values load on demand.")
        else:
            self.finding_title.setText("No matching cases")
            self.finding_copy.setText("Try a broader question or review your data source.")
            self.source_label.setText("No source record matched this question.")
        self._set_case_actions(bool(result.candidates))

    def set_answer(self, answer: str) -> None:
        self.answer_text.setPlainText(answer)

    def set_case_data(
        self,
        pid: str,
        timeseries: pd.DataFrame,
        flags: pd.DataFrame,
        *,
        comparison: bool = False,
        open_detail: bool = False,
    ) -> None:
        candidate = self._candidate(pid)
        if candidate is None:
            return
        ordered = (
            timeseries.sort_values("t_min").reset_index(drop=True)
            if timeseries is not None and "t_min" in timeseries
            else pd.DataFrame(columns=["t_min"])
        )
        view = CaseView(candidate, ordered, flags)
        self._case_views[pid] = view
        if comparison:
            self._comparison_pid = pid
            self.comparison_card.show()
            self.comparison_label.setText(self._case_summary(view))
        else:
            self._primary_pid = pid
            self.finding_title.setText(self._observation(view)[0])
            self.finding_copy.setText(self._observation(view)[1])
            self.source_label.setText(self._case_summary(view))
        if open_detail:
            self.open_detail(pid)

    def _candidate(self, pid: str) -> SearchCandidate | None:
        if self.result is None:
            return None
        return next((candidate for candidate in self.result.candidates if candidate.pid == pid), None)

    def _select_candidate(self, row: int) -> None:
        if self.result is None or row >= len(self.result.candidates):
            return
        candidate = self.result.candidates[row]
        self._selected_pid = candidate.pid
        self.selection_label.setText(
            f"Selected {candidate.pid} · {candidate.source_label} · {candidate.procedure or 'procedure not recorded'}"
        )
        self._set_case_actions(True)

    def _set_case_actions(self, enabled: bool) -> None:
        self.inspect_button.setEnabled(enabled)
        self.compare_button.setEnabled(enabled)
        self.finding_open.setEnabled(enabled)

    def _inspect_selected(self) -> None:
        if self._selected_pid:
            self._open_or_request(self._selected_pid, comparison=False)

    def _compare_selected(self) -> None:
        if self._selected_pid:
            self._open_or_request(self._selected_pid, comparison=True, open_detail=False)

    def _inspect_primary(self) -> None:
        pid = self._primary_pid or self._selected_pid
        if pid:
            self._open_or_request(pid, comparison=False)

    def _inspect_comparison(self) -> None:
        if self._comparison_pid:
            self._open_or_request(self._comparison_pid, comparison=True)

    def _open_or_request(self, pid: str, *, comparison: bool, open_detail: bool = True) -> None:
        if pid in self._case_views:
            if open_detail:
                self.open_detail(pid)
            elif comparison:
                self._comparison_pid = pid
                self.comparison_card.show()
                self.comparison_label.setText(self._case_summary(self._case_views[pid]))
        else:
            self.case_requested.emit(pid, comparison, open_detail)

    def _observation(self, view: CaseView) -> tuple[str, str, int]:
        ts = view.timeseries
        if ts.empty:
            return "Signal source unavailable", "This case has no time series to inspect.", 0
        question = self._question.lower()
        if any(word in question for word in ("missing", "gap", "coverage")) and "ETCO2" in ts:
            missing = ts["ETCO2"].isna()
            if missing.any():
                index = int(missing.idxmax())
                minute = ts.iloc[index]["t_min"]
                return (
                    f"ETCO₂ missing at {minute:g} min",
                    "This source row has no ETCO₂ value. The gap remains unknown in the trace and source table.",
                    index,
                )
        if "PIP" in ts and ts["PIP"].notna().any():
            values = pd.to_numeric(ts["PIP"], errors="coerce")
            index = int(values.idxmax())
            row = ts.iloc[index]
            return (
                f"Peak sampled PIP {values.iloc[index]:g} cmH₂O at {row['t_min']:g} min",
                "This is the highest observed PIP in the loaded case. The measurements do not establish its cause.",
                index,
            )
        return "Case signals loaded", "Open F to inspect the recorded samples and missing values.", 0

    def _case_summary(self, view: CaseView) -> str:
        ts = view.timeseries
        pip = int(ts["PIP"].notna().sum()) if "PIP" in ts else 0
        etco2 = int(ts["ETCO2"].notna().sum()) if "ETCO2" in ts else 0
        return (
            f"{view.candidate.pid} · {view.candidate.procedure or 'procedure not recorded'}\n"
            f"{view.candidate.source_label} · {len(ts)} minute rows · PIP {pip}/{len(ts)} · ETCO₂ {etco2}/{len(ts)} observed"
        )

    def open_detail(self, pid: str) -> None:
        view = self._case_views.get(pid)
        if view is None or view.timeseries.empty:
            self.set_status("Source unavailable: this case has no time series to inspect.")
            return
        self._current_pid = pid
        self.detail_title.setText(f"{pid} · {view.candidate.procedure or 'procedure not recorded'}")
        self.detail_context.setText(f"Opened from the evidence workspace · {view.candidate.source_label}")
        self.minute_slider.blockSignals(True)
        self.minute_slider.setRange(0, len(view.timeseries) - 1)
        self.minute_slider.setValue(self._observation(view)[2])
        self.minute_slider.blockSignals(False)
        self.stack.setCurrentIndex(1)
        self._render_minute(self.minute_slider.value())

    def _render_minute(self, index: int) -> None:
        if self._current_pid is None:
            return
        view = self._case_views[self._current_pid]
        row = view.timeseries.iloc[index]
        minute = float(row["t_min"])
        self.minute_slider.setAccessibleDescription(f"{minute:g} minutes relative to incision")
        self.minute_label.setText(f"Selected moment: {minute:g} minutes relative to incision")
        values = []
        for column, label, unit in (("PIP", "PIP", "cmH₂O"), ("ETCO2", "ETCO₂", "mmHg"), ("HR", "Heart rate", "bpm")):
            value = row.get(column)
            values.append(f"{label}: {'missing' if pd.isna(value) else f'{float(value):g} {unit}'}")
        self.sample_label.setText(" · ".join(values))
        self.detail_chart.set_figure(focused_signals_figure(view.timeseries, minute))

    def return_to_canvas(self) -> None:
        self.stack.setCurrentIndex(0)
        self.set_status("Returned to the same question and comparison.")

    def _show_primary_source(self) -> None:
        pid = self._primary_pid or self._selected_pid
        if pid:
            self._show_source(pid)

    def _show_current_source(self) -> None:
        if self._current_pid:
            self._show_source(self._current_pid, self.minute_slider.value())

    def _show_source(self, pid: str, index: int | None = None) -> None:
        view = self._case_views.get(pid)
        if view is None or view.timeseries.empty:
            candidate = self._candidate(pid)
            if candidate:
                QMessageBox.information(self, f"Indexed source · {pid}", candidate.source_text)
            return
        ts = view.timeseries
        selected = self._observation(view)[2] if index is None else index
        start = max(0, selected - 10)
        stop = min(len(ts), selected + 11)
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Source samples · {pid}")
        dialog.resize(620, 480)
        layout = QVBoxLayout(dialog)
        layout.addWidget(_wrap(f"Rows {start + 1}–{stop} of {len(ts)} · minutes relative to incision · dash means missing"))
        table = QTableWidget(stop - start, 4)
        table.setHorizontalHeaderLabels(["Minute", "PIP cmH₂O", "ETCO₂ mmHg", "Heart rate bpm"])
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for table_row, frame_row in enumerate(range(start, stop)):
            source = ts.iloc[frame_row]
            for column, key in enumerate(("t_min", "PIP", "ETCO2", "HR")):
                value = source.get(key)
                text = "—" if pd.isna(value) else f"{float(value):g}"
                table.setItem(table_row, column, QTableWidgetItem(text))
        table.selectRow(selected - start)
        table.resizeColumnsToContents()
        layout.addWidget(table)
        close = _button("Close")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)
        dialog.exec()
