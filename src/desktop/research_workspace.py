"""Question-led evidence workspace and focused signal inspection for the desktop."""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field, replace

import pandas as pd
from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QKeyEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QBoxLayout,
    QDialog,
    QFrame,
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
from src.desktop.accessibility import announce
from src.desktop.case_page import (
    EPISODE_ROW_HEIGHT,
    GLYPH_ROLE,
    NAME_ROLE,
    RANGE_ROLE,
    SEVERITY_ROLE,
    SWATCH_SIZE,
    EpisodeDelegate,
    EpisodeList,
    SignalPage,
    swatch_icon,
)
from src.desktop.charts import BASE_TRACKS, FocusedSignalChart, focused_minute_rows, signal_info
from src.desktop.evidence_trail import GUTTER, Sparkline, TrailStep
from src.desktop.conversation_panel import AnswerText, ConversationPanel, Turn, TurnState
from src.desktop.fonts import make_tabular
from src.desktop.marks import MissingCellDelegate, mark_missing, severity_glyph
from src.desktop.research_controls import LocalModelButton, ScopeSwitch, TraceButton
from src.desktop.research_workers import MinuteQuestionWorker
from src.desktop.rules_drawer import RulesDrawer, threshold_parts
from src.desktop.theme import token_color
from src.desktop.threshold_ruler import ThresholdRuler, ruler_for
from src.llm.minute_context import build_minute_extract
from src.pipeline.flags import collapse_episodes
from src.rules import RuleCatalog
from src.services.articles import ABSTRACT_LIMIT, ArticleSearchResult, NO_CATALOG
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


@dataclass(frozen=True)
class Place:
    """Where the workspace is, for the running head's breadcrumb."""

    page: str  # "research", "signal" or "cases"
    question: int | None = None  # 1-based number of the question on the board
    questions: int = 0
    pid: str | None = None
    minute: float | None = None
    from_overview: bool = False


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
    scope: str = "Cases"
    article_result: ArticleSearchResult | None = None


FOLLOW_UP_NO_MODEL = "No AI answer. The minute's source rows are above."
RULES_DRAWER_MAX_WIDTH = 420
RULES_DRAWER_SHARE = 0.42
PAGE_MARGINS = (32, 24, 32, 32)
CONVERSATION_WIDTH = 380
CASES_PLACEHOLDER = "Try: Where does pressure peak in laparoscopic cases?"
ARTICLES_PLACEHOLDER = "Search titles and abstracts in the local catalog"
CASES_EXTRACTS = "The local model reads at most three retrieved source extracts."
ARTICLES_EXTRACTS = "The local model reads at most five article abstracts."
SPARK_WAITING = "PIP / source samples load on demand"
# The observation's signal column: its label on the sparkline and its colour token.
_SPARK_SIGNALS = {"PIP": ("PIP", "s_pip"), "ETCO2": ("ETCO₂", "s_etco2")}
SHORTLIST_COLUMNS = ("#", "Case", "Procedure", "Duration", "Status")
SOURCE_COLUMNS = ("Min", "PIP", "ETCO₂", "HR")
SOURCE_ROW_COLUMNS = ("Signal", "Value", "Unit")
EPISODE_ROWS_SHOWN = 6
NO_EPISODE_SELECTED = "Select an episode to inspect its rule and measurements."
# Only keys that work on this page: the chart's, the slider's own, the episode list's, Escape and Ctrl+R.
KEYS_LINE = ("Chart: ← → one minute, Shift+← Shift+→ ten, E next episode · "
             "Minute slider: ← → one minute, Page Up and Page Down ten · Episode list: ↑ ↓ pick an episode · "
             "Esc back · Ctrl+R rules")
SHORTLIST_COLLAPSED_ROWS = 3
SOURCE_ROWS = 3
ROW_HEIGHT = 32
HEADER_HEIGHT = 30
# Board width from which the observation and its source rows sit side by side.
SIDE_BY_SIDE_WIDTH = 720
CONTROLS_GAP = 24
# Bar position of a running question by search phase; the bar only moves forward.
PHASE_PROGRESS = {
    "": 0.05, "preparing": 0.1, "filtering": 0.25, "embedding_query": 0.35, "ranking": 0.5,
    "fetching": 0.7, "generating": 0.85, "done": 1.0,
}


def _wrap(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def _button(text: str, *, primary: bool = False) -> QPushButton:
    button = QPushButton(text)
    button.setMinimumHeight(44)
    button.setObjectName("primaryBtn" if primary else "secondaryBtn")
    # Outside a dialog Qt clicks a focused button on Space only; autoDefault adds Enter.
    # Every QPushButton in the main window sets it.
    button.setAutoDefault(True)
    return button


def _text_button(text: str) -> QPushButton:
    """A quiet action: accent text, no box."""
    button = QPushButton(text)
    button.setObjectName("textBtn")
    button.setAutoDefault(True)
    return button


def _link_button(text: str) -> QPushButton:
    """A small in-line link, such as Show all or All rows."""
    button = QPushButton(text)
    button.setObjectName("linkBtn")
    button.setAutoDefault(True)
    return button


def _step_heading(text: str, number: int | None = None) -> QWidget:
    """A step's name. A step with no pin of its own carries its number in front."""
    label = QLabel(text)
    label.setObjectName("stepHeading")
    if number is None:
        return label
    holder = QWidget()
    row = QHBoxLayout(holder)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(6)
    mark = QLabel(str(number))
    mark.setObjectName("stepNumber")
    row.addWidget(mark)
    row.addWidget(label, 1)
    holder.setAccessibleName(f"{number} {text}")
    return holder


def _align_headers(table: QTableWidget, right: tuple[int, ...]) -> None:
    """Headings sit over their cells: right over the number columns in ``right``, left over text."""
    for column in range(table.columnCount()):
        header = table.horizontalHeaderItem(column)
        if header is not None:
            side = Qt.AlignmentFlag.AlignRight if column in right else Qt.AlignmentFlag.AlignLeft
            header.setTextAlignment(side | Qt.AlignmentFlag.AlignVCenter)


def _fit_rows(table: QTableWidget, rows: int) -> None:
    """Size a small table to exactly ``rows`` rows, so only the page scrolls."""
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
    table.horizontalHeader().setFixedHeight(HEADER_HEIGHT)
    table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    # The app style keeps a 2 px frame so the focus ring does not move the rows.
    table.setFixedHeight(HEADER_HEIGHT + rows * ROW_HEIGHT + 4)


class _ShortlistTable(QTableWidget):
    """The shortlist: case and procedure stay whole, the procedure column takes the spare width."""

    def __init__(self, rows: int, columns: int, parent=None):
        super().__init__(rows, columns, parent)
        header = self.horizontalHeader()
        for column in range(columns):
            header.setSectionResizeMode(
                column, QHeaderView.ResizeMode.Stretch if column == 2 else QHeaderView.ResizeMode.ResizeToContents
            )
        header.setStretchLastSection(False)


class ResearchWorkspace(QWidget):
    question_submitted = Signal(str)
    cancel_requested = Signal()
    board_restore_requested = Signal()
    case_requested = Signal(str, bool, bool)
    setup_requested = Signal()
    model_start_requested = Signal()
    model_refresh_requested = Signal()
    model_stop_requested = Signal()
    model_unload_requested = Signal()
    model_changed = Signal(str)
    # Page, question, case or minute changed: the window refreshes its breadcrumb and rail.
    place_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.result: RetrievalResult | None = None
        self._question = ""
        self._scope = "Cases"
        self.article_result: ArticleSearchResult | None = None
        self._catalog_count = 0
        self._article_error = ""
        self._setup_required = False
        self._before_question: QuestionState | None = None
        self._searching = False
        # The status line shows a running step ("Model round 1/8…") that must not outlive its search
        self._status_is_progress = False
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
        # The user's More signals choices outlive episode selection, clearing and other cases
        self._user_extras: list[str] = []
        self._rule_extras: list[str] = []
        self._rule_lines: list[tuple[str | None, str]] = []
        self._cases_data: tuple | None = None
        # The last search's scope line; None until a case search returns.
        self._case_scope_text: str | None = None
        self._cases_overview: CasesOverview | None = None
        self._overview_candidates: dict[str, SearchCandidate] = {}
        self._case_origins: dict[str, deque[bool]] = {}
        self._detail_from_overview = False
        # The F follow-up answers one (case, minute); it is never part of the G conversation
        self._follow_up_key: tuple[str, float] | None = None
        self._follow_up_request = 0
        self._follow_up_active: int | None = None
        self._follow_up_workers: dict[int, MinuteQuestionWorker] = {}

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.stack = QStackedWidget()
        self.stack.currentChanged.connect(lambda _index: self.place_changed.emit())
        root.addWidget(self.stack, 1)
        self._build_canvas()
        self._build_signal_detail()
        self.rules_drawer = RulesDrawer(self, fallback_focus=self._rules_fallback_focus)
        self.rules_drawer.closed.connect(self._apply_board_layout)
        root.addWidget(self.rules_drawer)
        # The room the controls have is the board's viewport, which a scroll bar narrows; fit again when it changes.
        self.board_scroll.viewport().installEventFilter(self)

    def _build_canvas(self) -> None:
        scroll = QScrollArea()
        scroll.setObjectName("evidenceScroll")
        scroll.setWidgetResizable(True)
        scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        page = QWidget()
        page.setObjectName("evidencePage")
        self._board_page = page
        layout = QVBoxLayout(page)
        layout.setContentsMargins(*PAGE_MARGINS)
        layout.setSpacing(0)

        # The question is the page's title: the largest text on the screen.
        self.question_edit = QLineEdit()
        self.question_edit.setObjectName("questionField")
        self.question_edit.setAccessibleName("Research question")
        self.question_edit.setPlaceholderText(CASES_PLACEHOLDER)
        self.question_edit.returnPressed.connect(self._submit_question)
        layout.addWidget(self.question_edit)

        self.scope_switch = ScopeSwitch()
        self.scope_switch.changed.connect(self._change_scope)
        self.model_menu = LocalModelButton()
        self.model_menu.model_chosen.connect(self.model_changed)
        self.start_action = self.model_menu.start_action
        self.refresh_action = self.model_menu.refresh_action
        self.setup_action = self.model_menu.setup_action
        self.stop_action = self.model_menu.stop_action
        self.unload_action = self.model_menu.unload_action
        self.start_action.triggered.connect(self.model_start_requested)
        self.refresh_action.triggered.connect(self.model_refresh_requested)
        self.setup_action.triggered.connect(self.setup_requested)
        self.stop_action.triggered.connect(self.model_stop_requested)
        self.unload_action.triggered.connect(self.model_unload_requested)
        self.new_question_button = _text_button("New question")
        self.new_question_button.setAccessibleName("New question")
        self.new_question_button.setAccessibleDescription("Clear the board and keep the conversation")
        self.new_question_button.clicked.connect(self._new_question)
        self.add_comparison_button = _text_button("Add comparison")
        self.add_comparison_button.setAccessibleName("Add comparison")
        self.add_comparison_button.setAccessibleDescription("Add a second retrieved case to the evidence trail")
        self.add_comparison_button.clicked.connect(self._add_comparison)
        self.cancel_button = _text_button("Cancel")
        self.cancel_button.setAccessibleName("Cancel research request")
        self.cancel_button.clicked.connect(self.cancel_requested)
        self.cancel_button.hide()
        self.ask_button = TraceButton()
        self.ask_button.clicked.connect(self._submit_question)
        for keys in ("Ctrl+Return", "Ctrl+Enter"):
            shortcut = QShortcut(QKeySequence(keys), page)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(self._submit_with_shortcut)

        self._build_actions()

        choices = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        choices.setSpacing(12)
        choices.addWidget(self.scope_switch)
        choices.addWidget(self.model_menu)
        choices.addStretch(1)
        secondary = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        secondary.setSpacing(8)
        for widget in (self.new_question_button, self.add_comparison_button, self.cancel_button):
            secondary.addWidget(widget)
        actions = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        actions.setSpacing(8)
        actions.addLayout(secondary)
        actions.addWidget(self.ask_button)
        self._choices_row, self._secondary_row, self._actions_row = choices, secondary, actions
        # One row when it fits; otherwise the actions drop under the choices, still right-aligned.
        controls = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        controls.setSpacing(CONTROLS_GAP)
        controls.addLayout(choices)
        controls.addStretch(1)
        controls.addLayout(actions)
        self._controls_layout = controls
        layout.addSpacing(14)
        layout.addLayout(controls)

        self.model_status = _wrap("Local AI has not been checked. Search remains available.")
        self.model_status.setObjectName("quietText")
        self.status_label = _wrap("Set up an EMR folder to search indexed surgeries.")
        self.status_label.setObjectName("quietText")
        self.status_label.setAccessibleName("Research search status")
        layout.addSpacing(8)
        layout.addWidget(self.model_status)
        layout.addWidget(self.status_label)
        self.answer_text = QTextEdit(self)
        self.answer_text.setReadOnly(True)
        self.answer_text.hide()

        heading = QHBoxLayout()
        heading.setSpacing(12)
        title = QLabel("Evidence")
        title.setObjectName("boardHeading")
        heading.addWidget(title)
        note = QLabel("Each step shows where the next one came from. Lines are provenance, not cause.")
        note.setObjectName("quietText")
        note.setWordWrap(True)
        heading.addWidget(note, 1)
        layout.addSpacing(32)
        layout.addLayout(heading)
        layout.addSpacing(20)

        # Step 1: the shortlist
        self.case_shortlist_card = shortlist = TrailStep(1)
        self._shortlist_head = QHBoxLayout()
        self._shortlist_head.setSpacing(12)
        self._shortlist_head.addWidget(_step_heading("Shortlist"))
        self.scope_label = _wrap("Indexed surgeries: — · analyzed cases: —")
        self.scope_label.setObjectName("quietText")
        self._shortlist_head.addWidget(self.scope_label, 1)
        self.show_all_button = _link_button("Show all")
        self.show_all_button.setAccessibleName("Show all retrieved cases")
        self.show_all_button.clicked.connect(self._toggle_show_all)
        self.show_all_button.hide()
        self._shortlist_head.addWidget(self.show_all_button)
        shortlist.body.addLayout(self._shortlist_head)
        self.candidate_table = _ShortlistTable(0, len(SHORTLIST_COLUMNS))
        self.candidate_table.setObjectName("shortlistTable")
        self.candidate_table.setAccessibleName("Retrieved cases")
        self.candidate_table.setHorizontalHeaderLabels(list(SHORTLIST_COLUMNS))
        _align_headers(self.candidate_table, right=(0, 3))
        # No column lines (design rule 2); a drawn transparent grid would leave gaps in tinted rows.
        self.candidate_table.setShowGrid(False)
        self.candidate_table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.candidate_table.setWordWrap(False)
        self.candidate_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.candidate_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.candidate_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.candidate_table.setTabKeyNavigation(False)
        self.candidate_table.setItemDelegate(MissingCellDelegate(self.candidate_table))
        self.candidate_table.cellClicked.connect(lambda row, _column: self._select_candidate(row))
        self.candidate_table.itemActivated.connect(lambda item: self._select_candidate(item.row()))
        shortlist.body.addWidget(self.candidate_table)
        picks = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        picks.setSpacing(8)
        self.inspect_button = _text_button("Inspect selected signal")
        self.inspect_button.clicked.connect(self._inspect_selected)
        picks.addWidget(self.inspect_button)
        self.compare_button = _text_button("Add selected as comparison")
        self.compare_button.clicked.connect(self._compare_selected)
        picks.addWidget(self.compare_button)
        picks.addStretch(1)
        self._picks_row = picks
        shortlist.body.addLayout(picks)
        self.selection_label = _wrap("Choose a case with one click or Enter.")
        self.selection_label.setObjectName("quietText")
        shortlist.body.addWidget(self.selection_label)
        layout.addWidget(shortlist)

        # The article shortlist replaces the trail in the Articles scope
        self.article_card = articles = TrailStep(1)
        articles_head = QHBoxLayout()
        articles_head.setSpacing(12)
        articles_head.addWidget(_step_heading("Articles"))
        self._articles_head = articles_head
        articles_head.addStretch(1)
        articles.body.addLayout(articles_head)
        self.article_list = QListWidget()
        self.article_list.setAccessibleName("Matching local articles")
        self.article_list.setWordWrap(True)
        self.article_list.setMinimumHeight(260)
        articles.body.addWidget(self.article_list)
        layout.addWidget(articles)
        articles.hide()

        # Step 2: the observation, with the rows behind it beside it
        self.observation_step = observation = TrailStep(2)
        self._observation_columns = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._observation_columns.setSpacing(32)
        left = QVBoxLayout()
        left.setSpacing(6)
        left.addWidget(_step_heading("Observation"))
        self.finding_title = _wrap("Select a retrieved case")
        self.finding_title.setObjectName("statement")
        left.addWidget(self.finding_title)
        self.finding_context = _wrap()
        self.finding_context.setObjectName("quietText")
        self.finding_context.hide()
        left.addWidget(self.finding_context)
        self.finding_copy = _wrap("Detailed measurements load when you inspect a case.")
        self.finding_copy.setObjectName("quietText")
        left.addWidget(self.finding_copy)
        self.sparkline = Sparkline(SPARK_WAITING)
        left.addWidget(self.sparkline)
        self.finding_open = QPushButton("Open the signal")
        self.finding_open.setAutoDefault(True)
        self.finding_open.clicked.connect(self._inspect_primary)
        left.addWidget(self.finding_open, alignment=Qt.AlignmentFlag.AlignLeft)
        left.addStretch(1)
        right = QVBoxLayout()
        right.setSpacing(6)
        rows_head = QHBoxLayout()
        rows_head.setSpacing(8)
        rows_head.addWidget(_step_heading("Source rows", 3), 1)
        self.all_rows_button = _link_button("All rows")
        self.all_rows_button.setAccessibleName("All source rows")
        self.all_rows_button.clicked.connect(self._show_primary_source)
        rows_head.addWidget(self.all_rows_button)
        right.addLayout(rows_head)
        self.source_title = _wrap("Choose a case")
        self.source_title.setObjectName("stepHeading")
        right.addWidget(self.source_title)
        self.source_table = QTableWidget(0, len(SOURCE_COLUMNS))
        self.source_table.setObjectName("sourceRows")
        self.source_table.setAccessibleName("Source rows behind the observation")
        self.source_table.setHorizontalHeaderLabels(list(SOURCE_COLUMNS))
        _align_headers(self.source_table, right=(0, 1, 2, 3))
        self.source_table.setShowGrid(False)
        self.source_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.source_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.source_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.source_table.setItemDelegate(MissingCellDelegate(self.source_table))
        self.source_table.hide()
        _fit_rows(self.source_table, 0)
        right.addWidget(self.source_table)
        self.source_label = _wrap("No case selected.")
        self.source_label.setObjectName("quietText")
        right.addWidget(self.source_label)
        right.addStretch(1)
        self._observation_columns.addLayout(left, 1)
        self._observation_columns.addLayout(right, 1)
        observation.body.addLayout(self._observation_columns)
        layout.addWidget(observation)

        # Step 4: the comparison, once one is added
        self.comparison_card = comparison = TrailStep(4)
        comparison.body.addWidget(_step_heading("Comparison"))
        self.comparison_label = _wrap()
        comparison.body.addWidget(self.comparison_label)
        compare_open = _link_button("Open comparison")
        compare_open.clicked.connect(self._inspect_comparison)
        comparison.body.addWidget(compare_open, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(comparison)
        comparison.hide()

        self.limit_step = limit = TrailStep(None)
        limit.body.setContentsMargins(GUTTER, 0, 0, 8)
        limit.body.addWidget(_step_heading("What this does not establish"))
        self.limit_label = _wrap(
            "A retrieved case can show a pattern, but its measurements alone do not establish a cause or a "
            "treatment decision. Missing values remain unknown."
        )
        limit.body.addWidget(self.limit_label)
        layout.addWidget(limit)
        layout.addStretch(1)
        self._layout_mode = "wide"
        scroll.setWidget(page)
        self.board_scroll = scroll
        self.conversation = ConversationPanel()
        self.conversation.case_requested.connect(self._open_cited_case)
        self.conversation.show_requested.connect(self.show_turn)
        self.conversation.ask_scoped_requested.connect(self._ask_again)
        self.conversation.cancel_requested.connect(self.cancel_requested)
        self.conversation.reset_requested.connect(self._reset_canvas)
        self.board_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.board_splitter.setObjectName("boardSplitter")
        self.board_splitter.setHandleWidth(1)
        self.board_splitter.addWidget(scroll)
        self.board_splitter.addWidget(self.conversation)
        self.board_splitter.setStretchFactor(0, 1)
        self.board_splitter.setStretchFactor(1, 0)
        self.board_splitter.setSizes([1000, CONVERSATION_WIDTH])
        self.canvas_scroll = QScrollArea()
        self.canvas_scroll.setWidgetResizable(True)
        self.canvas_scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.canvas_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.canvas_scroll.setWidget(self.board_splitter)
        self.stack.addWidget(self.canvas_scroll)
        self._extract_text = ""
        self._show_all_rows = False
        self._fit_shortlist()
        self._set_case_actions(False)
        self._set_extract_text(CASES_EXTRACTS)

    def _build_actions(self) -> None:
        """Actions behind the page's buttons, so Ctrl+K lists the same things the buttons do."""
        self.trace_action = QAction("Trace the evidence", self)
        self.trace_action.triggered.connect(self._submit_with_shortcut)
        self.new_question_action = QAction("New question", self)
        self.new_question_action.setToolTip("Clear the board and keep the conversation")
        self.new_question_action.triggered.connect(self._new_question)
        self.add_comparison_action = QAction("Add comparison", self)
        self.add_comparison_action.triggered.connect(self._add_comparison)
        self.reset_action = QAction("Reset conversation and board", self)
        self.reset_action.setToolTip("Clear the conversation and the board")
        self.reset_action.triggered.connect(self._reset_canvas)
        self.scope_actions: dict[str, QAction] = {}
        group = QActionGroup(self)
        group.setExclusive(True)
        for scope, text in (("Cases", "Ask about cases"), ("Articles", "Ask about articles")):
            action = QAction(text, self)
            action.setCheckable(True)
            action.setChecked(scope == "Cases")
            action.triggered.connect(lambda _checked=False, scope=scope: self.scope_switch.set_scope(scope))
            group.addAction(action)
            self.scope_actions[scope] = action

    def command_entries(self) -> list[tuple[QAction, str, str]]:
        """Palette lines for the research page: the action, its group and extra search words."""
        research = "Research"
        local = "Local AI"
        menu = self.model_menu
        return [
            (self.trace_action, research, "search ask question evidence"),
            (self.new_question_action, research, "clear board ask"),
            (self.add_comparison_action, research, "second case compare"),
            (self.reset_action, research, "clear conversation start over"),
            (self.scope_actions["Cases"], "Question scope", "search switch"),
            (self.scope_actions["Articles"], "Question scope", "search switch catalog"),
            (menu.start_action, local, "ollama model start"),
            (menu.stop_action, local, "ollama model stop memory"),
            (menu.unload_action, local, "ollama model memory"),
            (menu.refresh_action, local, "ollama model list models"),
        ]

    def _sync_actions(self) -> None:
        """Keep the palette's copies of the buttons on the same enabled rules as the buttons."""
        busy_reason = "A search is running. Cancel it or wait for it to finish."
        for action, button in ((self.trace_action, self.ask_button), (self.add_comparison_action, self.add_comparison_button)):
            action.setEnabled(button.isEnabled() and not button.isHidden())
            action.setToolTip("" if action.isEnabled() else (busy_reason if self._searching else "Not available here."))

    def _build_signal_detail(self) -> None:
        page = SignalPage()
        self.signal_page = page
        self.back_button = _link_button("← Back to evidence trail")
        self.back_button.setAccessibleName("Back to evidence trail")
        self.back_button.clicked.connect(self.return_to_canvas)
        page.top.insertWidget(0, self.back_button, alignment=Qt.AlignmentFlag.AlignLeft)

        # Header: where the case was opened from, the procedure as the title, one facts line, the selected minute.
        heading = QVBoxLayout()
        heading.setSpacing(2)
        self.detail_context = _wrap()
        self.detail_context.setObjectName("quietText")
        heading.addWidget(self.detail_context)
        self.detail_title = _wrap("Select a case")
        self.detail_title.setObjectName("caseTitle")
        heading.addWidget(self.detail_title)
        self.detail_facts = _wrap()
        self.detail_facts.setObjectName("caseFacts")
        heading.addWidget(self.detail_facts)
        stamp = QVBoxLayout()
        stamp.setSpacing(0)
        stamp_label = QLabel("Selected minute")
        stamp_label.setObjectName("quietText")
        stamp.addWidget(stamp_label)
        reading = QHBoxLayout()
        reading.setSpacing(6)
        self.minute_stamp = QLabel("—")
        self.minute_stamp.setObjectName("minuteStamp")
        self.minute_stamp.setAccessibleName("Selected minute")
        make_tabular(self.minute_stamp)
        reading.addWidget(self.minute_stamp, alignment=Qt.AlignmentFlag.AlignBottom)
        caption = QLabel("min from incision")
        caption.setObjectName("minuteCaption")
        reading.addWidget(caption, alignment=Qt.AlignmentFlag.AlignBottom)
        stamp.addLayout(reading)
        page.header.addLayout(heading, 1)
        page.header.addLayout(stamp)
        page.header.setAlignment(stamp, Qt.AlignmentFlag.AlignBottom)
        page.set_stamp(stamp_label, reading)

        # Chart column: tracks, the keys that work, the minute slider, the question about the minute.
        column = page.chart_column
        toolbar = QHBoxLayout()
        self.more_signals_button = _link_button("+ Track")
        self.more_signals_button.setAccessibleName("More signals")
        self.more_signals_button.setAccessibleDescription("Choose up to three extra tracks for the chart")
        self.more_signals_button.clicked.connect(self._choose_signals)
        toolbar.addWidget(self.more_signals_button)
        self.extras_note = _wrap()
        self.extras_note.setObjectName("quietText")
        self.extras_note.setVisible(False)
        toolbar.addWidget(self.extras_note, 1)
        toolbar.addStretch(1)
        column.addLayout(toolbar)
        self.detail_chart = FocusedSignalChart()
        self.detail_chart.episode_clicked.connect(self._pick_episode)
        self.detail_chart.minute_step.connect(self._step_minute)
        self.detail_chart.next_episode_requested.connect(self._select_next_episode)
        self.detail_chart.setMinimumWidth(260)
        self.metric_values = self.detail_chart.value_labels
        column.addWidget(self.detail_chart, stretch=1)
        self.keys_label = _wrap(KEYS_LINE)
        self.keys_label.setObjectName("quietText")
        column.addWidget(self.keys_label)
        self.minute_slider = QSlider(Qt.Orientation.Horizontal)
        self.minute_slider.setObjectName("minuteSlider")
        self.minute_slider.setMinimumHeight(28)
        self.minute_slider.setPageStep(10)
        self.minute_slider.setAccessibleName("Selected minute in case")
        self.minute_slider.valueChanged.connect(self._render_minute)
        column.addWidget(self.minute_slider)
        ask_row = QHBoxLayout()
        ask_row.setSpacing(12)
        self.follow_up_edit = QLineEdit()
        self.follow_up_edit.setPlaceholderText("Ask about this minute")
        self.follow_up_edit.setAccessibleName("Ask about this minute")
        self.follow_up_edit.returnPressed.connect(self._ask_about_minute)
        ask_row.addWidget(self.follow_up_edit, 1)
        self.follow_up_button = QPushButton("Ask")
        self.follow_up_button.setObjectName("primaryBtn")
        self.follow_up_button.setAutoDefault(True)
        self.follow_up_button.clicked.connect(self._ask_about_minute)
        ask_row.addWidget(self.follow_up_button)
        self.follow_up_cancel = _text_button("Cancel")
        self.follow_up_cancel.clicked.connect(lambda: self._reset_follow_up())
        self.follow_up_cancel.hide()
        ask_row.addWidget(self.follow_up_cancel)
        column.addLayout(ask_row)
        self.follow_up_label = _wrap()
        self.follow_up_label.setObjectName("quietText")
        self.follow_up_label.setTextFormat(Qt.TextFormat.PlainText)
        self.follow_up_label.hide()
        column.addWidget(self.follow_up_label)
        self.follow_up_answer = AnswerText()
        self.follow_up_answer.setAccessibleName("Answer about the selected minute")
        self.follow_up_answer.hide()
        column.addWidget(self.follow_up_answer)

        # Inspector: the source row, the rule episodes, the selected rule.
        inspector = page.inspector_column
        source = QVBoxLayout()
        source.setSpacing(6)
        source_head = QHBoxLayout()
        self.source_heading = QLabel("Source row")
        self.source_heading.setObjectName("stepHeading")
        source_head.addWidget(self.source_heading, 1)
        self.open_table_button = _link_button("Open table")
        self.open_table_button.setAccessibleName("Open the source rows table")
        self.open_table_button.clicked.connect(self._show_current_source)
        source_head.addWidget(self.open_table_button)
        source.addLayout(source_head)
        self.source_row_table = QTableWidget(0, len(SOURCE_ROW_COLUMNS))
        self.source_row_table.setObjectName("sourceRowTable")
        self.source_row_table.setAccessibleName("Source row at the selected minute")
        self.source_row_table.setHorizontalHeaderLabels(list(SOURCE_ROW_COLUMNS))
        _align_headers(self.source_row_table, right=(1,))
        self.source_row_table.setShowGrid(False)
        self.source_row_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.source_row_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        # Tab leaves the table for the next control instead of walking its cells.
        self.source_row_table.setTabKeyNavigation(False)
        self.source_row_table.setItemDelegate(MissingCellDelegate(self.source_row_table))
        self.source_row_table.setIconSize(QSize(*SWATCH_SIZE))
        header = self.source_row_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(False)
        _fit_rows(self.source_row_table, 0)
        source.addWidget(self.source_row_table)
        self.context_button = _link_button("Grounded context")
        self.context_button.setAccessibleName("Show grounded context")
        self.context_button.clicked.connect(self._show_grounded_context)
        source.addWidget(self.context_button, alignment=Qt.AlignmentFlag.AlignLeft)
        inspector.addLayout(source)

        episodes = QVBoxLayout()
        episodes.setSpacing(4)
        episodes_head = QHBoxLayout()
        episodes_head.setSpacing(10)
        episodes_title = QLabel("Rule episodes")
        episodes_title.setObjectName("stepHeading")
        episodes_head.addWidget(episodes_title)
        screening = QLabel("screening only")
        screening.setObjectName("quietText")
        episodes_head.addWidget(screening)
        episodes_head.addStretch(1)
        episodes.addLayout(episodes_head)
        self.episode_list = EpisodeList()
        self.episode_list.setObjectName("episodeList")
        self.episode_list.setAccessibleName("Rule episodes")
        self.episode_list.setItemDelegate(EpisodeDelegate(self.episode_list))
        self.episode_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.episode_list.itemSelectionChanged.connect(self._episode_selection_changed)
        episodes.addWidget(self.episode_list)
        inspector.addLayout(episodes)

        rule = QVBoxLayout()
        rule.setSpacing(6)
        # The id sits under the name: a long rule name would otherwise wrap beside it.
        self.rule_title = _wrap()
        self.rule_title.setObjectName("stepHeading")
        rule.addWidget(self.rule_title)
        self.rule_id_label = QLabel()
        self.rule_id_label.setObjectName("quietText")
        rule.addWidget(self.rule_id_label)
        self.rule_description = _wrap()
        self.rule_description.setObjectName("caseFacts")
        self.rule_description.setTextFormat(Qt.TextFormat.PlainText)
        rule.addWidget(self.rule_description)
        self.threshold_ruler = ThresholdRuler()
        rule.addWidget(self.threshold_ruler)
        self.rule_detail = _wrap(NO_EPISODE_SELECTED)
        self.rule_detail.setObjectName("caseFacts")
        self.rule_detail.setTextFormat(Qt.TextFormat.PlainText)
        rule.addWidget(self.rule_detail)
        rule_actions = QHBoxLayout()
        rule_actions.setSpacing(8)
        self.rule_link = _link_button("Rule reference")
        self.rule_link.setAccessibleName("Show the rule in the Rules drawer")
        self.rule_link.clicked.connect(self._show_selected_rule)
        rule_actions.addWidget(self.rule_link)
        self.clear_episode_button = _link_button("Clear episode selection")
        self.clear_episode_button.clicked.connect(self.clear_episode_selection)
        rule_actions.addWidget(self.clear_episode_button)
        self._set_episode_actions(False)
        rule_actions.addStretch(1)
        rule.addLayout(rule_actions)
        inspector.addLayout(rule)
        inspector.addStretch(1)
        self._show_rule_widgets(False)
        self._fit_episode_list(1)

        # Focus follows the screen: back, the chart column from the top, then the inspector from the top.
        order = (self.back_button, self.more_signals_button, self.detail_chart, self.minute_slider,
                 self.follow_up_edit, self.follow_up_button, self.follow_up_cancel, self.open_table_button,
                 self.source_row_table, self.context_button, self.episode_list, self.rule_link,
                 self.clear_episode_button)
        for earlier, later in zip(order, order[1:], strict=False):
            QWidget.setTabOrder(earlier, later)
        self.signal_scroll = QScrollArea()
        self.signal_scroll.setObjectName("signalScroll")
        self.signal_scroll.setWidgetResizable(True)
        self.signal_scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.signal_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.signal_scroll.setWidget(page)
        self.stack.addWidget(self.signal_scroll)
        self.stack.currentChanged.connect(lambda _index: self._reset_follow_up(clear_question=True))

    def _show_rule_widgets(self, shown: bool) -> None:
        """The selected rule's name, description and ruler show only while an episode is selected."""
        for widget in (self.rule_title, self.rule_id_label, self.rule_description):
            widget.setVisible(shown)
        if not shown:
            self.threshold_ruler.set_ruler(None)

    def _fit_episode_list(self, rows: int) -> None:
        """Size the episode list to its rows, up to EPISODE_ROWS_SHOWN; beyond that it scrolls inside itself."""
        self.episode_list.setFixedHeight(min(max(rows, 1), EPISODE_ROWS_SHOWN) * EPISODE_ROW_HEIGHT + 4)

    def selected_model(self) -> str | None:
        return self.model_menu.selected_model()

    def set_models(self, models: list[str], current: str | None = None, placeholder: str = "") -> None:
        """Show the installed models in the menu. A change made here never counts as the user's choice."""
        self.model_menu.set_models(models, current, placeholder)

    def set_model_state(self, state: str) -> None:
        """The local model's state word on the menu button: unchecked, ready, stopped, failed or busy:<word>."""
        self.model_menu.set_state(state)

    def set_model_status(self, text: str, *, rich: bool = False, relevant: bool = True) -> None:
        """The Ollama status line under the question. It is hidden while everything is fine."""
        self.model_status.setTextFormat(Qt.TextFormat.RichText if rich else Qt.TextFormat.PlainText)
        self.model_status.setText(text)
        self.model_status.setVisible(relevant and bool(text))

    @property
    def scope(self) -> str:
        return self._scope

    def set_article_catalog(self, count: int, error: str = "") -> None:
        self._catalog_count, self._article_error = count, error
        if self.scope == "Articles":
            self._render_scope()

    def _ask_again(self, question: str, scope: str) -> None:
        self.scope_switch.set_scope(scope)
        self.question_submitted.emit(question)

    def _change_scope(self, scope: str) -> None:
        self._stop_running_question()
        self.board_restore_requested.emit()
        self._detach_board()
        self._scope = scope
        self.scope_switch.set_scope(scope, notify=False)
        self._clear_board()

    def _set_extract_text(self, text: str) -> None:
        self._extract_text = text
        self.model_menu.set_intro(f"{text} Search works without it.")

    def _render_scope(self) -> None:
        articles = self.scope == "Articles"
        self.article_card.setVisible(articles)
        self.case_shortlist_card.setVisible(not articles)
        self.observation_step.setVisible(not articles)
        self.limit_step.setVisible(not articles)
        self.comparison_card.setVisible(not articles and self._comparison_pid is not None)
        self.add_comparison_button.setVisible(not articles)
        self._set_extract_text(ARTICLES_EXTRACTS if articles else CASES_EXTRACTS)
        self.question_edit.setPlaceholderText(ARTICLES_PLACEHOLDER if articles else CASES_PLACEHOLDER)
        # The scope line belongs beside the step that is showing.
        head = self._articles_head if articles else self._shortlist_head
        if head.indexOf(self.scope_label) < 0:
            head.insertWidget(1, self.scope_label, 1)
        if not articles:
            self.scope_label.setText(self._case_scope())
        if articles:
            self.scope_label.setText(f"Articles: {self._catalog_count:,} in the local catalog")
            self.status_label.setText(self._article_error or (NO_CATALOG if not self._catalog_count else "Ready to search local articles."))
        ready = not self._searching and (articles or not self._setup_required)
        self.ask_button.setEnabled(ready and not (articles and self._article_error))
        self.question_edit.setEnabled(ready)
        self.scope_actions[self.scope].setChecked(True)
        self._sync_actions()
        self._layout_mode = ""
        self._apply_board_layout()

    def set_setup_required(self, required: bool) -> None:
        self._setup_required = required
        if self.scope == "Articles":
            self._render_scope()
            return
        self.ask_button.setEnabled(not required)
        self.question_edit.setEnabled(not required)
        self._sync_actions()
        self.status_label.setText(
            "Setup required. Choose a valid EMR folder before searching."
            if required else "Ready to search indexed surgeries."
        )

    def set_searching(self, active: bool) -> None:
        ready = not active and (self.scope == "Articles" or not self._setup_required)
        self.ask_button.setEnabled(ready)
        self.question_edit.setEnabled(ready)
        self._searching = active
        self.cancel_button.setVisible(active)
        self.add_comparison_button.setEnabled(not active)
        self.inspect_button.setEnabled(not active and self._selected_pid is not None)
        self.compare_button.setEnabled(not active and self._selected_pid is not None)
        self._sync_actions()
        self._apply_board_layout()

    def _submit_with_shortcut(self) -> None:
        # Enter in the field already submits; the shortcut also works from the switch, the menu and the trail.
        if self.ask_button.isEnabled():
            self._submit_question()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self.board_scroll.viewport() and event.type() == QEvent.Type.Resize:
            self._apply_board_layout()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.rules_drawer.setFixedWidth(min(RULES_DRAWER_MAX_WIDTH, int(self.width() * RULES_DRAWER_SHARE)))
        self._apply_board_layout()

    def _apply_board_layout(self) -> None:
        """Re-flow the board and conversation for the width left beside the rules drawer."""
        board_width = self.width() - (self.rules_drawer.width() if self.rules_drawer.is_open else 0)
        stacked = board_width < 1100
        self.board_splitter.setMinimumHeight(640 if stacked else 0)
        orientation = Qt.Orientation.Vertical if stacked else Qt.Orientation.Horizontal
        if self.board_splitter.orientation() != orientation:
            self.board_splitter.setOrientation(orientation)
            self.board_splitter.setSizes([max(300, self.height() - 360), 340] if stacked
                                         else [max(600, board_width - CONVERSATION_WIDTH), CONVERSATION_WIDTH])
        self._layout_mode = "wide" if board_width >= 1100 else "narrow" if board_width >= 640 else "single"
        room = board_width - (0 if stacked else CONVERSATION_WIDTH) - PAGE_MARGINS[0] - PAGE_MARGINS[2]
        # The page never has more than its viewport, which a scroll bar narrows.
        room = min(room, self.board_scroll.viewport().width() - PAGE_MARGINS[0] - PAGE_MARGINS[2])
        self._fit_controls(room)
        side_by_side = room - GUTTER >= SIDE_BY_SIDE_WIDTH
        self._observation_columns.setDirection(
            QBoxLayout.Direction.LeftToRight if side_by_side else QBoxLayout.Direction.TopToBottom
        )
        # The two case actions stack when the trail is too narrow to hold them side by side.
        self.inspect_button.ensurePolished()
        self.compare_button.ensurePolished()
        pair = self.inspect_button.sizeHint().width() + self._picks_row.spacing() + self.compare_button.sizeHint().width()
        self._picks_row.setDirection(
            QBoxLayout.Direction.LeftToRight if room - GUTTER >= pair else QBoxLayout.Direction.TopToBottom
        )

    def _fit_controls(self, room: int) -> None:
        """Lay the controls out in as few rows as the width allows.

        One row when everything fits; otherwise the actions drop under the choices, on the right;
        and when even the actions are too wide, the Trace button drops under the other actions.
        The scope switch and the model menu stack the same way when they are too wide for the board.
        """
        # Unpolished buttons report plainer, narrower sizes than the styled ones will have.
        for control in (self.scope_switch, self.model_menu, self.new_question_button, self.add_comparison_button,
                        self.cancel_button, self.ask_button):
            control.ensurePolished()
        # Summed from the parts, not read from the layout, whose own width changes when it stacks.
        choices = self.scope_switch.sizeHint().width() + self._choices_row.spacing() + self.model_menu.sizeHint().width()
        self._choices_row.setDirection(
            QBoxLayout.Direction.LeftToRight if choices <= room else QBoxLayout.Direction.TopToBottom
        )
        shown = [button.sizeHint().width() for button in (self.new_question_button, self.add_comparison_button,
                                                           self.cancel_button) if not button.isHidden()]
        secondary = sum(shown) + self._secondary_row.spacing() * max(len(shown) - 1, 0)
        # Even the two secondary actions side by side can be too wide for the board; then they stack too.
        side_by_side = secondary <= room
        self._secondary_row.setDirection(
            QBoxLayout.Direction.LeftToRight if side_by_side else QBoxLayout.Direction.TopToBottom
        )
        for button in (self.new_question_button, self.add_comparison_button, self.cancel_button):
            self._secondary_row.setAlignment(button, Qt.AlignmentFlag(0) if side_by_side else Qt.AlignmentFlag.AlignRight)
        ask = self.ask_button.sizeHint().width()
        actions = secondary + self._actions_row.spacing() + ask
        one_row = choices + CONTROLS_GAP + actions <= room
        self._controls_layout.setDirection(
            QBoxLayout.Direction.LeftToRight if one_row else QBoxLayout.Direction.TopToBottom
        )
        self._controls_layout.setSpacing(CONTROLS_GAP if one_row else 10)
        self._controls_layout.setAlignment(
            self._actions_row, Qt.AlignmentFlag(0) if one_row else Qt.AlignmentFlag.AlignRight
        )
        stacked = not one_row and actions > room
        self._actions_row.setDirection(
            QBoxLayout.Direction.TopToBottom if stacked else QBoxLayout.Direction.LeftToRight
        )
        self._actions_row.setAlignment(self.ask_button, Qt.AlignmentFlag.AlignRight if stacked else Qt.AlignmentFlag(0))
        self._actions_row.setAlignment(self._secondary_row, Qt.AlignmentFlag.AlignRight if stacked else Qt.AlignmentFlag(0))

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
            self.detail_chart.set_rule_labels(self._rule_label_map())
            if self._current_pid in self._case_views:
                self._render_case_header(self._case_views[self._current_pid])
            if selected >= 0:
                self.episode_list.setCurrentRow(selected)

    def open_rules(self, opener: QWidget | None = None) -> None:
        self.rules_drawer.open(opener)
        self._apply_board_layout()

    def show_rule(self, rule_id: str) -> None:
        self.rules_drawer.show_rule(rule_id)
        self._apply_board_layout()

    def _rules_fallback_focus(self) -> QWidget:
        return self.question_edit if self.question_edit.isVisible() else self.back_button

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        # The open rules drawer owns Escape; a second Escape shortcut here would make Qt drop both as ambiguous.
        if (event.key() == Qt.Key.Key_Escape and self.stack.currentIndex() == 1
                and not self.rules_drawer.isVisible()):
            event.accept()
            self.return_to_canvas()
            return
        # The window's Reload action also uses Ctrl+R. event() claims the key while focus is
        # inside the workspace so the action does not fire there.
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
                self.source_title, self.source_label, self.selection_label, self.finding_context)

    def _snapshot_board(self) -> QuestionState:
        return QuestionState(
            self.result, self._question, self.answer_text.toPlainText(), self._selected_pid,
            self._primary_pid, self._comparison_pid, dict(self._case_views),
            tuple(label.text() for label in self._board_labels()), self.scope, self.article_result,
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
        turn = Turn(self._active_turn_request, question, self.selected_model(), scope=self.scope)
        turn.board = self._snapshot_board()
        self.conversation.append_turn(turn)
        self.set_searching(True)
        self.place_changed.emit()

    def _restore_board(self, state: QuestionState) -> None:
        self._restoring_board = True
        try:
            self._scope = state.scope
            self.scope_switch.set_scope(state.scope, notify=False)
            self._clear_board()
            if state.article_result is not None:
                self.set_article_results(state.article_result)
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
                        self._fit_shortlist()
            self.answer_text.setPlainText(state.answer)
            for label, text in zip(self._board_labels(), state.labels, strict=True):
                label.setText(text)
            self.stack.setCurrentIndex(0)
        finally:
            self._restoring_board = False
        self.place_changed.emit()

    def cancel_question(self) -> None:
        state = self._before_question
        turn = self.conversation.turn(self._active_turn_request)
        if self._status_is_progress:
            # The cancelled turn keeps its board; it must not keep the running step
            self.status_label.setText("Search cancelled.")
            self._status_is_progress = False
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

    def set_progress(self, message: str, phase: str = "", total: int = 0) -> None:
        """Show a running step of the current search; finish_progress replaces it.

        The running question's entry in the conversation lists the same steps with a bar.
        """
        self.set_status(message)
        self._status_is_progress = True
        turn = self.conversation.turn(self._active_turn_request)
        if turn is not None and turn.state == "running":
            turn.advance(message, phase, PHASE_PROGRESS.get(phase, PHASE_PROGRESS[""]))
            if phase == "fetching" and total:
                turn.extracts = total
            self.conversation.update_turn(turn)

    def finish_progress(self) -> None:
        """Replace a running step left on the status line once its search has ended."""
        if self._status_is_progress:
            self.set_status("Search finished.")

    def set_status(self, message: str) -> None:
        self._status_is_progress = False
        self.status_label.setText(message)
        announce(self.status_label, message)
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
        self.place_changed.emit()
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
            labels[7] = ""
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
        self.article_result = None
        self.article_list.clear()
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
        self._show_all_rows = False
        self._fit_shortlist()
        self._clear_observation()
        self.answer_text.clear()
        self.comparison_card.hide()
        self._case_scope_text = None
        self.scope_label.setText(self._case_scope())
        self._set_case_actions(False)
        self.set_status("Enter a research question to trace its evidence.")
        self._render_scope()

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

    def _clear_observation(self) -> None:
        self.sparkline.clear(SPARK_WAITING)
        self.source_table.setRowCount(0)
        self.source_table.hide()
        self.finding_context.hide()
        self.finding_context.clear()
        self.finding_open.setText("Open the signal")

    def _show_observation(self, view: CaseView, ts: pd.DataFrame) -> str:
        """Draw the observation's sparkline, button and rows; returns the rows' provenance sentence."""
        index, column = self._observed(view)[2:]
        label, color = _SPARK_SIGNALS[column]
        minute = float(ts.iloc[index]["t_min"]) if not ts.empty and "t_min" in ts else None
        if column not in ts or not ts[column].notna().any():
            self.sparkline.clear(f"{label} / no observed samples")
        else:
            values = pd.to_numeric(ts[column], errors="coerce")
            self.sparkline.set_series(
                [None if pd.isna(value) else float(value) for value in values], index,
                f"{label} over the case, observation at minute {minute:g}" if minute is not None
                else f"{label} over the case",
                label=label, color=color,
            )
        if minute is not None:
            self.finding_open.setText(f"Open at minute {minute:g}")
            first, last = float(ts["t_min"].iloc[0]), float(ts["t_min"].iloc[-1])
            self.finding_context.setText(
                f"{view.candidate.pid} · one case, {len(ts)} minute rows, minutes {first:g}–{last:g} relative to incision"
            )
            self.finding_context.show()
        return self._fill_source_rows(ts, index)

    def _fill_source_rows(self, ts: pd.DataFrame, index: int) -> str:
        """Three rows around the observed minute, with missing cells hatched; returns where they sit in the case."""
        if ts.empty:
            self.source_table.setRowCount(0)
            self.source_table.hide()
            return ""
        start = min(max(0, index - 1), max(0, len(ts) - SOURCE_ROWS))
        stop = min(len(ts), start + SOURCE_ROWS)
        self.source_table.setRowCount(stop - start)
        keys = ("t_min", "PIP", "ETCO2", "HR")
        for table_row, frame_row in enumerate(range(start, stop)):
            source = ts.iloc[frame_row]
            for column, key in enumerate(keys):
                value = source.get(key)
                missing = pd.isna(value)
                cell = QTableWidgetItem("missing" if missing else f"{float(value):g}")
                cell.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                mark_missing(cell, bool(missing))
                self.source_table.setItem(table_row, column, cell)
        self.source_table.selectRow(index - start)
        _fit_rows(self.source_table, stop - start)
        self.source_table.show()
        return (f"Rows {start + 1}–{stop} of {len(ts)} · minutes relative to incision. "
                "Missing stays missing; nothing is filled in.")

    def set_article_results(self, result: ArticleSearchResult) -> None:
        self.article_result = result
        self._catalog_count = result.catalog_count
        self.result = None
        self.article_list.clear()
        self.scope_label.setText(f"Articles: {result.catalog_count:,} in the local catalog")
        self.set_status(result.message)
        turn = self.conversation.turn(self._active_turn_request)
        if turn is not None and turn.model and turn.state == "running":
            turn.extracts = min(len(result.articles), ABSTRACT_LIMIT)
            self.conversation.update_turn(turn)
        for i, article in enumerate(result.articles, 1):
            citation = f"[A{i}] " if i <= ABSTRACT_LIMIT else ""
            snippet = article.abstract[:320] + ("…" if len(article.abstract) > 320 else "")
            item = QListWidgetItem(
                f"{citation}{article.title} ({article.year})\n{article.authors}\n"
                f"{article.doi_or_url}\n{snippet}"
            )
            item.setToolTip(f"Source: {article.source}\nRights: {article.rights_note}\nImported: {article.imported_at}")
            self.article_list.addItem(item)
        if not result.articles:
            self.article_list.addItem(result.message)
        self._save_board()

    def set_results(self, result: RetrievalResult) -> None:
        self.result = result
        self._primary_pid = None
        self._comparison_pid = None
        self._case_views.clear()
        self.comparison_card.hide()
        self._clear_observation()
        ranking = result.rank_method
        if ranking == "embedding":
            ranking += f", {result.embedded_count:,} of {result.indexed_count:,} records indexed"
            if result.embedded_count < result.indexed_count:
                ranking += "; the rest ranked by keywords"
        else:
            ranking = "keywords"
        self._case_scope_text = (
            result.scope_note
            or f"Indexed surgeries: {result.indexed_count:,} · analyzed cases: {result.analyzed_count:,} · ranking: {ranking}"
        )
        self.scope_label.setText(self._case_scope_text)
        self.candidate_table.setRowCount(len(result.candidates))
        for row, candidate in enumerate(result.candidates):
            values = (str(row + 1), candidate.pid, candidate.procedure, "", candidate.source_label)
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                if column in (0, 3):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.candidate_table.setItem(row, column, item)
        self._refresh_durations()
        self._show_all_rows = False
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
        self._fit_shortlist()
        self._set_case_actions(bool(result.candidates))
        self._save_board()

    def _case_duration(self, pid: str) -> tuple[str, bool]:
        """The case's length as text, and whether it is the analyzed-case table's own figure.

        The second value is False for the fallback, the last minute of the opened case's data, which is how long
        the signals run and not a recorded surgery length. A dash while neither is known.
        """
        cases = self._cases_data[0] if self._cases_data is not None else None
        if cases is not None and {"PID", "case_duration_min"} <= set(cases.columns):
            known = pd.to_numeric(cases.loc[cases["PID"].astype(str) == pid, "case_duration_min"], errors="coerce").dropna()
            if not known.empty:
                return f"{known.iloc[0]:g} min", True
        view = self._case_views.get(pid)
        if view is not None and "t_min" in view.timeseries and not view.timeseries.empty:
            return f"{float(view.timeseries['t_min'].max()):g} min", False
        return "—", False

    def _duration_text(self, pid: str) -> str:
        """Minutes from the analyzed-case table, or from the opened case; a dash while neither is known."""
        return self._case_duration(pid)[0]

    def _refresh_durations(self) -> None:
        for row in range(self.candidate_table.rowCount()):
            pid_item = self.candidate_table.item(row, 1)
            duration = self.candidate_table.item(row, 3)
            if pid_item is not None and duration is not None:
                text = self._duration_text(pid_item.text())
                duration.setText(text)
                duration.setToolTip(text)

    def _fit_shortlist(self) -> None:
        """Show the first rows, plus the selected one, and size the table to them; Show all reveals the rest."""
        total = self.candidate_table.rowCount()
        self.candidate_table.setVisible(total > 0)
        keep = total if self._show_all_rows else min(total, max(SHORTLIST_COLLAPSED_ROWS, self.candidate_table.currentRow() + 1))
        for row in range(total):
            self.candidate_table.setRowHidden(row, row >= keep)
        _fit_rows(self.candidate_table, keep)
        more = total > SHORTLIST_COLLAPSED_ROWS
        self.show_all_button.setVisible(more)
        self.show_all_button.setText("Show fewer" if self._show_all_rows else f"Show all {total}")

    def _toggle_show_all(self) -> None:
        self._show_all_rows = not self._show_all_rows
        self._fit_shortlist()

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
            announce(self.conversation, f"Research answer ready for {turn.question}.")

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
        self._refresh_durations()
        if comparison:
            self._comparison_pid = pid
            self.comparison_card.show()
            self.comparison_label.setText(self._case_summary(view))
        else:
            self._primary_pid = pid
            self.finding_title.setText(self._observation(view)[0])
            self.finding_copy.setText(self._observation(view)[1])
            self.source_title.setText(pid)
            provenance = self._show_observation(view, ordered)
            self.source_label.setText(self._case_summary(view) + (f"\n{provenance}" if provenance else ""))
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
        self._fit_shortlist()
        self.selection_label.setText(
            f"Selected {candidate.pid} · {candidate.source_label} · {candidate.procedure or 'procedure not recorded'}"
        )
        self._set_case_actions(True)
        self._save_board()

    def _set_case_actions(self, enabled: bool) -> None:
        self.inspect_button.setEnabled(enabled)
        self.compare_button.setEnabled(enabled)
        self.finding_open.setEnabled(enabled)
        self.all_rows_button.setEnabled(enabled)

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
                self.comparison_label.setText(self._case_summary(self._case_views[pid]))
                self._save_board()
        else:
            self._case_origins.setdefault(pid, deque()).append(from_overview)
            self.case_requested.emit(pid, comparison, open_detail)

    def _observation(self, view: CaseView) -> tuple[str, str, int]:
        title, copy, index, _signal = self._observed(view)
        return title, copy, index

    def _observed(self, view: CaseView) -> tuple[str, str, int, str]:
        """The observation's title, sentence, row index, and the signal column it is about."""
        ts = view.timeseries
        if ts.empty:
            return "Signal source unavailable", "This case has no time series to inspect.", 0, "PIP"
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
                    "ETCO2",
                )
        if "PIP" in ts and ts["PIP"].notna().any():
            values = pd.to_numeric(ts["PIP"], errors="coerce")
            index = int(values.idxmax())
            row = ts.iloc[index]
            return (
                f"Peak sampled PIP {values.iloc[index]:g} cmH₂O at {row['t_min']:g} min",
                "This is the highest observed PIP in the loaded case. The measurements do not establish its cause.",
                index,
                "PIP",
            )
        return "Case signals loaded", "Open F to inspect the recorded samples and missing values.", 0, "PIP"

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
        self._reset_follow_up(clear_question=True)
        self.rules_drawer.set_thresholds_sha256(view.thresholds_sha256)
        self._minute_rows = focused_minute_rows(view.timeseries).reset_index()
        self.selected_rule_id = None
        self.signal_statistics = {}
        self._show_rule_widgets(False)
        self.rule_detail.setText(NO_EPISODE_SELECTED)
        self._set_episode_actions(False)
        self._episodes = view.episodes.sort_values(["t_start_min", "rule_id"]).reset_index(drop=True) if not view.episodes.empty else pd.DataFrame()
        self._build_episode_list()
        self.detail_chart.set_case(view.timeseries, view.flags, self._episodes, self._rule_label_map())
        self._rule_extras = []
        self._apply_extra_tracks()
        self._detail_from_overview = from_overview
        self.back_button.setText("← Back to cases" if from_overview else "← Back to evidence trail")
        self.back_button.setAccessibleName("Back to cases" if from_overview else "Back to evidence trail")
        self._render_case_header(view)
        self.minute_slider.blockSignals(True)
        self.minute_slider.setRange(0, len(self._minute_rows) - 1)
        peak = float(view.timeseries.iloc[self._observation(view)[2]]["t_min"])
        self.minute_slider.setValue(int((self._minute_rows["t_min"] - peak).abs().argmin()))
        self.minute_slider.blockSignals(False)
        self.stack.setCurrentIndex(1)
        self.back_button.setFocus()
        self._render_minute(self.minute_slider.value())

    def _rule_label_map(self) -> dict[str, str]:
        return {spec.id: spec.label for spec in self._rule_catalog.specs} if self._rule_catalog is not None else {}

    def _render_case_header(self, view: CaseView) -> None:
        """Where the case came from, its procedure, and the one facts line."""
        pid = view.candidate.pid
        origin = "the Cases list" if self._detail_from_overview else "the evidence trail"
        self.detail_context.setText(f"{pid} · opened from {origin} · {view.candidate.source_label}")
        self.detail_title.setText(view.candidate.procedure or "Procedure not recorded")
        age = view.candidate.age
        facts = []
        duration, from_table = self._case_duration(pid)
        if duration != "—":
            facts.append(f"{duration} surgery" if from_table else f"{duration} recorded")
        if age is None or pd.isna(age):
            facts.append("age not recorded")
        else:
            facts.append(f"age {int(age) if float(age).is_integer() else age}")
        facts.append("1 sample/min")
        preset = self._rule_catalog.preset if self._rule_catalog is not None else "default"
        facts.append(f"rules {preset} @{view.thresholds_sha256[:12]}" if view.thresholds_sha256
                     else f"rules {preset}, thresholds not recorded")
        count = len(self._episodes)
        facts.append("no rule episodes" if not count else f"{count} rule episode{'s' if count != 1 else ''}")
        self.detail_facts.setText(" · ".join(facts))

    def _choose_signals(self) -> None:
        if self._current_pid is None:
            return
        from PySide6.QtWidgets import QCheckBox, QDialogButtonBox

        dialog = QDialog(self)
        dialog.setWindowTitle("More signals")
        layout = QVBoxLayout(dialog)
        layout.addWidget(_wrap("Choose up to three extra tracks. Missing samples remain missing."))
        ts = self._case_views[self._current_pid].timeseries
        legacy = ["TV", "PEEP", "Agent_Et", "Agent_Fi", "MAC_Et", "SPO2", "nMAP"]
        available = list(dict.fromkeys([*legacy, *(str(c) for c in ts.select_dtypes(include="number").columns)]))
        available = [signal for signal in available if signal not in
                     {"PIP", "ETCO2", "HR", "t_min", "Age", "case_frac", "case_duration_min", "likely_mech_vent"}]
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        choices = QWidget()
        choice_layout = QVBoxLayout(choices)
        boxes = []
        for signal in available:
            box = QCheckBox(signal)
            box.setChecked(signal in self._user_extras)
            if signal not in ts or not ts[signal].notna().any():
                box.setToolTip("No recorded samples for this signal in this case.")
            choice_layout.addWidget(box)
            boxes.append(box)
        scroll.setWidget(choices)
        layout.addWidget(scroll)

        def limit_choices() -> None:
            full = sum(box.isChecked() for box in boxes) >= 3
            for box in boxes:
                box.setEnabled(box.isChecked() or not full)

        for box in boxes:
            box.toggled.connect(limit_choices)
        limit_choices()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(350, 480)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._user_extras = [box.text() for box in boxes if box.isChecked()][:3]
            self._apply_extra_tracks()
            if self.selected_rule_id is not None:
                self._render_rule_detail()

    def _wanted_extras(self) -> list[str]:
        """The user's choices first, then the selected rule's other signals, three at most."""
        wanted = dict.fromkeys([*self._user_extras, *self._rule_extras])
        return [signal for signal in wanted if signal not in {"PIP", "ETCO2", "HR"}][:3]

    def _apply_extra_tracks(self) -> None:
        ts = self._case_views[self._current_pid].timeseries if self._current_pid in self._case_views else pd.DataFrame()
        # A rule's signal with no samples keeps its track, since the rule detail reports the gap;
        # a signal the user asked for would only show an empty track, so it gets a note instead.
        silent = [signal for signal in self._wanted_extras()
                  if signal in self._user_extras and (signal not in ts or not ts[signal].notna().any())]
        charted = [signal for signal in self._wanted_extras() if signal not in silent]
        self.chart_track_names = ["PIP", "ETCO2", "HR", *charted]
        self.detail_chart.set_tracks(charted)
        self.extras_note.setText("\n".join(f"No {signal} samples in this case" for signal in silent))
        self.extras_note.setVisible(bool(silent))
        self.more_signals_button.setText("+ Track" + (f" ({len(self._user_extras)}/3)" if self._user_extras else ""))
        if self._current_pid is not None and 0 <= self.minute_slider.value() < len(self._minute_rows):
            self._fill_source_row(self._minute_rows.iloc[self.minute_slider.value()])

    def _render_rule_detail(self) -> None:
        wanted = self._wanted_extras()
        self.rule_detail.setText("\n".join(
            text + ("; not charted" if signal in self._rule_extras and signal not in wanted else "")
            for signal, text in self._rule_lines
        ))

    def _show_grounded_context(self) -> None:
        if self._current_pid is None:
            return
        from src.llm.case_context import build_case_context

        view = self._case_views[self._current_pid]
        context = build_case_context(self._current_pid, cases=view.cases, timeseries=view.timeseries,
                                     flags=view.flags, episodes=view.episodes, events=view.events,
                                     include_emr_extras=True)
        box = QMessageBox(self)
        box.setWindowTitle("Grounded case context")
        box.setText("Structured source briefing for this case.")
        box.setDetailedText(context[:20000])
        box.exec()

    def _render_minute(self, index: int) -> None:
        if self._current_pid is None:
            return
        row = self._minute_rows.iloc[index]
        minute = float(row["t_min"])
        if self._follow_up_key not in (None, (self._current_pid, minute)):
            self._reset_follow_up()
        self.minute_slider.setAccessibleDescription(f"{minute:g} minutes relative to incision")
        self.minute_stamp.setText(f"{minute:g}")
        self.follow_up_edit.setPlaceholderText(f"Ask about minute {minute:g}")
        self.detail_chart.set_minute(minute)
        self._fill_source_row(row)
        self.place_changed.emit()

    def _fill_source_row(self, row: pd.Series) -> None:
        """The selected minute's values: the three main signals, SpO₂ and MAP when the case has them, chosen tracks."""
        minute = float(row["t_min"])
        self.source_heading.setText(f"Source row at minute {minute:g}")
        ts = self._case_views[self._current_pid].timeseries if self._current_pid in self._case_views else pd.DataFrame()
        charted = set(self.chart_track_names)
        columns = [*BASE_TRACKS, *(name for name in ("SPO2", "nMAP") if name in ts)]
        columns += [name for name in self.chart_track_names if name not in columns]
        table = self.source_row_table
        table.setRowCount(len(columns))
        for table_row, column in enumerate(columns):
            info = signal_info(column)
            value = pd.to_numeric(row.get(column), errors="coerce")
            missing = bool(pd.isna(value))
            name = QTableWidgetItem(info.label)
            name.setIcon(swatch_icon(info.token if column in charted else None))
            shown = QTableWidgetItem("missing" if missing else f"{float(value):g}")
            shown.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            mark_missing(shown, missing)
            unit = QTableWidgetItem(info.unit)
            unit.setForeground(token_color("ink_3"))
            for table_column, item in enumerate((name, shown, unit)):
                table.setItem(table_row, table_column, item)
        _fit_rows(table, len(columns))

    def _covering_episode(self, minute: float) -> pd.Series | None:
        if self._episodes.empty:
            return None
        covering = self._episodes[self._episodes["t_start_min"].le(minute) & self._episodes["t_end_min"].ge(minute)]
        if covering.empty:
            return None
        chosen = covering[covering["rule_id"].eq(self.selected_rule_id)]
        if chosen.empty:
            ranked = covering.assign(_severity=covering["severity"].map({"critical": 0, "warn": 1, "info": 2}).fillna(3))
            chosen = ranked.sort_values(["_severity", "t_start_min", "rule_id"])
        return chosen.iloc[0]

    def _minute_extract(self, pid: str, minute: float) -> str:
        view = self._case_views[pid]
        episode = self._covering_episode(minute)
        label, signals, thresholds = "", [], []
        if episode is not None:
            spec = self._rule_spec(str(episode["rule_id"]))
            if spec is not None and self._rule_catalog is not None:
                label = spec.label
                signals = [signal for signal in spec.signals if signal not in
                           {"t_min", "case_frac", "likely_mech_vent", "Age", "Agent"}]
                thresholds = [*self._rule_catalog.thresholds_provenance(view.thresholds_sha256),
                              *threshold_parts(spec), *([spec.composition] if spec.composition else [])]
            else:
                thresholds = ["Current thresholds unavailable for this rule."]
        return build_minute_extract(
            candidate=view.candidate, rows=focused_minute_rows(view.timeseries), minute=minute,
            signals=["PIP", "ETCO2", "HR", *self._wanted_extras()],
            episode=episode.to_dict() if episode is not None else None,
            episode_label=label, episode_signals=signals, threshold_lines=thresholds,
        )

    def _ask_about_minute(self) -> None:
        question = self.follow_up_edit.text().strip()
        if not question or self._current_pid is None or self._follow_up_active is not None:
            return
        minute = float(self._minute_rows.iloc[self.minute_slider.value()]["t_min"])
        self._reset_follow_up()
        self._follow_up_key = (self._current_pid, minute)
        self.follow_up_label.setText(f"Minute {minute:g} · {self._current_pid}")
        self.follow_up_label.show()
        model = self.selected_model()
        if not model:
            self._show_follow_up(FOLLOW_UP_NO_MODEL)
            return
        self._follow_up_request += 1
        request_id = self._follow_up_active = self._follow_up_request
        worker = MinuteQuestionWorker(
            self._case_views[self._current_pid].candidate, self._minute_extract(self._current_pid, minute), question, model,
            self._rule_catalog.preset if self._rule_catalog is not None else "default", parent=self,
        )
        self._follow_up_workers[request_id] = worker
        worker.answer.connect(lambda response: self._on_follow_up(request_id, str(getattr(response, "answer", "") or "No answer was returned.")))
        worker.failed.connect(lambda message: self._on_follow_up(request_id, f"The question could not be answered: {message}"))
        worker.finished.connect(lambda: self._follow_up_workers.pop(request_id, None))
        self.follow_up_button.setEnabled(False)
        self.follow_up_cancel.show()
        self.follow_up_edit.setEnabled(False)
        self._show_follow_up("Asking the local model…")
        worker.start()

    def _on_follow_up(self, request_id: int, text: str) -> None:
        if request_id != self._follow_up_active:
            return
        self._follow_up_active = None
        self._set_follow_up_running(False)
        self._show_follow_up(text)

    def _show_follow_up(self, text: str) -> None:
        self.follow_up_answer.clear()
        self.follow_up_answer.append_markdown(text)
        self.follow_up_answer.show()

    def _set_follow_up_running(self, running: bool) -> None:
        self.follow_up_edit.setEnabled(not running)
        self.follow_up_button.setEnabled(not running)
        self.follow_up_cancel.setVisible(running)

    def _reset_follow_up(self, *, clear_question: bool = False) -> None:
        """Drop the answer and any running question; a late answer for it is then ignored."""
        worker = self._follow_up_workers.get(self._follow_up_active) if self._follow_up_active is not None else None
        if worker is not None and worker.isRunning():
            worker.requestInterruption()
        self._follow_up_active = None
        self._follow_up_key = None
        self._set_follow_up_running(False)
        self.follow_up_label.hide()
        self.follow_up_answer.clear()
        self.follow_up_answer.hide()
        if clear_question:
            self.follow_up_edit.clear()

    def follow_up_workers(self) -> list[MinuteQuestionWorker]:
        return list(self._follow_up_workers.values())

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
            severity = str(episode["severity"])
            start, end = episode["t_start_min"], episode["t_end_min"]
            minutes = f"min {start:g}" if start == end else f"min {start:g}–{end:g}"
            glyph = severity_glyph(severity)
            item = QListWidgetItem(f"{glyph} {label} · {severity} · {minutes}".strip())
            item.setData(GLYPH_ROLE, glyph)
            item.setData(NAME_ROLE, label)
            item.setData(SEVERITY_ROLE, severity)
            item.setData(RANGE_ROLE, minutes)
            item.setToolTip(f"{label} · {severity} · {self._episode_phase(episode)} · "
                            f"{start:g} to {end:g} min · {episode['duration_min']:g} min duration")
            self.episode_list.addItem(item)
        self._fit_episode_list(max(len(self._episodes), 1))
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
        ts = self._case_views[self._current_pid].timeseries
        rows = focused_minute_rows(ts)
        window = rows.loc[(rows.index >= episode["t_start_min"] - 1e-9)
                          & (rows.index <= episode["t_end_min"] + 1e-9)]
        self.signal_statistics = {}
        lines: list[tuple[str | None, str]] = []
        if spec and self._rule_catalog:
            lines.extend((None, line) for line in self._rule_catalog.thresholds_provenance(
                self._case_views[self._current_pid].thresholds_sha256
            ))
            lines.extend((None, part) for part in threshold_parts(spec))
            if spec.composition:
                lines.append((None, spec.composition))
        else:
            lines.append((None, "Current thresholds unavailable for this rule."))
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
            lines.append((signal, f"{signal}: {summary}; {stats['missing']} missing minutes"))
        lines.append((None, f"Phase: {self._episode_phase(episode)}"))
        self._rule_lines = lines
        self._rule_extras = [signal for signal in signals if signal not in {"PIP", "ETCO2", "HR"}]
        self._apply_extra_tracks()
        self._render_rule_detail()
        self.rule_title.setText(spec.label if spec else self.selected_rule_id)
        self.rule_id_label.setText(self.selected_rule_id if spec else "")
        self.rule_description.setText(spec.description if spec else "")
        self.rule_description.setVisible(bool(spec and spec.description))
        self.rule_title.show()
        self.rule_id_label.setVisible(spec is not None)
        extreme = None
        if spec is not None and spec.signals:
            measured = self.signal_statistics.get(spec.signals[0])
            extreme = measured["max" if spec.direction == "high" else "min"] if measured else None
        self.threshold_ruler.set_ruler(ruler_for(spec, extreme) if spec is not None else None)
        self.detail_chart.set_selected_episode(index)
        self._set_episode_actions(True)
        self._seek_to_episode(episode)

    def _seek_to_episode(self, episode) -> None:
        """Put the selected minute on the episode's first minute: the one path for choosing it and for clicking it again."""
        index = int((self._minute_rows["t_min"] - episode["t_start_min"]).abs().argmin())
        self.minute_slider.setValue(index)
        self._render_minute(index)

    def _set_episode_actions(self, selected: bool) -> None:
        """The rule reference and Clear mean something only while an episode is selected, so Tab skips them otherwise."""
        self.rule_link.setEnabled(selected)
        self.clear_episode_button.setEnabled(selected)

    def _episode_selection_changed(self) -> None:
        """The selected row is the one source: the rule, the tracks and the lane outline follow it, or all clear."""
        rows = [self.episode_list.row(item) for item in self.episode_list.selectedItems()]
        if rows:
            self._select_episode(rows[0])
        else:
            self.clear_episode_selection()

    def clear_episode_selection(self) -> None:
        self.episode_list.blockSignals(True)
        self.episode_list.setCurrentRow(-1)
        self.episode_list.clearSelection()
        self.episode_list.blockSignals(False)
        self.selected_rule_id = None
        self.signal_statistics = {}
        self._show_rule_widgets(False)
        self.rule_detail.setText(NO_EPISODE_SELECTED)
        self._set_episode_actions(False)
        self.detail_chart.set_selected_episode(None)
        self._rule_extras = []
        self._apply_extra_tracks()

    def _pick_episode(self, index: int) -> None:
        """A click on the lane selects the episode exactly as choosing it in the list does.

        On the episode that is already selected the click returns the minute to its start; nothing else resets.
        """
        if self.selected_rule_id is not None and self.episode_list.currentRow() == index:
            self._seek_to_episode(self._episodes.iloc[index])
            return
        self.episode_list.setCurrentRow(index)

    def _step_minute(self, steps: int) -> None:
        """A key on the chart moves the minute exactly as the same distance on the slider does."""
        self.minute_slider.setValue(self.minute_slider.value() + steps)

    def _select_next_episode(self) -> None:
        """The episode after the selected one in time order, the first after the last or when none is selected."""
        if len(self._episodes):  # no row is current at -1, so the next is the first
            self.episode_list.setCurrentRow((self.episode_list.currentRow() + 1) % len(self._episodes))

    def selected_rule_text(self) -> str:
        """The selected rule as the inspector writes it, name and id first; empty when no episode is selected."""
        if self.selected_rule_id is None:
            return ""
        return "\n".join(part for part in (
            f"{self.rule_title.text()} ({self.selected_rule_id})", self.rule_description.text(),
            self.rule_detail.text()) if part)

    def _show_selected_rule(self) -> None:
        if self.selected_rule_id is not None:
            self.show_rule(self.selected_rule_id)

    def place(self) -> Place:
        index = self.stack.currentIndex()
        turns = self.conversation.turns
        question = next((number for number, turn in enumerate(turns, 1) if turn.request_id == self._board_request), None)
        if index == CASES_PAGE:
            return Place("cases", question, len(turns))
        if index != 1 or self._current_pid is None:
            return Place("research", question, len(turns))
        minute = None
        if not self._minute_rows.empty and 0 <= self.minute_slider.value() < len(self._minute_rows):
            minute = float(self._minute_rows.iloc[self.minute_slider.value()]["t_min"])
        return Place("signal", question, len(turns), self._current_pid, minute, self._detail_from_overview)

    def has_open_case(self) -> bool:
        """True when the Signal page has a case to show."""
        return self._current_pid is not None and self._current_pid in self._case_views

    def show_canvas(self) -> None:
        """The question board, as the rail's Research item opens it."""
        self._leave_signal()
        self.stack.setCurrentIndex(0)

    def show_signal(self) -> bool:
        """The open case's page again, with its minute and episode as they were; False when no case is open."""
        view = self._case_views.get(self._current_pid) if self._current_pid is not None else None
        if view is None:
            return False
        self.rules_drawer.set_thresholds_sha256(view.thresholds_sha256)
        self.stack.setCurrentIndex(1)
        return True

    def _leave_signal(self) -> None:
        if self.stack.currentIndex() == 1:
            self.clear_episode_selection()
            self.rules_drawer.set_thresholds_sha256(self._thresholds_sha256)

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

    def set_cases_data(self, cases: pd.DataFrame | None, scan, catalog, flags: pd.DataFrame | None = None) -> None:
        """Hand over the loaded sample, the full-EMR scan and the rule catalog of the loaded preset."""
        self._cases_data = (cases, scan, catalog, flags)
        if self.scope != "Articles":
            self.scope_label.setText(self._case_scope())
        if self._cases_overview is not None:
            self._cases_overview.set_data(cases, scan, catalog, flags)

    def _case_scope(self) -> str:
        """The case scope line: the last search's counts, or the loaded sample before any search."""
        if self._case_scope_text is not None:
            return self._case_scope_text
        cases = self._cases_data[0] if self._cases_data is not None else None
        if cases is None:
            return "Indexed surgeries: — · analyzed cases: —"
        return f"Indexed surgeries: counted at the first search · analyzed cases: {len(cases):,}"

    def refresh_charts(self) -> None:
        self.detail_chart.refresh()
        if self._current_pid is not None and 0 <= self.minute_slider.value() < len(self._minute_rows):
            self._fill_source_row(self._minute_rows.iloc[self.minute_slider.value()])
        if self._cases_overview is not None:
            self._cases_overview.refresh_charts()

    def show_cases(self) -> None:
        self._leave_signal()
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
        self.question_edit.setFocus()

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
        layout.addWidget(_wrap(f"Rows {start + 1}–{stop} of {len(ts)} · minutes relative to incision · hatched cells are missing"))
        signals = self.chart_track_names if index is not None else ["PIP", "ETCO2", "HR"]
        columns = ["t_min", *signals]
        headings = {"t_min": "Minute", "PIP": "PIP cmH₂O", "ETCO2": "ETCO₂ mmHg", "HR": "Heart rate bpm"}
        table = QTableWidget(stop - start, len(columns))
        table.setHorizontalHeaderLabels([headings.get(signal, signal) for signal in columns])
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setItemDelegate(MissingCellDelegate(table))
        for table_row, frame_row in enumerate(range(start, stop)):
            source = ts.iloc[frame_row]
            for column, key in enumerate(columns):
                value = source.get(key)
                text = "missing" if pd.isna(value) else f"{float(value):g}"
                cell = QTableWidgetItem(text)
                mark_missing(cell, bool(pd.isna(value)))
                table.setItem(table_row, column, cell)
        table.selectRow(selected - start)
        table.resizeColumnsToContents()
        layout.addWidget(table)
        close = _button("Close")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)
        dialog.exec()
