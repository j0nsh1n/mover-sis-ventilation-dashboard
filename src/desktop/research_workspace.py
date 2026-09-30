"""Question-led evidence workspace and focused signal inspection for the desktop."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
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


def _name_kicker(layout: QVBoxLayout) -> None:
    """Restyle a card's heading label (its first item) as a node kicker."""
    item = layout.itemAt(0)
    heading = item.widget() if item is not None else None
    if heading is not None:
        heading.setObjectName("nodeKicker")


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
        self.setStyleSheet("""
            QWidget#evidencePage, QScrollArea#evidenceScroll { background: #ebe9e3; color: #2c2930; }
            QWidget#evidenceHeader { background: #f8f7f3; border-bottom: 1px solid #d5d0d4; }
            QWidget#evidencePage QLabel { color: #302b35; }
            QLabel#evidenceTitle { font-family: Georgia; font-size: 34px; color: #302637; }
            QLabel#evidenceKicker { font-size: 11px; font-weight: bold; color: #755390; }
            QLabel#nodeTitle { font-family: Georgia; font-size: 23px; }
            QLabel#nodeKicker { font-size: 10px; font-weight: bold; }
            QFrame#questionNode, QFrame#findingNode, QFrame#sourceNode,
            QFrame#comparisonNode, QFrame#comparisonHint, QFrame#limitNode,
            QFrame#shortlistNode { border: 1px solid #d2cbd3; border-radius: 9px; }
            QFrame#questionNode { background: #332941; border-color: #332941; }
            QFrame#questionNode QLabel { color: #f7f2fa; }
            QFrame#questionNode QLabel#nodeKicker { color: #c9b8dd; }
            QFrame#questionNode QLabel#nodeTitle { color: #fff9f4; }
            QFrame#questionNode QLineEdit, QFrame#questionNode QComboBox,
            QFrame#questionNode QTextEdit { background: #493956; color: #fff9f4;
                border: 1px solid #8c739f; border-radius: 5px; padding: 8px; }
            QFrame#questionNode QLineEdit:disabled { color: #d3c6d9; }
            QFrame#questionNode QPushButton#primaryBtn { background: #f5e2cb; color: #332941;
                border: 1px solid #f5e2cb; border-radius: 5px; font-weight: bold; }
            QFrame#questionNode QPushButton#secondaryBtn { background: transparent; color: #eadcf4;
                border: 1px solid #8c739f; border-radius: 5px; }
            QFrame#findingNode { background: #f2e3d3; border-color: #ddc6af; }
            QFrame#sourceNode { background: #fffefa; }
            QFrame#comparisonNode { background: #e3e9e1; border-color: #b5c3b1; }
            QFrame#comparisonHint { background: transparent; border: 1px dashed #9e93a3; }
            QFrame#limitNode { background: #ebe4ef; border-color: #c8bbd1; }
            QFrame#shortlistNode { background: #fbfaf6; }
            QWidget#evidencePage QPushButton#textAction { background: transparent; color: #4b3d5d;
                border: 0; border-bottom: 1px solid #a99bac; border-radius: 0;
                text-align: left; padding: 7px 0; font-weight: bold; }
            QWidget#evidencePage QPushButton#outlineAction { background: #f8f7f3; color: #4b3d5d;
                border: 1px solid #b4afba; border-radius: 5px; padding: 8px 12px; }
            QWidget#evidencePage QTableWidget { background: #fffefa; color: #302b35;
                gridline-color: #ddd6dd; border: 1px solid #ddd6dd; }
            QWidget#evidencePage QHeaderView::section { background: #f1ecef; color: #4d4154;
                border: 0; border-bottom: 1px solid #d7ccd9; padding: 7px; }
            QWidget#signalPage { background: #111a1c; }
            QWidget#signalPage QLabel { color: #edf3eb; }
            QWidget#signalPage QLabel#signalKicker { color: #bce891; font-size: 11px; font-weight: bold; }
            QWidget#signalPage QLabel#signalTitle { font-family: Georgia; font-size: 34px; }
            QWidget#signalPage QLabel#minuteStamp { font-size: 47px; color: #edf3eb; }
            QWidget#signalPage QLabel#mutedText { color: #a3b8b2; }
            QFrame#signalShell { background: #142124; border: 1px solid #35484a; border-radius: 8px; }
            QFrame#signalShell QLabel { color: #d5e5dc; }
            QWidget#signalPage QPushButton { background: #263a2b; color: #d4f4b8;
                border: 1px solid #5b775e; border-radius: 5px; padding: 8px 12px; }
            QWidget#signalPage QSlider::groove:horizontal { height: 5px; background: #3a534e; }
            QWidget#signalPage QSlider::handle:horizontal { background: #c4ed93; width: 16px;
                margin: -6px 0; border-radius: 8px; }
            QWidget#signalPage QWidget#chartView { background: #142124; }
        """)

    def _build_canvas(self) -> None:
        scroll = QScrollArea()
        scroll.setObjectName("evidenceScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        page = QWidget()
        page.setObjectName("evidencePage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 24)
        layout.setSpacing(0)
        header = QWidget()
        header.setObjectName("evidenceHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(34, 21, 34, 21)
        header_copy = QVBoxLayout()
        kicker = QLabel("G  /  PRIMARY RESEARCH WORKSPACE")
        kicker.setObjectName("evidenceKicker")
        header_copy.addWidget(kicker)
        title = QLabel("An answer you can take apart.")
        title.setObjectName("evidenceTitle")
        header_copy.addWidget(title)
        header_layout.addLayout(header_copy, 1)
        self.add_comparison_button = _button("+ Add a comparison")
        self.add_comparison_button.setObjectName("outlineAction")
        self.add_comparison_button.clicked.connect(self._add_comparison)
        header_layout.addWidget(self.add_comparison_button)
        self.reset_button = _button("New question")
        self.reset_button.setObjectName("outlineAction")
        self.reset_button.clicked.connect(self._reset_canvas)
        header_layout.addWidget(self.reset_button)
        layout.addWidget(header)

        map_wrap = QWidget()
        map_layout = QVBoxLayout(map_wrap)
        map_layout.setContentsMargins(34, 20, 34, 16)
        map_layout.setSpacing(14)
        meta = QHBoxLayout()
        meta.addWidget(QLabel("●  EVIDENCE CONNECTIONS"))
        meta.addStretch(1)
        meta.addWidget(QLabel("Links show source relationships. They do not imply causation."))
        map_layout.addLayout(meta)

        content = QGridLayout()
        content.setHorizontalSpacing(24)
        content.setVerticalSpacing(20)
        question_card, question_layout = _card("01 / YOUR QUESTION")
        question_card.setObjectName("questionNode")
        _name_kicker(question_layout)
        question_title = _wrap("Start with what you want to know.")
        question_title.setObjectName("nodeTitle")
        question_layout.addWidget(question_title)
        question_layout.addWidget(QLabel("Research question"))
        self.question_edit = QLineEdit()
        self.question_edit.setPlaceholderText("Try: Where does pressure peak in laparoscopic cases?")
        self.question_edit.setMinimumHeight(44)
        self.question_edit.returnPressed.connect(self._submit_question)
        question_layout.addWidget(self.question_edit)
        self.ask_button = _button("Trace the evidence with AI  ↗", primary=True)
        self.ask_button.clicked.connect(self._submit_question)
        question_layout.addWidget(self.ask_button)
        question_layout.addWidget(_wrap("The local model reads at most three retrieved source extracts."))
        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("Local AI"))
        self.model_combo = QComboBox()
        self.model_combo.setMinimumHeight(44)
        self.model_combo.currentTextChanged.connect(self.model_changed)
        model_row.addWidget(self.model_combo, stretch=1)
        question_layout.addLayout(model_row)
        model_actions = QHBoxLayout()
        start = _button("Start AI")
        start.clicked.connect(self.model_start_requested)
        model_actions.addWidget(start)
        refresh = _button("Refresh")
        refresh.clicked.connect(self.model_refresh_requested)
        model_actions.addWidget(refresh)
        self.setup_button = _button("Data setup")
        self.setup_button.clicked.connect(self.setup_requested)
        model_actions.addWidget(self.setup_button)
        question_layout.addLayout(model_actions)
        self.status_label = _wrap("Set up an EMR folder to search indexed surgeries.")
        self.status_label.setAccessibleName("Research search status")
        question_layout.addWidget(self.status_label)
        self.scope_label = _wrap("Indexed surgeries: — · analyzed cases: —")
        question_layout.addWidget(self.scope_label)
        question_layout.addWidget(QLabel("AI READING / SOURCE LINKED"))
        self.answer_text = QTextEdit()
        self.answer_text.setReadOnly(True)
        self.answer_text.setMinimumHeight(130)
        self.answer_text.setPlaceholderText("The local model will summarize loaded source extracts here. Case search still works when the model is unavailable.")
        question_layout.addWidget(self.answer_text)
        question_layout.addStretch(1)
        content.addWidget(question_card, 0, 0, 2, 1)

        finding_card, finding_layout = _card("02 / OBSERVATION")
        finding_card.setObjectName("findingNode")
        _name_kicker(finding_layout)
        self.finding_title = _wrap("Select a retrieved case")
        self.finding_title.setObjectName("nodeTitle")
        finding_layout.addWidget(self.finding_title)
        self.finding_copy = _wrap("Detailed measurements load when you inspect a case.")
        finding_layout.addWidget(self.finding_copy)
        self.spark_label = QLabel("PIP / source samples load on demand")
        self.spark_label.setMinimumHeight(62)
        finding_layout.addWidget(self.spark_label)
        self.finding_open = _button("Explore this signal in F  ↗")
        self.finding_open.setObjectName("textAction")
        self.finding_open.clicked.connect(self._inspect_primary)
        finding_layout.addWidget(self.finding_open)
        content.addWidget(finding_card, 0, 1)

        source_card, source_layout = _card("03 / ORIGINAL SAMPLES")
        source_card.setObjectName("sourceNode")
        _name_kicker(source_layout)
        self.source_title = _wrap("Choose a case")
        self.source_title.setObjectName("nodeTitle")
        source_layout.addWidget(self.source_title)
        self.source_label = _wrap("No case selected.")
        source_layout.addWidget(self.source_label)
        source_button = _button("Open source table  ↗")
        source_button.setObjectName("textAction")
        source_button.clicked.connect(self._show_primary_source)
        source_layout.addStretch(1)
        source_layout.addWidget(source_button)
        content.addWidget(source_card, 0, 2)

        self.comparison_hint, hint_layout = _card("04 / COMPARE ANOTHER CASE")
        self.comparison_hint.setObjectName("comparisonHint")
        _name_kicker(hint_layout)
        hint_layout.addWidget(_wrap("Add a second case to the evidence trail."))
        self.hint_button = _button("Add a comparison")
        self.hint_button.setObjectName("outlineAction")
        self.hint_button.clicked.connect(self._add_comparison)
        hint_layout.addWidget(self.hint_button)
        content.addWidget(self.comparison_hint, 1, 1)

        self.comparison_card, compare_layout = _card("04 / COMPARE ANOTHER CASE")
        self.comparison_card.setObjectName("comparisonNode")
        _name_kicker(compare_layout)
        self.comparison_label = _wrap()
        compare_layout.addWidget(self.comparison_label)
        compare_button = _button("Explore comparison in F  ↗")
        compare_button.setObjectName("textAction")
        compare_button.clicked.connect(self._inspect_comparison)
        compare_layout.addWidget(compare_button)
        content.addWidget(self.comparison_card, 1, 1)
        self.comparison_card.hide()

        limit_card, limit_layout = _card("WHAT THIS DOES NOT ESTABLISH")
        limit_card.setObjectName("limitNode")
        _name_kicker(limit_layout)
        limit_title = _wrap("An observation has limits.")
        limit_title.setObjectName("nodeTitle")
        limit_layout.addWidget(limit_title)
        self.limit_label = _wrap("A retrieved case can show a pattern, but its measurements alone do not establish a cause or a treatment decision. Missing values remain unknown.")
        limit_layout.addWidget(self.limit_label)
        limit_layout.addStretch(1)
        content.addWidget(limit_card, 1, 2)
        content.setColumnStretch(0, 10)
        content.setColumnStretch(1, 11)
        content.setColumnStretch(2, 10)
        map_layout.addLayout(content)
        layout.addWidget(map_wrap)

        candidate_card, candidate_layout = _card("RETRIEVED CASES / SOURCE SHORTLIST")
        candidate_card.setObjectName("shortlistNode")
        _name_kicker(candidate_layout)
        self.candidate_table = QTableWidget(0, 3)
        self.candidate_table.setHorizontalHeaderLabels(["Case", "Procedure", "Scope"])
        self.candidate_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.candidate_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.candidate_table.setMinimumHeight(160)
        self.candidate_table.cellClicked.connect(lambda row, _column: self._select_candidate(row))
        self.candidate_table.itemActivated.connect(lambda item: self._select_candidate(item.row()))
        candidate_layout.addWidget(self.candidate_table)
        actions = QHBoxLayout()
        self.inspect_button = _button("Inspect selected signal  ↗")
        self.inspect_button.setObjectName("outlineAction")
        self.inspect_button.clicked.connect(self._inspect_selected)
        actions.addWidget(self.inspect_button)
        self.compare_button = _button("Add selected as comparison")
        self.compare_button.setObjectName("outlineAction")
        self.compare_button.clicked.connect(self._compare_selected)
        actions.addWidget(self.compare_button)
        candidate_layout.addLayout(actions)
        self.selection_label = _wrap("Choose a case with one click or Enter.")
        candidate_layout.addWidget(self.selection_label)
        layout.addWidget(candidate_card)
        layout.setStretchFactor(candidate_card, 0)
        scroll.setWidget(page)
        self.stack.addWidget(scroll)
        self._set_case_actions(False)

    def _build_signal_detail(self) -> None:
        page = QWidget()
        page.setObjectName("signalPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(34, 14, 34, 14)
        layout.setSpacing(10)
        back = _button("← Back to evidence workspace")
        back.clicked.connect(self.return_to_canvas)
        layout.addWidget(back, alignment=Qt.AlignmentFlag.AlignLeft)
        header = QHBoxLayout()
        heading = QVBoxLayout()
        kicker = QLabel("F  /  SIGNAL DETAIL")
        kicker.setObjectName("signalKicker")
        heading.addWidget(kicker)
        title = QLabel("Go straight to the moment.")
        title.setObjectName("signalTitle")
        heading.addWidget(title)
        heading.addWidget(_wrap("Inspect the observation from G. Scrub the case and check its exact sample."))
        header.addLayout(heading, 1)
        stamp = QVBoxLayout()
        stamp_label = QLabel("SELECTED MOMENT")
        stamp_label.setObjectName("mutedText")
        stamp.addWidget(stamp_label, alignment=Qt.AlignmentFlag.AlignRight)
        self.minute_stamp = QLabel("— min")
        self.minute_stamp.setObjectName("minuteStamp")
        stamp.addWidget(self.minute_stamp, alignment=Qt.AlignmentFlag.AlignRight)
        stamp.addWidget(QLabel("relative to incision"), alignment=Qt.AlignmentFlag.AlignRight)
        header.addLayout(stamp)
        layout.addLayout(header)
        self.detail_title = QLabel("Select a case")
        self.detail_title.setObjectName("signalKicker")
        self.detail_context = _wrap()
        self.detail_context.setObjectName("mutedText")
        shell = QFrame()
        shell.setObjectName("signalShell")
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(18, 16, 18, 16)
        shell_layout.setSpacing(8)
        shell_layout.addWidget(self.detail_title)
        shell_layout.addWidget(self.detail_context)
        metric_row = QHBoxLayout()
        self.metric_values = {}
        for key, label, unit in (("PIP", "PIP", "cmH₂O"), ("ETCO2", "ETCO₂", "mmHg"), ("HR", "Heart rate", "bpm")):
            metric = QVBoxLayout()
            metric.addWidget(QLabel(label.upper()))
            value = QLabel("—")
            value.setStyleSheet("font-size: 25px; color: #c4ed93; border: 0;")
            metric.addWidget(value)
            metric.addWidget(QLabel(unit))
            metric_row.addLayout(metric, 1)
            self.metric_values[key] = value
        shell_layout.addLayout(metric_row)
        self.detail_chart = ChartView()
        self.detail_chart.setMinimumHeight(220)
        shell_layout.addWidget(self.detail_chart, stretch=1)
        self.minute_label = _wrap()
        shell_layout.addWidget(self.minute_label)
        self.minute_slider = QSlider(Qt.Orientation.Horizontal)
        self.minute_slider.setMinimumHeight(44)
        self.minute_slider.setAccessibleName("Selected minute in case")
        self.minute_slider.valueChanged.connect(self._render_minute)
        shell_layout.addWidget(self.minute_slider)
        shell.setMinimumHeight(445)
        layout.addWidget(shell, 1)
        assistant = QHBoxLayout()
        mark = QLabel("✳")
        mark.setStyleSheet("font-size: 26px; color: #c4ed93;")
        assistant.addWidget(mark)
        copy = QVBoxLayout()
        copy.addWidget(QLabel("SOURCE SAMPLE / SELECTED MINUTE"))
        self.sample_label = _wrap()
        copy.addWidget(self.sample_label)
        assistant.addLayout(copy, 1)
        source = _button("Inspect selected source row")
        source.clicked.connect(self._show_current_source)
        assistant.addWidget(source)
        layout.addLayout(assistant)
        foot = _wrap("Historical de-identified research data · not for clinical care")
        foot.setObjectName("mutedText")
        layout.addWidget(foot)
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
        self.reset_button.setEnabled(not active)
        self.add_comparison_button.setEnabled(not active)
        self.hint_button.setEnabled(not active)
        self.inspect_button.setEnabled(not active and self._selected_pid is not None)
        self.compare_button.setEnabled(not active and self._selected_pid is not None)

    def set_status(self, message: str) -> None:
        self.status_label.setText(message)

    def _submit_question(self) -> None:
        question = self.question_edit.text().strip()
        if question:
            self._question = question
            self.question_submitted.emit(question)

    def _reset_canvas(self) -> None:
        self.question_edit.clear()
        self.question_edit.setFocus()
        self.result = None
        self._question = ""
        self._selected_pid = None
        self._primary_pid = None
        self._comparison_pid = None
        self._case_views.clear()
        self.candidate_table.setRowCount(0)
        self.finding_title.setText("Select a retrieved case")
        self.finding_copy.setText("Detailed measurements load when you inspect a case.")
        self.source_title.setText("Choose a case")
        self.source_label.setText("No case selected.")
        self.spark_label.setPixmap(QPixmap())
        self.spark_label.setText("PIP / source samples load on demand")
        self.answer_text.clear()
        self.comparison_card.hide()
        self.comparison_hint.show()
        self.scope_label.setText("Indexed surgeries: — · analyzed cases: —")
        self._set_case_actions(False)
        self.set_status("Enter a research question to trace its evidence.")

    def _add_comparison(self) -> None:
        if self.result is None:
            self.set_status("Search indexed surgeries before adding a comparison.")
            return
        primary = self._primary_pid or (self.result.candidates[0].pid if self.result.candidates else None)
        candidate = next(
            (item for item in self.result.candidates if item.pid != primary),
            None,
        )
        if candidate is None:
            self.set_status("No second retrieved case is available for comparison.")
            return
        self._open_or_request(candidate.pid, comparison=True, open_detail=False)

    def _show_sparkline(self, ts: pd.DataFrame) -> None:
        if "PIP" not in ts or not ts["PIP"].notna().any():
            self.spark_label.setText("PIP / no observed samples")
            return
        values = pd.to_numeric(ts["PIP"], errors="coerce")
        width, height = 320, 66
        pixmap = QPixmap(width, height)
        pixmap.fill(QColor("#f2e3d3"))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#cfb59f"), 1))
        painter.drawLine(0, height - 8, width, height - 8)
        low, high = float(values.min()), float(values.max())
        span = max(high - low, 1.0)
        path = QPainterPath()
        drawing = False
        for index, value in enumerate(values):
            if pd.isna(value):
                drawing = False
                continue
            x = 4 + index * (width - 8) / max(len(values) - 1, 1)
            y = height - 12 - (float(value) - low) / span * (height - 22)
            if drawing:
                path.lineTo(QPointF(x, y))
            else:
                path.moveTo(QPointF(x, y))
                drawing = True
        painter.setPen(QPen(QColor("#996045"), 2.5))
        painter.drawPath(path)
        painter.end()
        self.spark_label.setPixmap(pixmap)

    def set_results(self, result: RetrievalResult) -> None:
        self.result = result
        self._primary_pid = None
        self._comparison_pid = None
        self._case_views.clear()
        self.comparison_card.hide()
        self.comparison_hint.show()
        self.spark_label.setPixmap(QPixmap())
        self.spark_label.setText("PIP / source samples load on demand")
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
            row = self._preferred_row()
            self.candidate_table.setCurrentCell(row, 0)
            self._select_candidate(row)
            self.source_title.setText(result.candidates[row].pid)
            self.source_label.setText("Loading source values…")
            self.finding_title.setText("Open a case to inspect its measurements")
            self.finding_copy.setText("The shortlist is based on indexed surgery metadata. Detailed signal values load on demand.")
        else:
            self.finding_title.setText("No matching cases")
            self.finding_copy.setText("Try a broader question or review your data source.")
            self.source_title.setText("No match")
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
            self.comparison_hint.hide()
            self.comparison_label.setText(self._case_summary(view))
        else:
            self._primary_pid = pid
            self.finding_title.setText(self._observation(view)[0])
            self.finding_copy.setText(self._observation(view)[1])
            self.source_title.setText(pid)
            self.source_label.setText(self._case_summary(view))
            self._show_sparkline(ordered)
        if open_detail:
            self.open_detail(pid)

    def _candidate(self, pid: str) -> SearchCandidate | None:
        if self.result is None:
            return None
        return next((candidate for candidate in self.result.candidates if candidate.pid == pid), None)

    def candidate_for(self, pid: str) -> SearchCandidate | None:
        return self._candidate(pid)

    def _preferred_row(self) -> int:
        """First candidate with ventilator data; row 0 when none has any."""
        if self.result is None:
            return 0
        return next(
            (i for i, candidate in enumerate(self.result.candidates) if candidate.has_vent),
            0,
        )

    def preferred_pid(self) -> str | None:
        """The case to open automatically after a search."""
        if self.result is None or not self.result.candidates:
            return None
        return self.result.candidates[self._preferred_row()].pid

    def show_case_unavailable(self, pid: str, message: str, *, comparison: bool = False) -> None:
        """Say why a case cannot be shown, in the source card and status line."""
        if not comparison:
            self.source_title.setText(pid)
            self.source_label.setText(message)
        self.set_status(f"Source unavailable for {pid}: {message}")

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
        primary = self._primary_pid
        if primary is None and self.result is not None and self.result.candidates:
            primary = self.result.candidates[0].pid
        if self._selected_pid and self._selected_pid != primary:
            self._open_or_request(self._selected_pid, comparison=True, open_detail=False)
        elif self._selected_pid:
            self.set_status("Choose another case before adding a comparison.")

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
                self.comparison_hint.hide()
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
        self.minute_stamp.setText(f"{minute:g} min")
        self.minute_label.setText(f"Selected moment: {minute:g} minutes relative to incision")
        values = []
        for column, label, unit in (("PIP", "PIP", "cmH₂O"), ("ETCO2", "ETCO₂", "mmHg"), ("HR", "Heart rate", "bpm")):
            value = row.get(column)
            values.append(f"{label}: {'missing' if pd.isna(value) else f'{float(value):g} {unit}'}")
            self.metric_values[column].setText("missing" if pd.isna(value) else f"{float(value):g}")
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
