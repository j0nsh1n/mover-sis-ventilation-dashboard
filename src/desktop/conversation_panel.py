"""Display-only research turns, kept separate from each question's model input."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from math import ceil

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from src.desktop.answer_markdown import MarkdownAnswer

if TYPE_CHECKING:
    from src.desktop.research_workspace import QuestionState

TurnState = Literal["running", "answered", "no_model", "cancelled", "failed"]
NO_MODEL_ANSWER = "No AI answer. Search results are on the board."
EMPTY_TEXT = "Your answers will appear here. Each question uses its own source extracts."


@dataclass
class Turn:
    request_id: int
    question: str
    model: str | None
    state: TurnState = "running"
    answer: str = ""
    cited_pids: tuple[str, ...] = ()
    failure: str = ""
    note: str = ""
    board: QuestionState | None = None
    scope: str = "Cases"
    # How many source extracts or abstracts this question's answer was read from; 0 until known
    extracts: int = 0
    # Named steps of a running question: the finished ones, the one in progress, and a 0-1 bar position
    steps: list[str] = field(default_factory=list)
    current_step: str = ""
    current_phase: str = ""
    progress: float = 0.0

    @property
    def badge(self) -> str:
        return self.model or "No AI answer"

    def advance(self, message: str, phase: str = "", fraction: float | None = None) -> None:
        """Record a progress message. A new phase finishes the step before it; the bar never moves back."""
        if self.current_step and (phase or message) != (self.current_phase or self.current_step) and message != self.current_step:
            if not self.steps or self.steps[-1] != self.current_step:
                self.steps.append(self.current_step)
        self.current_step = message
        self.current_phase = phase
        if fraction is not None:
            self.progress = max(self.progress, min(1.0, fraction))

    @property
    def finished_steps(self) -> list[str]:
        return [step.rstrip("…").rstrip(".") for step in self.steps]


class AnswerText(MarkdownAnswer):
    """Read-only answer that grows to its text, so only the conversation panel scrolls."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTabChangesFocus(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.document().contentsChanged.connect(self._fit)
        self.document().documentLayout().documentSizeChanged.connect(self._no_inner_scroll)

    def _no_inner_scroll(self) -> None:
        # The widget is always tall enough, so a stale range would only let the wheel scroll inside it
        self.verticalScrollBar().setRange(0, 0)

    def wheelEvent(self, event) -> None:  # noqa: N802
        event.ignore()

    def _fit(self) -> None:
        width = self.viewport().width()
        if width <= 0:
            return
        self.document().setTextWidth(width)
        height = ceil(self.document().size().height()) + 2 * self.frameWidth()
        if height != self.height():
            self.setFixedHeight(height)
        self._no_inner_scroll()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit()
        self._no_inner_scroll()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._fit()


class TurnWidget(QFrame):
    case_requested = Signal(int, str)
    show_requested = Signal(int)
    ask_requested = Signal(str)
    cancel_requested = Signal()

    def __init__(self, turn: Turn, number: int = 1, parent=None):
        super().__init__(parent)
        self.setObjectName("conversationTurn")
        self._number = number
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.rule = QFrame()
        self.rule.setObjectName("turnRule")
        self.rule.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(self.rule)
        meta = QHBoxLayout()
        self.badge_label = QLabel()
        self.badge_label.setObjectName("quietText")
        self.badge_label.setTextFormat(Qt.TextFormat.PlainText)
        self.badge_label.setWordWrap(True)
        meta.addWidget(self.badge_label, 1)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("linkBtn")
        self.cancel_button.setAutoDefault(True)
        self.cancel_button.setAccessibleName(f"Cancel question {number}")
        self.cancel_button.clicked.connect(self.cancel_requested)
        meta.addWidget(self.cancel_button)
        layout.addLayout(meta)
        self.question_label = QLabel(turn.question)
        self.question_label.setTextFormat(Qt.TextFormat.PlainText)
        self.question_label.setWordWrap(True)
        self.question_label.setObjectName("turnQuestion")
        layout.addWidget(self.question_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("turnProgress")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setAccessibleName(f"Progress of question {number}")
        layout.addWidget(self.progress_bar)
        self.steps_label = QLabel()
        self.steps_label.setObjectName("quietText")
        self.steps_label.setWordWrap(True)
        self.steps_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.steps_label)
        self.state_label = QLabel()
        self.state_label.setObjectName("quietText")
        self.state_label.setWordWrap(True)
        self.state_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.state_label)
        self.note_label = QLabel()
        self.note_label.setObjectName("quietText")
        self.note_label.setWordWrap(True)
        self.note_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.note_label)
        self.answer_text = AnswerText()
        self.answer_text.setObjectName("turnAnswer")
        self._answer_source = ""
        self.answer_text.setAccessibleName(f"Answer to {turn.question}")
        layout.addWidget(self.answer_text)
        self.citations = QVBoxLayout()
        self.citations.setSpacing(2)
        layout.addLayout(self.citations)
        self.citation_buttons: list[QPushButton] = []
        self._cited_pids: tuple[str, ...] = ()
        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.show_button = QPushButton("Show on board")
        self.show_button.setObjectName("linkBtn")
        self.show_button.setAutoDefault(True)
        self.show_button.clicked.connect(lambda: self.show_requested.emit(turn.request_id))
        actions.addWidget(self.show_button)
        self.copy_button = QPushButton("Copy with sources")
        self.copy_button.setObjectName("linkBtn")
        self.copy_button.setAutoDefault(True)
        self.copy_button.clicked.connect(lambda: self._copy(turn))
        actions.addWidget(self.copy_button)
        self.ask_button = QPushButton("Ask again")
        self.ask_button.setObjectName("linkBtn")
        self.ask_button.setAutoDefault(True)
        self.ask_button.clicked.connect(lambda: self.ask_requested.emit(turn.question))
        actions.addWidget(self.ask_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.update_turn(turn)

    def hide_rule(self) -> None:
        """The first turn sits under the panel's own header, which has its own line."""
        self.rule.hide()

    @staticmethod
    def _procedures(turn: Turn) -> dict[str, str]:
        candidates = turn.board.result.candidates if turn.board is not None and turn.board.result is not None else ()
        return {candidate.pid: candidate.procedure for candidate in candidates}

    def _source_lines(self, turn: Turn) -> list[str]:
        procedures = self._procedures(turn)
        return [
            f"{number}  {pid}" + (f" · {procedures[pid]}" if procedures.get(pid) else "")
            for number, pid in enumerate(turn.cited_pids, 1)
        ]

    def copy_text(self, turn: Turn) -> str:
        """The question, the answer and its numbered sources, as plain text with no markup."""
        parts = [turn.question, turn.answer.strip()]
        sources = self._source_lines(turn)
        if sources:
            parts.append("Sources:\n" + "\n".join(sources))
        return "\n\n".join(part for part in parts if part)

    def _copy(self, turn: Turn) -> None:
        QApplication.clipboard().setText(self.copy_text(turn))

    def update_turn(self, turn: Turn) -> None:
        extracts = f" · {turn.extracts} {'extract' if turn.extracts == 1 else 'extracts'}" if turn.extracts else ""
        self.badge_label.setText(f"Question {self._number} · {turn.badge}{extracts}")
        running = turn.state == "running"
        self.cancel_button.setVisible(running)
        self.progress_bar.setVisible(running)
        self.progress_bar.setValue(round(turn.progress * 100))
        lines = [f"✓ {step}" for step in turn.finished_steps]
        if turn.current_step:
            lines.append(turn.current_step)
        self.steps_label.setText("\n".join(lines))
        self.steps_label.setVisible(running and bool(lines))
        self.state_label.setText({
            "running": "Searching and reading sources…",
            "answered": "Answered",
            "no_model": "Search complete",
            "cancelled": "Cancelled",
            "failed": f"Failed: {turn.failure}",
        }[turn.state])
        self.state_label.setVisible(not (running and bool(lines)))
        if self._answer_source != turn.answer:
            self.answer_text.clear()
            self.answer_text.append_markdown(turn.answer)
            self._answer_source = turn.answer
        self.answer_text.setVisible(bool(turn.answer))
        self.note_label.setText(turn.note)
        self.note_label.setVisible(bool(turn.note))
        self.show_button.setEnabled(turn.board is not None and turn.state != "running")
        self.copy_button.setEnabled(bool(turn.answer) and not running)
        self.ask_button.setEnabled(turn.state != "running")
        if turn.cited_pids != self._cited_pids:
            self.citation_buttons.clear()
            while self.citations.count():
                item = self.citations.takeAt(0)
                row = item.layout() if item is not None else None
                while row is not None and row.count():
                    child_item = row.takeAt(0)
                    child = child_item.widget() if child_item is not None else None
                    if child is not None:
                        child.deleteLater()
            for number, pid in enumerate(turn.cited_pids, 1):
                row = QHBoxLayout()
                row.setSpacing(4)
                button = QPushButton(f"{number}  {pid}")
                button.setAutoDefault(True)
                button.setObjectName("citationLink")
                button.setAccessibleName(f"Open cited case {pid} in signal view")
                button.clicked.connect(lambda _checked=False, pid=pid: self.case_requested.emit(turn.request_id, pid))
                row.addWidget(button)
                procedure = self._procedures(turn).get(pid, "")
                if procedure:
                    detail = QLabel(procedure)
                    detail.setObjectName("quietText")
                    detail.setWordWrap(True)
                    detail.setTextFormat(Qt.TextFormat.PlainText)
                    row.addWidget(detail, 1)
                else:
                    row.addStretch(1)
                self.citations.addLayout(row)
                self.citation_buttons.append(button)
            self._cited_pids = turn.cited_pids


class ConversationPanel(QScrollArea):
    case_requested = Signal(int, str)
    show_requested = Signal(int)
    ask_requested = Signal(str)
    ask_scoped_requested = Signal(str, str)
    cancel_requested = Signal()
    reset_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("conversationPanel")
        self.setWidgetResizable(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumSize(290, 180)
        self.turns: list[Turn] = []
        self.turn_widgets: dict[int, TurnWidget] = {}
        self._turns: dict[int, Turn] = {}
        page = QWidget()
        page.setObjectName("conversationPage")
        self._layout = QVBoxLayout(page)
        self._layout.setContentsMargins(22, 24, 22, 24)
        self._layout.setSpacing(16)
        head = QHBoxLayout()
        self.title = QLabel("Conversation")
        self.title.setObjectName("conversationTitle")
        head.addWidget(self.title, 1)
        self.reset_button = QPushButton("Reset")
        self.reset_button.setObjectName("linkBtn")
        self.reset_button.setAutoDefault(True)
        self.reset_button.setAccessibleName("Reset")
        self.reset_button.setAccessibleDescription("Clear the conversation and the board")
        self.reset_button.clicked.connect(self.reset_requested)
        head.addWidget(self.reset_button)
        self._layout.addLayout(head)
        self.empty_label = QLabel(EMPTY_TEXT)
        self.empty_label.setObjectName("quietText")
        self.empty_label.setWordWrap(True)
        self._layout.addWidget(self.empty_label)
        self._layout.addStretch(1)
        self.setWidget(page)

    def _count_line(self) -> str:
        count = len(self.turns)
        return f"{count} {'question' if count == 1 else 'questions'}, each answered from its own extracts"

    def _refresh_header(self) -> None:
        self.empty_label.setText(self._count_line() if self.turns else EMPTY_TEXT)
        self.empty_label.setVisible(True)

    def append_turn(self, turn: Turn) -> None:
        self.turns.append(turn)
        self._turns[turn.request_id] = turn
        widget = TurnWidget(turn, len(self.turns))
        if len(self.turns) == 1:
            widget.hide_rule()
        self.turn_widgets[turn.request_id] = widget
        widget.case_requested.connect(self.case_requested)
        widget.show_requested.connect(self.show_requested)
        widget.ask_requested.connect(self.ask_requested)
        widget.ask_requested.connect(lambda question: self.ask_scoped_requested.emit(question, turn.scope))
        widget.cancel_requested.connect(self.cancel_requested)
        self._layout.insertWidget(self._layout.count() - 1, widget)
        self._refresh_header()
        QTimer.singleShot(0, lambda: self._scroll_to_turn(turn.request_id))

    def _scroll_to_turn(self, request_id: int) -> None:
        widget = self.turn_widgets.get(request_id)
        if widget is not None:
            self.ensureWidgetVisible(widget)

    def turn(self, request_id: int | None) -> Turn | None:
        return self._turns.get(request_id) if request_id is not None else None

    def update_turn(self, turn: Turn) -> None:
        self.turn_widgets[turn.request_id].update_turn(turn)

    def clear(self) -> None:
        for widget in self.turn_widgets.values():
            self._layout.removeWidget(widget)
            widget.hide()
            widget.deleteLater()
        self.turn_widgets.clear()
        self._turns.clear()
        self.turns.clear()
        self._refresh_header()
