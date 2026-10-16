"""Display-only research turns, kept separate from each question's model input."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from math import ceil

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from src.desktop.answer_markdown import MarkdownAnswer

if TYPE_CHECKING:
    from src.desktop.research_workspace import QuestionState

TurnState = Literal["running", "answered", "no_model", "cancelled", "failed"]
NO_MODEL_ANSWER = "No AI answer. Search results are on the board."


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

    @property
    def badge(self) -> str:
        return self.model or "No AI answer"


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

    def __init__(self, turn: Turn, parent=None):
        super().__init__(parent)
        self.setObjectName("conversationTurn")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        self.question_label = QLabel(turn.question)
        self.question_label.setTextFormat(Qt.TextFormat.PlainText)
        self.question_label.setWordWrap(True)
        self.question_label.setObjectName("turnQuestion")
        layout.addWidget(self.question_label)
        self.badge_label = QLabel(turn.badge)
        self.badge_label.setTextFormat(Qt.TextFormat.PlainText)
        self.badge_label.setWordWrap(True)
        layout.addWidget(self.badge_label)
        self.state_label = QLabel()
        self.state_label.setWordWrap(True)
        self.state_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.state_label)
        self.note_label = QLabel()
        self.note_label.setWordWrap(True)
        self.note_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.note_label)
        self.answer_text = AnswerText()
        self._answer_source = ""
        self.answer_text.setAccessibleName(f"Answer to {turn.question}")
        layout.addWidget(self.answer_text)
        self.citations = QVBoxLayout()
        layout.addLayout(self.citations)
        self.citation_buttons: list[QPushButton] = []
        self._cited_pids: tuple[str, ...] = ()
        actions = QHBoxLayout()
        self.show_button = QPushButton("Show on board")
        self.show_button.setAutoDefault(True)
        self.show_button.clicked.connect(lambda: self.show_requested.emit(turn.request_id))
        actions.addWidget(self.show_button)
        self.ask_button = QPushButton("Ask again")
        self.ask_button.setAutoDefault(True)
        self.ask_button.clicked.connect(lambda: self.ask_requested.emit(turn.question))
        actions.addWidget(self.ask_button)
        layout.addLayout(actions)
        self.update_turn(turn)

    def update_turn(self, turn: Turn) -> None:
        self.state_label.setText({
            "running": "Searching and reading sources…",
            "answered": "Answered",
            "no_model": "Search complete",
            "cancelled": "Cancelled",
            "failed": f"Failed: {turn.failure}",
        }[turn.state])
        if self._answer_source != turn.answer:
            self.answer_text.clear()
            self.answer_text.append_markdown(turn.answer)
            self._answer_source = turn.answer
        self.answer_text.setVisible(bool(turn.answer))
        self.note_label.setText(turn.note)
        self.note_label.setVisible(bool(turn.note))
        self.show_button.setEnabled(turn.board is not None and turn.state != "running")
        self.ask_button.setEnabled(turn.state != "running")
        if turn.cited_pids != self._cited_pids:
            for button in self.citation_buttons:
                self.citations.removeWidget(button)
                button.deleteLater()
            self.citation_buttons.clear()
            for pid in turn.cited_pids:
                button = QPushButton(pid)
                button.setAutoDefault(True)
                button.setObjectName("citationLink")
                button.setAccessibleName(f"Open cited case {pid} in signal view")
                button.clicked.connect(lambda _checked=False, pid=pid: self.case_requested.emit(turn.request_id, pid))
                self.citations.addWidget(button)
                self.citation_buttons.append(button)
            self._cited_pids = turn.cited_pids


class ConversationPanel(QScrollArea):
    case_requested = Signal(int, str)
    show_requested = Signal(int)
    ask_requested = Signal(str)
    ask_scoped_requested = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("conversationPanel")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMinimumSize(290, 180)
        self.turns: list[Turn] = []
        self.turn_widgets: dict[int, TurnWidget] = {}
        self._turns: dict[int, Turn] = {}
        page = QWidget()
        page.setObjectName("conversationPage")
        self._layout = QVBoxLayout(page)
        self._layout.setContentsMargins(12, 12, 12, 12)
        title = QLabel("Research conversation")
        title.setObjectName("turnQuestion")
        self._layout.addWidget(title)
        self.empty_label = QLabel("Your answers will appear here. Each question uses its own source extracts.")
        self.empty_label.setWordWrap(True)
        self._layout.addWidget(self.empty_label)
        self._layout.addStretch(1)
        self.setWidget(page)

    def append_turn(self, turn: Turn) -> None:
        self.turns.append(turn)
        self._turns[turn.request_id] = turn
        widget = TurnWidget(turn)
        self.turn_widgets[turn.request_id] = widget
        widget.case_requested.connect(self.case_requested)
        widget.show_requested.connect(self.show_requested)
        widget.ask_requested.connect(self.ask_requested)
        widget.ask_requested.connect(lambda question: self.ask_scoped_requested.emit(question, turn.scope))
        self._layout.insertWidget(self._layout.count() - 1, widget)
        self.empty_label.hide()
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
        self.empty_label.show()
