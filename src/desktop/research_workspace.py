"""Question-led evidence workspace and focused signal inspection for the desktop."""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field, replace

import pandas as pd
from PySide6.QtCore import QEvent, QPointF, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QBoxLayout,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.desktop.cases_overview import CasesOverview
from src.desktop.charts import FocusedSignalChart, focused_minute_rows
from src.desktop.conversation_panel import ConversationPanel, Turn, TurnState
from src.desktop.rules_drawer import RulesDrawer, threshold_parts
from src.pipeline.flags import collapse_episodes
from src.rules import RuleCatalog
from src.services.case_fetch import FetchResult
from src.services.retrieval import RetrievalResult, SearchCandidate

CASES_PAGE = 2
RELOAD_NOTE = "Processed data was reloaded. Reopen a case to see current data."


@dataclass
class CaseView:
    candidate: SearchCandidate
    timeseries: pd.DataFrame
    flags: pd.DataFrame
    cases: pd.DataFrame = field(default_factory=pd.DataFrame)
    episodes: pd.DataFrame = field(default_factory=pd.DataFrame)
    events: pd.DataFrame = field(default_factory=pd.DataFrame)
    thresholds_sha256: str | None = None


@dataclass
class QuestionState:
    result: RetrievalResult | None
    question: str
    answer: str
    selected_pid: str | None
    primary_pid: str | None
    comparison_pid: str | None
    case_views: dict[str, CaseView]
    labels: tuple[str, ...]


RULES_DRAWER_MAX_WIDTH = 420
RULES_DRAWER_SHARE = 0.42


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


class _ShortlistTable(QTableWidget):
    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.fit_columns()

    def fit_columns(self) -> None:
        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.resizeColumnsToContents()
        if header.length() > self.viewport().width():
            header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)


class ResearchWorkspace(QWidget):
    question_submitted = Signal(str)
    cancel_requested = Signal()
    board_restore_requested = Signal()
    case_requested = Signal(str, bool, bool)
    setup_requested = Signal()
    model_start_requested = Signal()
    model_refresh_requested = Signal()
    model_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.result: RetrievalResult | None = None
        self._question = ""
        self._before_question: QuestionState | None = None
        self._searching = False
        self._active_turn_request: int | None = None
        self._board_request: int | None = None
        self._before_board_request: int | None = None
        self._restoring_board = False
        self._selected_pid: str | None = None
        self._primary_pid: str | None = None
        self._comparison_pid: str | None = None
        self._current_pid: str | None = None
        self._case_views: dict[str, CaseView] = {}
        self._rule_catalog: RuleCatalog | None = None
        self._thresholds_sha256: str | None = None
        self._episodes = pd.DataFrame()
        self._minute_rows = pd.DataFrame()
        self.selected_rule_id: str | None = None
        self.signal_statistics: dict[str, dict[str, float | int | None]] = {}
        self.chart_track_names = ["PIP", "ETCO2", "HR"]
        self._cases_data: tuple | None = None
        self._cases_overview: CasesOverview | None = None
        self._overview_candidates: dict[str, SearchCandidate] = {}
        self._case_origins: dict[str, deque[bool]] = {}
        self._detail_from_overview = False

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self._build_canvas()
        self._build_signal_detail()
        self.rules_drawer = RulesDrawer(self, fallback_focus=self._rules_fallback_focus)
        self.rules_drawer.closed.connect(self._apply_board_layout)
        root.addWidget(self.rules_drawer)
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
            QFrame#questionNode QLineEdit:focus { border: 2px solid #fff9f4; padding: 7px; }
            QFrame#questionNode QPushButton#primaryBtn { background: #f5e2cb; color: #332941;
                border: 1px solid #f5e2cb; border-radius: 5px; font-weight: bold; }
            QFrame#questionNode QPushButton#secondaryBtn { background: transparent; color: #eadcf4;
                border: 1px solid #8c739f; border-radius: 5px; }
            QFrame#questionNode QPushButton#secondaryBtn:focus { border: 2px solid #fff9f4; }
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
            QScrollArea#conversationPanel, QWidget#conversationPage { background: #ebe9e3; color: #302b35; }
            QFrame#conversationTurn { background: #fffefa; border: 1px solid #b4afba; border-radius: 8px; }
            QWidget#conversationPage QLabel { color: #302b35; }
            QWidget#conversationPage QLabel#turnQuestion { font-weight: bold; font-size: 15px; }
            QWidget#conversationPage QTextEdit { background: #fffefa; color: #302b35;
                border: 0; selection-background-color: #493956; selection-color: #fff9f4; }
            QWidget#conversationPage QPushButton { background: #f8f7f3; color: #4b3d5d;
                border: 1px solid #8c739f; border-radius: 5px; padding: 7px; min-height: 24px; }
            QWidget#conversationPage QPushButton#citationLink { text-align: left; text-decoration: underline; }
            QWidget#conversationPage QPushButton:disabled { color: #64586b; background: #ebe9e3; }
            QWidget#conversationPage QPushButton:focus, QWidget#conversationPage QTextEdit:focus {
                border: 2px solid #755390; }
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
            QWidget#signalPage QListWidget { background: #142124; color: #edf3eb;
                border: 1px solid #5b775e; padding: 4px; }
            QWidget#signalPage QListWidget::item { padding: 8px; }
            QWidget#signalPage QListWidget::item:selected { background: #344d40; color: #ffffff; }
            QWidget#signalPage QListWidget:focus { border: 2px solid #8c739f; }
            QScrollArea#signalScroll { background: #111a1c; border: 0; }
            QWidget#signalPage QSlider::groove:horizontal { height: 5px; background: #3a534e; }
            QWidget#signalPage QSlider::handle:horizontal { background: #c4ed93; width: 16px;
                margin: -6px 0; border-radius: 8px; }
            QWidget#signalPage QWidget#chartView { background: #142124; }
            QPushButton:focus,
            QFrame#questionNode QPushButton#primaryBtn:focus,
            QWidget#evidencePage QPushButton#textAction:focus,
            QWidget#evidencePage QPushButton#outlineAction:focus,
            QWidget#signalPage QPushButton:focus { border: 2px solid #8c739f; }
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
        header_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, header)
        header_layout.setContentsMargins(34, 21, 34, 21)
        self._header_layout = header_layout
        header_actions = QGridLayout()
        self._header_actions = header_actions
        header_copy = QVBoxLayout()
        kicker = QLabel("G  /  PRIMARY RESEARCH WORKSPACE")
        kicker.setObjectName("evidenceKicker")
        header_copy.addWidget(kicker)
        title = QLabel("An answer you can take apart.")
        title.setObjectName("evidenceTitle")
        title.setWordWrap(True)
        header_copy.addWidget(title)
        header_layout.addLayout(header_copy, 1)
        self.add_comparison_button = _button("+ Add a comparison")
        self.add_comparison_button.setObjectName("outlineAction")
        self.add_comparison_button.clicked.connect(self._add_comparison)
        self.cases_button = _button("Cases")
        self.cases_button.setObjectName("outlineAction")
        self.cases_button.setAccessibleName("Open the cases overview")
        self.cases_button.clicked.connect(self.show_cases)
        self.new_question_button = _button("New question")
        self.new_question_button.setObjectName("outlineAction")
        self.new_question_button.setAccessibleName("Clear the board and keep the conversation")
        self.new_question_button.clicked.connect(self._new_question)
        self.reset_button = _button("Reset")
        self.reset_button.setObjectName("outlineAction")
        self.reset_button.setAccessibleName("Clear the conversation and the board")
        self.reset_button.clicked.connect(self._reset_canvas)
        self.rules_button = _button("Rules")
        self.rules_button.setObjectName("outlineAction")
        self.rules_button.setAccessibleName("Open rules drawer")
        self.rules_button.setToolTip("Search the rules (Ctrl+R)")
        self.rules_button.clicked.connect(lambda: self.open_rules(self.rules_button))
        self._header_buttons = (self.add_comparison_button, self.cases_button, self.new_question_button,
                                self.reset_button, self.rules_button)
        self._place_header_actions(len(self._header_buttons))
        header_layout.addLayout(header_actions)
        layout.addWidget(header)

        map_wrap = QWidget()
        map_layout = QVBoxLayout(map_wrap)
        map_layout.setContentsMargins(34, 20, 34, 16)
        map_layout.setSpacing(14)
        meta = QHBoxLayout()
        meta.addWidget(QLabel("●  EVIDENCE CONNECTIONS"))
        meta.addStretch(1)
        meta.addWidget(_wrap("Links show source relationships. They do not imply causation."))
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
        self.cancel_button = _button("Cancel")
        self.cancel_button.setAccessibleName("Cancel research request")
        self.cancel_button.clicked.connect(self.cancel_requested)
        self.cancel_button.hide()
        question_layout.addWidget(self.cancel_button)
        question_layout.addWidget(_wrap("The local model reads at most three retrieved source extracts."))
        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("Local AI"))
        self.model_combo = QComboBox()
        self.model_combo.setMinimumHeight(44)
        self.model_combo.currentTextChanged.connect(self.model_changed)
        model_row.addWidget(self.model_combo, stretch=1)
        question_layout.addLayout(model_row)
        model_actions = QHBoxLayout()
        self.start_button = _button("Start AI")
        self.start_button.clicked.connect(self.model_start_requested)
        model_actions.addWidget(self.start_button)
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
        self.answer_text = QTextEdit(self)
        self.answer_text.setReadOnly(True)
        self.answer_text.hide()
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
        self._evidence_grid = content
        self._evidence_cards = (question_card, finding_card, source_card, self.comparison_card, limit_card)
        self._layout_mode = "wide"
        map_layout.addLayout(content)
        layout.addWidget(map_wrap)

        candidate_card, candidate_layout = _card("RETRIEVED CASES / SOURCE SHORTLIST")
        candidate_card.setObjectName("shortlistNode")
        _name_kicker(candidate_layout)
        self.candidate_table = _ShortlistTable(0, 3)
        self.candidate_table.setHorizontalHeaderLabels(["Case", "Procedure", "Scope"])
        self.candidate_table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.candidate_table.setWordWrap(False)
        self.candidate_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.candidate_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.candidate_table.setTabKeyNavigation(False)
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
        self.board_scroll = scroll
        self.conversation = ConversationPanel()
        self.conversation.case_requested.connect(self._open_cited_case)
        self.conversation.show_requested.connect(self.show_turn)
        self.conversation.ask_requested.connect(self.question_submitted)
        self.board_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.board_splitter.addWidget(scroll)
        self.board_splitter.addWidget(self.conversation)
        self.board_splitter.setStretchFactor(0, 1)
        self.board_splitter.setStretchFactor(1, 0)
        self.board_splitter.setSizes([1000, 340])
        self.stack.addWidget(self.board_splitter)
        self._set_case_actions(False)

    def _build_signal_detail(self) -> None:
        page = QWidget()
        page.setObjectName("signalPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(34, 14, 34, 14)
        layout.setSpacing(10)
        self.back_button = _button("← Back to evidence workspace")
        self.back_button.clicked.connect(self.return_to_canvas)
        layout.addWidget(self.back_button, alignment=Qt.AlignmentFlag.AlignLeft)
        header = QHBoxLayout()
        heading = QVBoxLayout()
        kicker = QLabel("F  /  SIGNAL DETAIL")
        kicker.setObjectName("signalKicker")
        heading.addWidget(kicker)
        title = QLabel("Go straight to the moment.")
        title.setObjectName("signalTitle")
        title.setWordWrap(True)
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
        self.detail_chart = FocusedSignalChart()
        self.detail_chart.minute_clicked.connect(self._select_flag_minute)
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
        self.episode_list = QListWidget()
        self.episode_list.setAccessibleName("Flag episodes")
        self.episode_list.setWordWrap(True)
        self.episode_list.setMinimumHeight(130)
        self.episode_list.setMaximumHeight(185)
        self.episode_list.currentRowChanged.connect(self._select_episode)
        self.episode_list.itemSelectionChanged.connect(self._episode_selection_cleared)
        layout.addWidget(self.episode_list)
        self.clear_episode_button = _button("Clear episode selection")
        self.clear_episode_button.clicked.connect(self.clear_episode_selection)
        layout.addWidget(self.clear_episode_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.rule_detail = _wrap("Select an episode to inspect its rule and measurements.")
        self.rule_detail.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.rule_detail)
        self.rule_link = _button("Show in rules list")
        self.rule_link.setEnabled(False)
        self.rule_link.clicked.connect(self._show_selected_rule)
        layout.addWidget(self.rule_link, alignment=Qt.AlignmentFlag.AlignLeft)
        assistant = QHBoxLayout()
        mark = QLabel("✳")
        mark.setStyleSheet("font-size: 26px; color: #c4ed93;")
        assistant.addWidget(mark)
        copy = QVBoxLayout()
        sample_kicker = QLabel("SOURCE SAMPLE / SELECTED MINUTE")
        sample_kicker.setWordWrap(True)
        copy.addWidget(sample_kicker)
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
        self.signal_scroll = QScrollArea()
        self.signal_scroll.setObjectName("signalScroll")
        self.signal_scroll.setWidgetResizable(True)
        self.signal_scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.signal_scroll.setWidget(page)
        self.stack.addWidget(self.signal_scroll)

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
        self._searching = active
        self.cancel_button.setVisible(active)
        self.add_comparison_button.setEnabled(not active)
        self.hint_button.setEnabled(not active)
        self.inspect_button.setEnabled(not active and self._selected_pid is not None)
        self.compare_button.setEnabled(not active and self._selected_pid is not None)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.rules_drawer.setFixedWidth(min(RULES_DRAWER_MAX_WIDTH, int(self.width() * RULES_DRAWER_SHARE)))
        self._apply_board_layout()

    def _apply_board_layout(self) -> None:
        """Re-flow the board and conversation for the width left beside the rules drawer."""
        board_width = self.width() - (self.rules_drawer.width() if self.rules_drawer.is_open else 0)
        stacked = board_width < 1100
        orientation = Qt.Orientation.Vertical if stacked else Qt.Orientation.Horizontal
        if self.board_splitter.orientation() != orientation:
            self.board_splitter.setOrientation(orientation)
            self.board_splitter.setSizes([max(300, self.height() - 360), 340] if stacked
                                         else [max(600, board_width - 340), 340])
        mode = "wide" if board_width >= 1100 else "narrow" if board_width >= 640 else "single"
        if mode == self._layout_mode:
            return
        self._layout_mode = mode
        positions = {
            "wide": ((0, 0, 2, 1), (0, 1, 1, 1), (0, 2, 1, 1), (1, 1, 1, 1), (1, 2, 1, 1)),
            "narrow": ((0, 0, 1, 2), (1, 0, 1, 1), (1, 1, 1, 1), (2, 0, 1, 1), (2, 1, 1, 1)),
            "single": ((0, 0, 1, 1), (1, 0, 1, 1), (2, 0, 1, 1), (3, 0, 1, 1), (4, 0, 1, 1)),
        }[mode]
        for card in (*self._evidence_cards, self.comparison_hint):
            self._evidence_grid.removeWidget(card)
        for card, position in zip(self._evidence_cards, positions, strict=True):
            self._evidence_grid.addWidget(card, *position)
        # The hint stands in for the comparison card until one is added.
        self._evidence_grid.addWidget(self.comparison_hint, *positions[3])
        stretches = {"wide": (10, 11, 10), "narrow": (10, 10, 0), "single": (1, 0, 0)}[mode]
        for column, stretch in enumerate(stretches):
            self._evidence_grid.setColumnStretch(column, stretch)
        self._header_layout.setDirection(
            QBoxLayout.Direction.TopToBottom if mode == "single" else QBoxLayout.Direction.LeftToRight
        )
        self._header_layout.setContentsMargins(*((16, 14, 16, 14) if mode == "single" else (34, 21, 34, 21)))
        # One row of five buttons is wider than a one-column board beside the drawer.
        self._place_header_actions(3 if mode == "single" else len(self._header_buttons))

    def _place_header_actions(self, columns: int) -> None:
        for button in self._header_buttons:
            self._header_actions.removeWidget(button)
        for index, button in enumerate(self._header_buttons):
            self._header_actions.addWidget(button, index // columns, index % columns)

    def set_rule_catalog(self, catalog: RuleCatalog | None, *, recorded: bool = True,
                         thresholds_sha256: str | None = None, error: str = "") -> None:
        """Hand the drawer the loaded preset's rules; None with an error means the config is invalid."""
        self._rule_catalog = catalog
        self._thresholds_sha256 = thresholds_sha256
        case = self._case_views.get(self._current_pid) if self._current_pid is not None and self.stack.currentIndex() == 1 else None
        self.rules_drawer.set_catalog(
            catalog, recorded=recorded, error=error,
            thresholds_sha256=case.thresholds_sha256 if case else thresholds_sha256,
        )
        if self._current_pid is not None:
            selected = self.episode_list.currentRow() if self.selected_rule_id is not None else -1
            self._build_episode_list()
            if selected >= 0:
                self.episode_list.setCurrentRow(selected)

    def open_rules(self, opener: QWidget | None = None) -> None:
        self.rules_drawer.open(opener)
        self._apply_board_layout()

    def show_rule(self, rule_id: str) -> None:
        self.rules_drawer.show_rule(rule_id)
        self._apply_board_layout()

    def _rules_fallback_focus(self) -> QWidget:
        return self.rules_button if self.rules_button.isVisible() else self.back_button

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        # The open rules drawer owns Escape; a second Escape shortcut here would make Qt drop both as ambiguous.
        if (event.key() == Qt.Key.Key_Escape and self.stack.currentIndex() == 1
                and not self.rules_drawer.isVisible()):
            event.accept()
            self.return_to_canvas()
            return
        # File > Reload data also uses Ctrl+R. event() claims the key while focus is
        # inside the workspace so the menu action does not fire there.
        if event.key() == Qt.Key.Key_R and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            event.accept()
            self.open_rules()
            return
        super().keyPressEvent(event)

    def event(self, event) -> bool:
        if (
            event.type() == QEvent.Type.ShortcutOverride
            and event.key() == Qt.Key.Key_R
            and event.modifiers() == Qt.KeyboardModifier.ControlModifier
        ):
            event.accept()
            return True
        return super().event(event)

    def _board_labels(self) -> tuple[QLabel, ...]:
        return (self.status_label, self.scope_label, self.finding_title, self.finding_copy,
                self.source_title, self.source_label, self.selection_label)

    def _snapshot_board(self) -> QuestionState:
        return QuestionState(
            self.result, self._question, self.answer_text.toPlainText(), self._selected_pid,
            self._primary_pid, self._comparison_pid, dict(self._case_views),
            tuple(label.text() for label in self._board_labels()),
        )

    def _save_board(self) -> None:
        turn = self.conversation.turn(self._board_request)
        if turn is not None and not self._restoring_board:
            turn.board = self._snapshot_board()

    def begin_question(self, question: str, request_id: int | None = None) -> None:
        self._save_board()
        self._before_question = self._snapshot_board()
        self._before_board_request = self._board_request
        self._board_request = None
        self._clear_board()
        self._active_turn_request = request_id if request_id is not None else max(
            (turn.request_id for turn in self.conversation.turns), default=0
        ) + 1
        self._board_request = self._active_turn_request
        self._question = question
        self.question_edit.setText(question)
        turn = Turn(self._active_turn_request, question, self.selected_model())
        turn.board = self._snapshot_board()
        self.conversation.append_turn(turn)
        self.set_searching(True)

    def _restore_board(self, state: QuestionState) -> None:
        self._restoring_board = True
        try:
            self._clear_board()
            self._question = state.question
            self.question_edit.setText(state.question)
            if state.result is not None:
                self.set_results(state.result)
                for pid in (state.primary_pid, state.comparison_pid):
                    if pid is not None and pid in state.case_views:
                        view = state.case_views[pid]
                        self.set_case_data(pid, view.timeseries, view.flags,
                                           fetched=FetchResult(view.cases, view.timeseries, view.flags, view.episodes, view.events),
                                           thresholds_sha256=view.thresholds_sha256,
                                           comparison=pid == state.comparison_pid)
                self._case_views = dict(state.case_views)
                self._primary_pid = state.primary_pid
                self._comparison_pid = state.comparison_pid
                self._selected_pid = state.selected_pid
                if state.selected_pid is not None:
                    row = next((i for i, candidate in enumerate(state.result.candidates) if candidate.pid == state.selected_pid), None)
                    if row is not None:
                        self.candidate_table.setCurrentCell(row, 0)
            self.answer_text.setPlainText(state.answer)
            for label, text in zip(self._board_labels(), state.labels, strict=True):
                label.setText(text)
            self.stack.setCurrentIndex(0)
        finally:
            self._restoring_board = False

    def cancel_question(self) -> None:
        state = self._before_question
        turn = self.conversation.turn(self._active_turn_request)
        if turn is not None and self._searching:
            turn.state = "cancelled"
            self._save_board()
            self.conversation.update_turn(turn)
        self._active_turn_request = None
        self._board_request = self._before_board_request
        self._before_question = None
        self.set_searching(False)
        if state is not None:
            self._restore_board(state)

    def show_turn(self, request_id: int) -> None:
        turn = self.conversation.turn(request_id)
        if turn is None or turn.board is None or turn.state == "running":
            return
        if self._searching:
            self.cancel_requested.emit()
            if self._searching:
                self.cancel_question()
        self._save_board()
        self.board_restore_requested.emit()
        self._active_turn_request = None
        self._board_request = request_id
        self._restore_board(turn.board)

    def _open_cited_case(self, request_id: int, pid: str) -> None:
        turn = self.conversation.turn(request_id)
        if turn is None or turn.board is None or turn.board.result is None:
            return
        # The case is saved into whichever turn is on the board, so show the owner first
        if self._board_request != request_id:
            self.show_turn(request_id)
            if self._board_request != request_id:
                return
        candidate = next((item for item in turn.board.result.candidates if item.pid == pid), None)
        if candidate is not None:
            self._overview_candidates[pid] = candidate
            self._open_or_request(pid, comparison=False)

    def set_status(self, message: str) -> None:
        self.status_label.setText(message)
        self._save_board()
        if self._cases_overview is not None and self.stack.currentIndex() == CASES_PAGE:
            self._cases_overview.set_message(message)

    def invalidate_case_data(self) -> bool:
        """Drop case views computed from the previous data; True when any were shown."""
        had_cases = bool(self._case_views) or self._current_pid is not None
        running = self.conversation.turn(self._active_turn_request)
        if running is not None and running.state == "running":
            running.state = "failed"
            running.failure = "Processed data was reloaded during this question."
            self._active_turn_request = None
            self._before_board_request = None
            self.board_restore_requested.emit()
        for turn in self.conversation.turns:
            self._drop_turn_case_data(turn, shown=turn.request_id == self._board_request)
        self._before_question = None
        self._current_pid = None
        self.rules_drawer.set_thresholds_sha256(self._thresholds_sha256)
        self._overview_candidates.clear()
        self._case_origins.clear()
        self.set_searching(False)
        if self.stack.currentIndex() != CASES_PAGE:
            self.stack.setCurrentIndex(0)
        self._case_views.clear()
        if self.result is not None:
            self.set_results(self.result)
        self._save_board()
        if running is not None:
            self.conversation.update_turn(running)
        return had_cases

    def _drop_turn_case_data(self, turn: Turn, *, shown: bool) -> None:
        """Forget case data a saved board kept from before a reload; the shown board is cleared live."""
        board = turn.board
        if board is None or not (board.case_views or board.primary_pid or board.comparison_pid):
            return
        turn.note = RELOAD_NOTE
        if not shown:
            labels = list(board.labels)
            labels[0] = RELOAD_NOTE
            labels[2:6] = ("Open a case to inspect its measurements",
                           "The shortlist is based on indexed surgery metadata. Detailed signal values load on demand.",
                           "Choose a case", "No case selected.")
            turn.board = replace(board, case_views={}, primary_pid=None, comparison_pid=None, labels=tuple(labels))
        self.conversation.update_turn(turn)

    def _new_question(self) -> None:
        self._stop_running_question()
        self.board_restore_requested.emit()
        self._detach_board()
        self._clear_board()

    def _stop_running_question(self) -> None:
        if self._searching:
            self.cancel_requested.emit()
            if self._searching:
                self.cancel_question()

    def _detach_board(self) -> None:
        self.set_searching(False)
        self._before_question = None
        self._active_turn_request = None
        self._board_request = None
        self._before_board_request = None

    def _confirm_reset(self) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Reset")
        box.setText("Clear the conversation and the board?")
        clear = box.addButton("Clear", QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        return box.clickedButton() is clear

    def _submit_question(self) -> None:
        question = self.question_edit.text().strip()
        if question:
            self.question_submitted.emit(question)

    def _reset_canvas(self) -> None:
        if self.conversation.turns and not self._confirm_reset():
            return
        if self._searching:
            self.cancel_requested.emit()
        self.board_restore_requested.emit()
        self._detach_board()
        self.conversation.clear()
        self._clear_board()

    def _clear_board(self) -> None:
        self.question_edit.clear()
        self.question_edit.setFocus()
        self.result = None
        self._question = ""
        self._selected_pid = None
        self._primary_pid = None
        self._comparison_pid = None
        self._case_views.clear()
        self._case_origins.clear()
        self.candidate_table.setRowCount(0)
        self.selection_label.setText("Choose a case with one click or Enter.")
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
        ranking = result.rank_method
        if ranking == "embedding":
            ranking += f", {result.embedded_count:,} of {result.indexed_count:,} records indexed"
            if result.embedded_count < result.indexed_count:
                ranking += "; the rest ranked by keywords"
        else:
            ranking = "keywords"
        self.scope_label.setText(
            f"Indexed surgeries: {result.indexed_count:,} · analyzed cases: {result.analyzed_count:,} · ranking: {ranking}"
        )
        self.candidate_table.setRowCount(len(result.candidates))
        for row, candidate in enumerate(result.candidates):
            for column, value in enumerate((candidate.pid, candidate.procedure, candidate.source_label)):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                self.candidate_table.setItem(row, column, item)
        self.candidate_table.fit_columns()
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
        self._save_board()

    def set_answer(
        self, answer: str, request_id: int | None = None, *,
        state: TurnState = "answered", failure: str = "",
    ) -> None:
        if request_id is not None and request_id != self._active_turn_request:
            return
        self.answer_text.setPlainText(answer)
        turn = self.conversation.turn(self._active_turn_request)
        if turn is not None:
            turn.answer, turn.state, turn.failure = answer, state, failure
            candidates = self.result.candidates if self.result is not None else ()
            turn.cited_pids = tuple(candidate.pid for candidate in candidates
                                    if re.search(r"(?<!\w)" + re.escape(candidate.pid) + r"(?!\w)", answer))
            self._save_board()
            self.conversation.update_turn(turn)

    def set_case_data(
        self,
        pid: str,
        timeseries: pd.DataFrame,
        flags: pd.DataFrame,
        *,
        fetched: FetchResult | None = None,
        thresholds_sha256: str | None = None,
        comparison: bool = False,
        open_detail: bool = False,
        from_overview: bool | None = None,
    ) -> None:
        if from_overview is None:
            from_overview = self.take_case_origin(pid)
        candidate = self._candidate(pid)
        if candidate is None:
            return
        ordered = (
            timeseries.sort_values("t_min", kind="stable").reset_index(drop=True)
            if timeseries is not None and "t_min" in timeseries
            else pd.DataFrame(columns=["t_min"])
        )
        episodes = fetched.episodes if fetched is not None else pd.DataFrame()
        if episodes.empty and not flags.empty:
            episodes = collapse_episodes(flags)
        view = CaseView(
            candidate, ordered, flags,
            fetched.cases if fetched is not None else pd.DataFrame(), episodes,
            fetched.events if fetched is not None else pd.DataFrame(),
            thresholds_sha256,
        )
        self._case_views[pid] = view
        if from_overview:
            if open_detail:
                self.open_detail(pid, from_overview=True)
            return
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
        self._save_board()
        if open_detail:
            self.open_detail(pid)

    def _result_candidate(self, pid: str) -> SearchCandidate | None:
        if self.result is None:
            return None
        return next((candidate for candidate in self.result.candidates if candidate.pid == pid), None)

    def _candidate(self, pid: str) -> SearchCandidate | None:
        return self._result_candidate(pid) or self._overview_candidates.get(pid)

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

    def show_case_unavailable(
        self, pid: str, message: str, *, comparison: bool = False,
        from_overview: bool | None = None,
    ) -> None:
        """Show a failure on the page that requested the case and in the status line."""
        if from_overview is None:
            from_overview = self.take_case_origin(pid)
        if from_overview and self._cases_overview is not None:
            self._cases_overview.set_message(f"Source unavailable for {pid}: {message}")
        if not from_overview and not comparison:
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
        self._save_board()

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

    def take_case_origin(self, pid: str) -> bool:
        origins = self._case_origins.get(pid)
        if not origins:
            return False
        from_overview = origins.popleft()
        if not origins:
            del self._case_origins[pid]
        return from_overview

    def _open_or_request(
        self, pid: str, *, comparison: bool, open_detail: bool = True,
        from_overview: bool = False,
    ) -> None:
        if pid in self._case_views:
            if open_detail:
                self.open_detail(pid, from_overview=from_overview)
            elif comparison:
                self._comparison_pid = pid
                self.comparison_card.show()
                self.comparison_hint.hide()
                self.comparison_label.setText(self._case_summary(self._case_views[pid]))
                self._save_board()
        else:
            self._case_origins.setdefault(pid, deque()).append(from_overview)
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

    def open_detail(self, pid: str, *, from_overview: bool = False) -> None:
        view = self._case_views.get(pid)
        if view is None or view.timeseries.empty:
            self.set_status("Source unavailable: this case has no time series to inspect.")
            return
        self._current_pid = pid
        self.rules_drawer.set_thresholds_sha256(view.thresholds_sha256)
        self._minute_rows = focused_minute_rows(view.timeseries).reset_index()
        self.selected_rule_id = None
        self.chart_track_names = ["PIP", "ETCO2", "HR"]
        self.signal_statistics = {}
        self.rule_detail.setText("Select an episode to inspect its rule and measurements.")
        self.rule_link.setEnabled(False)
        self._episodes = view.episodes.sort_values(["t_start_min", "rule_id"]).reset_index(drop=True) if not view.episodes.empty else pd.DataFrame()
        self._build_episode_list()
        self.detail_chart.set_case(view.timeseries, view.flags)
        self._detail_from_overview = from_overview
        origin = "the Cases overview" if self._detail_from_overview else "the evidence workspace"
        self.back_button.setText(
            "← Back to cases" if self._detail_from_overview else "← Back to evidence workspace"
        )
        self.detail_title.setText(f"{pid} · {view.candidate.procedure or 'procedure not recorded'}")
        self.detail_context.setText(f"Opened from {origin} · {view.candidate.source_label}")
        self.minute_slider.blockSignals(True)
        self.minute_slider.setRange(0, len(self._minute_rows) - 1)
        peak = float(view.timeseries.iloc[self._observation(view)[2]]["t_min"])
        self.minute_slider.setValue(int((self._minute_rows["t_min"] - peak).abs().argmin()))
        self.minute_slider.blockSignals(False)
        self.stack.setCurrentIndex(1)
        self.back_button.setFocus()
        self._render_minute(self.minute_slider.value())

    def _render_minute(self, index: int) -> None:
        if self._current_pid is None:
            return
        row = self._minute_rows.iloc[index]
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
        self.detail_chart.set_minute(minute)

    def _rule_spec(self, rule_id: str):
        if self._rule_catalog is not None:
            self._rule_catalog = self._rule_catalog.refreshed()
            try:
                return self._rule_catalog.get(rule_id)
            except KeyError:
                pass
        return None

    @staticmethod
    def _episode_phase(episode) -> str:
        start, end = episode.get("phase"), episode.get("phase_end")
        phase = str(start) if pd.notna(start) else "unknown"
        return f"{phase} → {end}" if pd.notna(end) and end != start else phase

    def _build_episode_list(self) -> None:
        self.episode_list.blockSignals(True)
        self.episode_list.clear()
        if self._episodes.empty:
            item = QListWidgetItem("No flags in this case")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.episode_list.addItem(item)
        for episode in self._episodes.to_dict("records"):
            spec = self._rule_spec(episode["rule_id"])
            label = spec.label if spec else episode["rule_id"]
            item = QListWidgetItem(
                f"{label} · {episode['severity']} · {self._episode_phase(episode)}\n"
                f"{episode['t_start_min']:g} to {episode['t_end_min']:g} min · {episode['duration_min']:g} min duration"
            )
            item.setToolTip(item.text())
            self.episode_list.addItem(item)
        self.episode_list.blockSignals(False)

    def _select_episode(self, index: int) -> None:
        if self._current_pid is None or not 0 <= index < len(self._episodes):
            self.clear_episode_selection()
            return
        episode = self._episodes.iloc[index]
        self.selected_rule_id = str(episode["rule_id"])
        spec = self._rule_spec(self.selected_rule_id)
        signals = [signal for signal in spec.signals if signal not in
                   {"t_min", "case_frac", "likely_mech_vent", "Age", "Agent"}] if spec else []
        extras = [signal for signal in signals if signal not in {"PIP", "ETCO2", "HR"}]
        self.chart_track_names = ["PIP", "ETCO2", "HR", *extras[:3]]
        ts = self._case_views[self._current_pid].timeseries
        rows = focused_minute_rows(ts)
        window = rows.loc[(rows.index >= episode["t_start_min"] - 1e-9)
                          & (rows.index <= episode["t_end_min"] + 1e-9)]
        self.signal_statistics = {}
        lines = [f"{spec.label if spec else self.selected_rule_id} ({self.selected_rule_id})"]
        if spec and self._rule_catalog:
            lines.extend(self._rule_catalog.thresholds_provenance(
                self._case_views[self._current_pid].thresholds_sha256
            ))
            lines.extend(threshold_parts(spec))
            if spec.composition:
                lines.append(spec.composition)
        else:
            lines.append("Current thresholds unavailable for this rule.")
        for signal in signals:
            values = pd.to_numeric(window[signal], errors="coerce") if signal in window else pd.Series(dtype=float)
            stats = {
                "min": float(values.min()) if values.notna().any() else None,
                "max": float(values.max()) if values.notna().any() else None,
                "mean": float(values.mean()) if values.notna().any() else None,
                "missing": len(window) - int(values.notna().sum()),
            }
            self.signal_statistics[signal] = stats
            summary = ", ".join(f"{key} {value:.6g}" if value is not None else f"{key} missing"
                                for key, value in stats.items() if key != "missing")
            lines.append(f"{signal}: {summary}; {stats['missing']} missing minutes"
                         + ("; not charted" if signal in extras[3:] else ""))
        lines.append(f"Phase: {self._episode_phase(episode)}")
        self.rule_detail.setText("\n".join(lines))
        self.rule_link.setEnabled(True)
        self.detail_chart.set_tracks(extras[:3])
        index = int((self._minute_rows["t_min"] - episode["t_start_min"]).abs().argmin())
        self.minute_slider.setValue(index)
        self._render_minute(index)

    def _episode_selection_cleared(self) -> None:
        if not self.episode_list.selectedItems():
            self.clear_episode_selection()

    def clear_episode_selection(self) -> None:
        self.episode_list.blockSignals(True)
        self.episode_list.setCurrentRow(-1)
        self.episode_list.clearSelection()
        self.episode_list.blockSignals(False)
        self.selected_rule_id = None
        self.signal_statistics = {}
        self.chart_track_names = ["PIP", "ETCO2", "HR"]
        self.rule_detail.setText("Select an episode to inspect its rule and measurements.")
        self.rule_link.setEnabled(False)
        self.detail_chart.set_tracks([])

    def _select_flag_minute(self, minute: float) -> None:
        matches = self._episodes[self._episodes["t_start_min"].le(minute) & self._episodes["t_end_min"].ge(minute)] if not self._episodes.empty else self._episodes
        if not matches.empty:
            ranked = matches.assign(_severity=matches["severity"].map({"critical": 0, "warn": 1, "info": 2}).fillna(3))
            index = int(ranked.sort_values(["_severity", "t_start_min", "rule_id"]).index[0])
            self.episode_list.blockSignals(True)
            self.episode_list.setCurrentRow(index)
            self.episode_list.blockSignals(False)
            self._select_episode(index)
        if self._current_pid is not None:
            self.minute_slider.setValue(int((self._minute_rows["t_min"] - minute).abs().argmin()))

    def _show_selected_rule(self) -> None:
        if self.selected_rule_id is not None:
            self.show_rule(self.selected_rule_id)

    def return_to_canvas(self) -> None:
        self.clear_episode_selection()
        self.rules_drawer.set_thresholds_sha256(self._thresholds_sha256)
        if self._detail_from_overview and self._cases_overview is not None:
            self.stack.setCurrentIndex(CASES_PAGE)
            self._cases_overview.set_message("")
            self._cases_overview.focus_table()
            return
        self.stack.setCurrentIndex(0)
        self.set_status("Returned to the same question and comparison.")

    def set_cases_data(self, cases: pd.DataFrame | None, scan, catalog) -> None:
        """Hand over the loaded sample, the full-EMR scan and the rule catalog of the loaded preset."""
        self._cases_data = (cases, scan, catalog)
        if self._cases_overview is not None:
            self._cases_overview.set_data(cases, scan, catalog)

    def show_cases(self) -> None:
        if self._cases_overview is None:
            # Built on first open so the workspace's first paint does not pay for it
            overview = CasesOverview()
            overview.case_requested.connect(self._open_overview_case)
            overview.back_requested.connect(self.close_cases)
            self.stack.addWidget(overview)
            self._cases_overview = overview
            if self._cases_data is not None:
                overview.set_data(*self._cases_data)
        overview = self._cases_overview
        overview.set_message("")
        self.stack.setCurrentIndex(CASES_PAGE)
        overview.focus_table()

    def close_cases(self) -> None:
        self.stack.setCurrentIndex(0)
        self.cases_button.setFocus()

    def _open_overview_case(self, pid: str) -> None:
        overview = self._cases_overview
        row = overview.row_for(pid) if overview is not None else None
        if overview is None or row is None:
            return
        age, sex = row.get("Age"), row.get("Gender")
        scan = overview.scope() == "scan"
        procedure = row.get("Procedure_short") or ""
        self._overview_candidates[pid] = SearchCandidate(
            pid=pid,
            source_label="full-EMR scan case" if scan else "analyzed case",
            analyzed=True,
            procedure=procedure,
            age=age,
            gender=sex or None,
            source_text=f"PID {pid}. Procedure: {procedure or 'not recorded'}.",
            rank_method="overview",
            score=0.0,
        )
        self._open_or_request(pid, comparison=False, from_overview=True)

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
        ts = self._minute_rows if index is not None and pid == self._current_pid else view.timeseries
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
