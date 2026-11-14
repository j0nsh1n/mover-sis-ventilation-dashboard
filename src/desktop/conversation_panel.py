"""Display-only research turns, thread composer, and history tracking."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil
from typing import TYPE_CHECKING, Literal

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent, QTextCursor, QTextOption
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QScrollBar,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.desktop.answer_markdown import MarkdownAnswer, plain_text
from src.desktop.research_controls import LocalModelButton, ScopeSwitch, TraceButton
from src.desktop.shell import DEMO_STAMP, RESEARCH_STAMP
from src.llm.history import PriorTurn

if TYPE_CHECKING:
    from src.desktop.research_workspace import QuestionState

TurnState = Literal["running", "answered", "no_model", "cancelled", "failed"]
NO_MODEL_ANSWER = "No AI answer. Search results are on the board."
EMPTY_TEXT = "Your answers will appear here. Each question uses its own source extracts."
EMPTY_PLACEHOLDER = "Ask a question\u2026"
FOLLOW_UP_PLACEHOLDER = "Ask a follow-up\u2026"
_NOTE_PREFIX = "**Verification note:**"
_MIN_BUBBLE_WIDTH = 60
_MAX_BUBBLE_SHARE = 0.80


def split_verification_notes(answer: str) -> tuple[str, tuple[str, ...]]:
    """Split ``**Verification note:**`` paragraphs out of an answer body.

    Leaves the research-only footer and mid-line prose mentioning
    "Verification note" intact inside the returned body. Each extracted note is
    rendered to plain text for display under Checks.
    """
    text = answer or ""
    kept: list[str] = []
    notes: list[str] = []
    current_note: list[str] | None = None

    def flush_note() -> None:
        nonlocal current_note
        if current_note is None:
            return
        rendered = plain_text("\n".join(current_note))
        prefix = "Verification note:"
        if rendered.startswith(prefix):
            rendered = rendered[len(prefix):].strip()
        if rendered:
            notes.append(rendered)
        current_note = None

    for line in text.splitlines():
        if line.lstrip().startswith(_NOTE_PREFIX):
            flush_note()
            current_note = [line.lstrip()]
            continue
        if current_note is not None:
            if line.strip() == "":
                flush_note()
                kept.append(line)
            elif line.lstrip().startswith("---"):
                flush_note()
                kept.append(line)
            else:
                current_note.append(line)
            continue
        kept.append(line)
    flush_note()
    body = "\n".join(kept).strip()
    while "\n\n\n" in body:
        body = body.replace("\n\n\n", "\n\n")
    return body, tuple(notes)


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
    # Whether the question was asked on the fictional demo data. The turn keeps this after the folders change,
    # so an answer never takes the name of data it was not read from.
    demo: bool = False
    # Recorded when the answer arrives. Switching the AI menu later does not rewrite them.
    backend: str = ""
    trimmed: bool = False
    sources: tuple[str, ...] = ()
    # Frozen copy of what a follow-up is shown. Editing the turn afterwards leaves it.
    sent: PriorTurn | None = None
    # How many source extracts or abstracts this question's answer was read from; 0 until known
    extracts: int = 0
    # Named steps of a running question: the finished ones, the one in progress, and a 0-1 bar position
    steps: list[str] = field(default_factory=list)
    current_step: str = ""
    current_phase: str = ""
    progress: float = 0.0
    # Optional badge inside the user bubble (e.g., "Minute -40 · caseA").
    context_label: str = ""

    @property
    def badge(self) -> str:
        return self.model or "No AI answer"

    @property
    def data_stamp(self) -> str:
        return DEMO_STAMP if self.demo else RESEARCH_STAMP

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
        return [step.rstrip("\u2026").rstrip(".") for step in self.steps]

    def remember(self) -> None:
        """Copy this answer into the history a follow-up reads. Once only."""
        if self.sent is not None or self.state != "answered":
            return
        self.sent = PriorTurn(
            question=self.question,
            answer=self.answer,
            sources=self.sources,
            pids=self.cited_pids,
            backend=self.backend,
            trimmed=self.trimmed,
            scope=self.scope,
            state=self.state,
            demo=self.demo,
        )


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


class BubbleText(QTextEdit):
    """Plain-text selectable user prompt inside a compact right-aligned bubble."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("turnBubbleText")
        self.setReadOnly(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setTabChangesFocus(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.document().setDocumentMargin(2)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.document().documentLayout().documentSizeChanged.connect(self._no_inner_scroll)
        if text:
            self.setPlainText(text)

    def text(self) -> str:
        return self.toPlainText()

    def _no_inner_scroll(self) -> None:
        self.verticalScrollBar().setRange(0, 0)
        self.horizontalScrollBar().setRange(0, 0)

    def wheelEvent(self, event) -> None:  # noqa: N802
        event.ignore()


class ThreadComposer(QPlainTextEdit):
    """Multi-line prompt box: Enter submits, Shift+Enter inserts a newline, Tab moves focus."""

    submitted = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("threadComposer")
        self.setAccessibleName("Ask a question")
        self.setTabChangesFocus(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setPlaceholderText(EMPTY_PLACEHOLDER)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._fitting = False
        self.textChanged.connect(self._fit_height)
        self.document().documentLayout().documentSizeChanged.connect(lambda *_args: self._fit_height())
        self._fit_height()

    def text(self) -> str:
        return self.toPlainText()

    def setText(self, text: str) -> None:
        self.setPlainText(text)
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.setTextCursor(cursor)

    def _wrapped_lines(self) -> int:
        """Visual lines, including soft wraps. A paragraph is one block and may be several lines."""
        total = 0
        block = self.document().firstBlock()
        while block.isValid():
            layout = block.layout()
            count = 0 if layout is None else layout.lineCount()
            total += count if count > 0 else 1
            block = block.next()
        return max(total, 1)

    def _fit_height(self) -> None:
        if self._fitting:
            return
        self._fitting = True
        try:
            self.ensurePolished()
            line_h = max(18, self.fontMetrics().lineSpacing())
            lines = max(2, min(4, self._wrapped_lines()))
            margins = self.contentsMargins()
            target = lines * line_h + margins.top() + margins.bottom() + 18
            clamped = max(56, min(116, target))
            if self.height() != clamped:
                self.setFixedHeight(clamped)
        finally:
            self._fitting = False

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit_height()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._fit_height()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        mods = event.modifiers()
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        alt = bool(mods & Qt.KeyboardModifier.AltModifier)
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not alt:
            event.accept()
            if shift and not ctrl:
                self.insertPlainText("\n")
            else:
                self.submitted.emit()
            return
        super().keyPressEvent(event)


class TurnWidget(QFrame):
    case_requested = Signal(int, str)
    show_requested = Signal(int)
    ask_requested = Signal(str)
    cancel_requested = Signal()
    tab_order_changed = Signal()

    def __init__(self, turn: Turn, number: int = 1, parent=None):
        super().__init__(parent)
        self.setObjectName("conversationTurn")
        self._number = number
        self._request_id = turn.request_id
        self._sources_open = False
        self._checks_open = False
        self._check_notes: tuple[str, ...] = ()
        self._question_source = ""
        self._context_source = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.rule = QFrame()
        self.rule.setObjectName("turnRule")
        self.rule.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(self.rule)

        # 1. Compact right-aligned question bubble
        bubble_row = QHBoxLayout()
        bubble_row.setContentsMargins(0, 0, 0, 0)
        bubble_row.setSpacing(0)
        bubble_row.addStretch(1)
        self.bubble = QFrame()
        self.bubble.setObjectName("turnBubble")
        self.bubble.setFrameShape(QFrame.Shape.NoFrame)
        self.bubble.setAccessibleName(f"Question {number}")
        self._bubble_layout = QVBoxLayout(self.bubble)
        self._bubble_layout.setContentsMargins(12, 8, 12, 8)
        self._bubble_layout.setSpacing(4)
        self.context_label = QLabel()
        self.context_label.setObjectName("turnContext")
        self.context_label.setTextFormat(Qt.TextFormat.PlainText)
        self.context_label.setWordWrap(True)
        self.context_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.context_label.hide()
        self._bubble_layout.addWidget(self.context_label)
        self.question_label = BubbleText(turn.question)
        self.question_label.setAccessibleName(f"Question {number}: {turn.question}")
        self._question_source = turn.question
        self._bubble_layout.addWidget(self.question_label)
        bubble_row.addWidget(self.bubble)
        layout.addLayout(bubble_row)

        # 2. Quiet meta line: service, data stamp, and progress while running
        meta = QHBoxLayout()
        meta.setContentsMargins(0, 0, 0, 0)
        meta.setSpacing(8)
        self.badge_label = QLabel()
        self.badge_label.setObjectName("quietText")
        self.badge_label.setTextFormat(Qt.TextFormat.PlainText)
        self.badge_label.setWordWrap(True)
        meta.addWidget(self.badge_label, 1)
        # Set once: the data a question was asked on does not change after it is asked
        self.data_label = QLabel(turn.data_stamp)
        self.data_label.setObjectName("quietText")
        self.data_label.setTextFormat(Qt.TextFormat.PlainText)
        self.data_label.setWordWrap(True)
        meta.addWidget(self.data_label)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("linkBtn")
        self.cancel_button.setAutoDefault(True)
        self.cancel_button.setAccessibleName(f"Cancel question {number}")
        self.cancel_button.clicked.connect(self.cancel_requested)
        meta.addWidget(self.cancel_button)
        layout.addLayout(meta)

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

        # 3. Full-width answer body (verification notes stripped)
        self.answer_text = AnswerText()
        self.answer_text.setObjectName("turnAnswer")
        self._answer_source = ""
        self.answer_text.setAccessibleName(f"Answer to {turn.question}")
        layout.addWidget(self.answer_text)

        # 4. Folded Sources (n)
        self.sources_toggle = QPushButton("Sources (0)")
        self.sources_toggle.setObjectName("foldToggle")
        self.sources_toggle.setAutoDefault(True)
        self.sources_toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.sources_toggle.clicked.connect(self._toggle_sources)
        self.sources_toggle.hide()
        layout.addWidget(self.sources_toggle, alignment=Qt.AlignmentFlag.AlignLeft)

        self.sources_container = QWidget()
        self.citations = QVBoxLayout(self.sources_container)
        self.citations.setContentsMargins(0, 0, 0, 0)
        self.citations.setSpacing(2)
        self.sources_container.hide()
        layout.addWidget(self.sources_container)
        self.citation_buttons: list[QPushButton] = []
        self._cited_pids: tuple[str, ...] = ()

        # 5. Folded Checks: N notes
        self.checks_toggle = QPushButton("Checks: 0 notes")
        self.checks_toggle.setObjectName("foldToggle")
        self.checks_toggle.setAutoDefault(True)
        self.checks_toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.checks_toggle.clicked.connect(self._toggle_checks)
        self.checks_toggle.hide()
        layout.addWidget(self.checks_toggle, alignment=Qt.AlignmentFlag.AlignLeft)

        self.checks_container = QWidget()
        self.checks_layout = QVBoxLayout(self.checks_container)
        self.checks_layout.setContentsMargins(0, 0, 0, 0)
        self.checks_layout.setSpacing(4)
        self.checks_container.hide()
        layout.addWidget(self.checks_container)
        self.check_note_labels: list[QLabel] = []

        # 6. Per-turn actions
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(10)
        self._current_turn = turn
        self._on_board = False
        self.show_button = QPushButton("Show on board")
        self.show_button.setObjectName("linkBtn")
        self.show_button.setAutoDefault(True)
        self.show_button.setAccessibleName(f"Show on board for question {number}")
        self.show_button.clicked.connect(lambda: self.show_requested.emit(self._request_id))
        actions.addWidget(self.show_button)
        self.copy_button = QPushButton("Copy with sources")
        self.copy_button.setObjectName("linkBtn")
        self.copy_button.setAutoDefault(True)
        self.copy_button.setAccessibleName(f"Copy with sources for question {number}")
        self.copy_button.clicked.connect(lambda: self._copy(self._current_turn))
        actions.addWidget(self.copy_button)
        self.ask_button = QPushButton("Ask again")
        self.ask_button.setObjectName("linkBtn")
        self.ask_button.setAutoDefault(True)
        self.ask_button.setAccessibleName(f"Ask again for question {number}")
        self.ask_button.clicked.connect(lambda: self.ask_requested.emit(self._current_turn.question))
        actions.addWidget(self.ask_button)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.update_turn(turn)

    def tab_widgets(self) -> list[QWidget]:
        widgets: list[QWidget] = [
            self.question_label,
            self.cancel_button,
            self.answer_text,
            self.sources_toggle,
            *self.citation_buttons,
            self.checks_toggle,
            *self.check_note_labels,
            self.show_button,
            self.copy_button,
            self.ask_button,
        ]
        return [
            widget for widget in widgets
            if not widget.isHidden() and widget.focusPolicy() != Qt.FocusPolicy.NoFocus
        ]

    def hide_rule(self) -> None:
        """The first turn sits under the panel's own header, which has its own line."""
        self.rule.hide()

    def _fit_bubble(self) -> None:
        avail = self.width()
        if avail <= 0:
            avail = 400
        self.bubble.ensurePolished()
        self.question_label.ensurePolished()
        self.context_label.ensurePolished()
        max_bubble = max(_MIN_BUBBLE_WIDTH, int(avail * _MAX_BUBBLE_SHARE))
        margins = self._bubble_layout.contentsMargins()
        frame_chrome = margins.left() + margins.right() + 2 * self.bubble.frameWidth()
        inner_pad = 2 * self.question_label.frameWidth() + 4
        ctx_w = 0
        if self._context_source:
            ctx_w = self.context_label.fontMetrics().horizontalAdvance(self._context_source) + 2
        doc = self.question_label.document()
        doc.setTextWidth(-1)
        ideal_w = ceil(doc.idealWidth()) + inner_pad
        desired_w = max(ideal_w, ctx_w) + frame_chrome
        bubble_w = max(_MIN_BUBBLE_WIDTH, min(max_bubble, desired_w))
        self.bubble.setFixedWidth(bubble_w)
        inner_w = max(20, bubble_w - frame_chrome)
        self.question_label.setFixedWidth(inner_w)
        doc.setTextWidth(max(1, inner_w - 2 * self.question_label.frameWidth()))
        text_h = ceil(doc.size().height()) + 2 * self.question_label.frameWidth() + 2
        self.question_label.setFixedHeight(max(20, text_h))
        self.question_label._no_inner_scroll()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit_bubble()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._fit_bubble()

    def _sync_sources_ui(self) -> None:
        count = len(self._cited_pids)
        if count == 0:
            self.sources_toggle.hide()
            self.sources_toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.sources_container.hide()
            return
        state_word = "expanded" if self._sources_open else "collapsed"
        label = f"Sources ({count})"
        self.sources_toggle.setText(label)
        self.sources_toggle.setAccessibleName(f"{label} for question {self._number}")
        self.sources_toggle.setAccessibleDescription(f"Sources, {count}, {state_word}")
        self.sources_toggle.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.sources_toggle.show()
        self.sources_container.setVisible(self._sources_open)
        policy = Qt.FocusPolicy.StrongFocus if self._sources_open else Qt.FocusPolicy.NoFocus
        for button in self.citation_buttons:
            button.setVisible(self._sources_open)
            button.setFocusPolicy(policy)

    def _sync_checks_ui(self) -> None:
        count = len(self._check_notes)
        amber = "true" if count > 0 else "false"
        if self.checks_toggle.property("amber") != amber:
            self.checks_toggle.setProperty("amber", amber)
            self.checks_toggle.style().unpolish(self.checks_toggle)
            self.checks_toggle.style().polish(self.checks_toggle)
        if count == 0:
            self.checks_toggle.hide()
            self.checks_toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.checks_container.hide()
            return
        state_word = "expanded" if self._checks_open else "collapsed"
        noun = "note" if count == 1 else "notes"
        label = f"Checks: {count} {noun}"
        self.checks_toggle.setText(label)
        self.checks_toggle.setAccessibleName(f"{label} for question {self._number}")
        self.checks_toggle.setAccessibleDescription(f"Verification checks, {count} {noun}, {state_word}")
        self.checks_toggle.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.checks_toggle.show()
        self.checks_container.setVisible(self._checks_open)
        for note_label in self.check_note_labels:
            note_label.setVisible(self._checks_open)

    def _toggle_sources(self) -> None:
        self._sources_open = not self._sources_open
        self._sync_sources_ui()
        self.tab_order_changed.emit()

    def _toggle_checks(self) -> None:
        self._checks_open = not self._checks_open
        self._sync_checks_ui()
        self.tab_order_changed.emit()

    @staticmethod
    def _procedures(turn: Turn) -> dict[str, str]:
        candidates = turn.board.result.candidates if turn.board is not None and turn.board.result is not None else ()
        return {candidate.pid: candidate.procedure for candidate in candidates}

    def _source_lines(self, turn: Turn) -> list[str]:
        procedures = self._procedures(turn)
        return [
            f"{number}  {pid}" + (f" \u00b7 {procedures[pid]}" if procedures.get(pid) else "")
            for number, pid in enumerate(turn.cited_pids, 1)
        ]

    def copy_text(self, turn: Turn) -> str:
        """The question, the full answer (including notes and footer), and its numbered sources."""
        parts = [turn.question, plain_text(turn.answer)]
        sources = self._source_lines(turn)
        if sources:
            parts.append("Sources:\n" + "\n".join(sources))
        parts.append(turn.data_stamp)
        return "\n\n".join(part for part in parts if part)

    def _copy(self, turn: Turn) -> None:
        QApplication.clipboard().setText(self.copy_text(turn))

    def update_turn(self, turn: Turn) -> None:
        self._current_turn = turn
        self._request_id = turn.request_id
        if turn.context_label != self._context_source:
            self._context_source = turn.context_label
            self.context_label.setText(turn.context_label)
            self.context_label.setVisible(bool(turn.context_label))
        if turn.question != self._question_source:
            self._question_source = turn.question
            self.question_label.setPlainText(turn.question)
            self.question_label.setAccessibleName(f"Question {self._number}: {turn.question}")
            self.answer_text.setAccessibleName(f"Answer to {turn.question}")
        self._fit_bubble()

        extracts = f" \u00b7 {turn.extracts} {'extract' if turn.extracts == 1 else 'extracts'}" if turn.extracts else ""
        self.badge_label.setText(f"Question {self._number} \u00b7 {turn.badge}{extracts}")
        running = turn.state == "running"
        self.cancel_button.setVisible(running)
        self.progress_bar.setVisible(running)
        self.progress_bar.setValue(round(turn.progress * 100))
        lines = [f"\u2713 {step}" for step in turn.finished_steps]
        if turn.current_step:
            lines.append(turn.current_step)
        self.steps_label.setText("\n".join(lines))
        self.steps_label.setVisible(running and bool(lines))
        self.state_label.setText({
            "running": "Searching and reading sources\u2026",
            "answered": "Answered",
            "no_model": "Search complete",
            "cancelled": "Cancelled",
            "failed": f"Failed: {turn.failure}",
        }[turn.state])
        self.state_label.setVisible(not (running and bool(lines)))

        if self._answer_source != turn.answer:
            body, notes = split_verification_notes(turn.answer)
            self.answer_text.clear()
            if body:
                self.answer_text.append_markdown(body)
            self._answer_source = turn.answer
            self.answer_text.setVisible(bool(body))
            if notes != self._check_notes:
                self.check_note_labels.clear()
                while self.checks_layout.count():
                    item = self.checks_layout.takeAt(0)
                    child = item.widget() if item is not None else None
                    if child is not None:
                        child.deleteLater()
                for note in notes:
                    note_label = QLabel(note)
                    note_label.setObjectName("turnCheckNote")
                    note_label.setTextFormat(Qt.TextFormat.PlainText)
                    note_label.setWordWrap(True)
                    note_label.setTextInteractionFlags(
                        Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
                    )
                    note_label.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
                    self.checks_layout.addWidget(note_label)
                    self.check_note_labels.append(note_label)
                self._check_notes = notes
            self._sync_checks_ui()
        else:
            self.answer_text.setVisible(bool(self._answer_source and not self.answer_text.document().isEmpty()))

        self.note_label.setText(turn.note)
        self.note_label.setVisible(bool(turn.note))
        self._sync_show_button()
        self.copy_button.setEnabled(bool(turn.answer) and not running)
        self.ask_button.setEnabled(not running)

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
                row.setContentsMargins(0, 0, 0, 0)
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
            self._sync_sources_ui()
        self.tab_order_changed.emit()

    def set_on_board(self, on_board: bool) -> None:
        self._on_board = on_board
        self._sync_show_button()

    def _sync_show_button(self) -> None:
        turn = self._current_turn
        if self._on_board:
            self.show_button.setText("On board")
            self.show_button.setEnabled(False)
            self.show_button.setAccessibleName(f"On board for question {self._number}")
            return
        self.show_button.setText("Show on board")
        self.show_button.setEnabled(turn.board is not None and turn.state != "running")
        self.show_button.setAccessibleName(f"Show on board for question {self._number}")


class ConversationPanel(QFrame):
    case_requested = Signal(int, str)
    show_requested = Signal(int)
    ask_requested = Signal(str)
    ask_scoped_requested = Signal(str, str, int)
    cancel_requested = Signal()
    reset_requested = Signal()
    tab_order_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("conversationPanel")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMinimumSize(360, 180)
        self.turns: list[Turn] = []
        self.turn_widgets: dict[int, TurnWidget] = {}
        self._shown_request: int | None = None
        self._turns: dict[int, Turn] = {}
        self._next_request_id = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Panel header: title + New thread
        header_host = QWidget()
        head = QHBoxLayout(header_host)
        self._header_layout = head
        head.setContentsMargins(20, 16, 20, 10)
        head.setSpacing(8)
        self.title = QLabel("Conversation")
        self.title.setObjectName("conversationTitle")
        head.addWidget(self.title, 1)
        self.reset_button = QPushButton("New thread")
        self.reset_button.setObjectName("linkBtn")
        self.reset_button.setAutoDefault(True)
        self.reset_button.setAccessibleName("New thread")
        self.reset_button.setAccessibleDescription("Clear the conversation, history, and board")
        self.reset_button.clicked.connect(self.reset_requested)
        head.addWidget(self.reset_button)
        root.addWidget(header_host)

        # Scrolling thread body
        self._scroll_area = QScrollArea()
        self._scroll_area.setObjectName("conversationScroll")
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page = QWidget()
        page.setObjectName("conversationPage")
        self._layout = QVBoxLayout(page)
        self._layout.setContentsMargins(20, 4, 20, 16)
        self._layout.setSpacing(16)
        self.empty_label = QLabel(EMPTY_TEXT)
        self.empty_label.setObjectName("quietText")
        self.empty_label.setWordWrap(True)
        self._layout.addWidget(self.empty_label)
        self._layout.addStretch(1)
        self._scroll_area.setWidget(page)
        self._scroll_area.viewport().installEventFilter(self)
        root.addWidget(self._scroll_area, 1)

        # Pinned bottom composer bar
        self.composer_bar = QFrame()
        self.composer_bar.setObjectName("composerBar")
        self.composer_bar.setFrameShape(QFrame.Shape.NoFrame)
        bar_layout = QVBoxLayout(self.composer_bar)
        bar_layout.setContentsMargins(16, 10, 16, 12)
        bar_layout.setSpacing(8)

        self.composer = ThreadComposer()
        bar_layout.addWidget(self.composer)

        self.scope_switch = ScopeSwitch()
        self.model_menu = LocalModelButton()
        self.ask_button = TraceButton()

        self._choices_row = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._choices_row.setContentsMargins(0, 0, 0, 0)
        self._choices_row.setSpacing(8)
        self._choices_row.addWidget(self.scope_switch)
        self._choices_row.addWidget(self.model_menu)
        self._choices_row.addStretch(1)

        self._composer_controls = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._composer_controls.setContentsMargins(0, 0, 0, 0)
        self._composer_controls.setSpacing(8)
        self._composer_controls.addLayout(self._choices_row, 1)
        self._composer_controls.addWidget(self.ask_button)
        bar_layout.addLayout(self._composer_controls)
        root.addWidget(self.composer_bar)
        self.composer_bar.installEventFilter(self)

        self._refresh_header()
        self.fit_composer()

    # Callers still use the scroll-area names. The area itself is not named scroll:
    # QWidget.scroll is a method, and that name confuses the type checker.
    def viewport(self) -> QWidget:
        return self._scroll_area.viewport()

    def verticalScrollBar(self) -> QScrollBar:  # noqa: N802
        return self._scroll_area.verticalScrollBar()

    def horizontalScrollBar(self) -> QScrollBar:  # noqa: N802
        return self._scroll_area.horizontalScrollBar()

    def ensureWidgetVisible(self, widget: QWidget, xmargin: int = 50, ymargin: int = 50) -> None:  # noqa: N802
        self._scroll_area.ensureWidgetVisible(widget, xmargin, ymargin)

    def setWidgetResizable(self, resizable: bool) -> None:  # noqa: N802
        self._scroll_area.setWidgetResizable(resizable)

    def set_stacked(self, stacked: bool) -> None:
        val = "true" if stacked else "false"
        if self.property("stacked") != val:
            self.setProperty("stacked", val)
            self.style().unpolish(self)
            self.style().polish(self)
        self.setMinimumWidth(0 if stacked else 360)
        # A stacked page is short. Tighter header and composer margins leave the thread more of the panel.
        bar = self.composer_bar.layout()
        if stacked:
            self._header_layout.setContentsMargins(16, 8, 16, 6)
            if bar is not None:
                bar.setContentsMargins(12, 6, 12, 8)
        else:
            self._header_layout.setContentsMargins(20, 16, 20, 10)
            if bar is not None:
                bar.setContentsMargins(16, 10, 16, 12)

    def fit_composer(self) -> None:
        margins = self.composer_bar.layout().contentsMargins()
        room = self.composer_bar.width() - margins.left() - margins.right()
        if room <= 0:
            room = max(320, self.width() - margins.left() - margins.right())
        for control in (self.scope_switch, self.model_menu, self.ask_button):
            control.ensurePolished()
        choices_w = self.scope_switch.sizeHint().width() + self._choices_row.spacing() + self.model_menu.sizeHint().width()
        ask_w = self.ask_button.sizeHint().width()
        one_row = choices_w + self._composer_controls.spacing() + ask_w <= room
        if one_row:
            self._choices_row.setDirection(QBoxLayout.Direction.LeftToRight)
            self._composer_controls.setDirection(QBoxLayout.Direction.LeftToRight)
            self._composer_controls.setAlignment(self.ask_button, Qt.AlignmentFlag(0))
        elif choices_w <= room:
            self._choices_row.setDirection(QBoxLayout.Direction.LeftToRight)
            self._composer_controls.setDirection(QBoxLayout.Direction.TopToBottom)
            self._composer_controls.setAlignment(self.ask_button, Qt.AlignmentFlag.AlignRight)
        else:
            self._choices_row.setDirection(QBoxLayout.Direction.TopToBottom)
            self._composer_controls.setDirection(QBoxLayout.Direction.TopToBottom)
            self._composer_controls.setAlignment(self.ask_button, Qt.AlignmentFlag.AlignRight)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Resize:
            if watched is self.composer_bar:
                self.fit_composer()
            elif watched is self._scroll_area.viewport():
                for widget in self.turn_widgets.values():
                    widget._fit_bubble()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.fit_composer()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.fit_composer()

    def set_shown_turn(self, request_id: int | None) -> None:
        """The turn whose results are on the board. Its button reads On board and does nothing."""
        self._shown_request = request_id
        for rid, widget in self.turn_widgets.items():
            widget.set_on_board(rid == request_id)

    def focus_turn(self, request_id: int) -> None:
        """Scroll the thread so this turn is in view and focus its answer."""
        widget = self.turn_widgets.get(request_id)
        if widget is None:
            return
        self._scroll_to_turn(request_id)
        widget.answer_text.setFocus()

    def allocate_request_id(self) -> int:
        self._next_request_id += 1
        return self._next_request_id

    def _count_line(self) -> str:
        count = len(self.turns)
        return f"{count} {'question' if count == 1 else 'questions'}, each answered from its own extracts"

    def _refresh_header(self) -> None:
        self.empty_label.setText(self._count_line() if self.turns else EMPTY_TEXT)
        self.empty_label.setVisible(True)
        if self.turns:
            self.composer.setPlaceholderText(FOLLOW_UP_PLACEHOLDER)
            self.composer.setAccessibleName("Ask a follow-up")
        else:
            self.composer.setPlaceholderText(EMPTY_PLACEHOLDER)
            self.composer.setAccessibleName("Ask a question")

    def tab_widgets(self) -> list[QWidget]:
        widgets: list[QWidget] = [self.reset_button]
        for turn in self.turns:
            widget = self.turn_widgets.get(turn.request_id)
            if widget is not None:
                widgets.extend(widget.tab_widgets())
        widgets.extend([
            self.composer,
            self.scope_switch.option(self.scope_switch.scope()),
            self.model_menu,
            self.ask_button,
        ])
        return widgets

    def append_turn(self, turn: Turn) -> None:
        if turn.request_id > self._next_request_id:
            self._next_request_id = turn.request_id
        self.turns.append(turn)
        self._turns[turn.request_id] = turn
        widget = TurnWidget(turn, len(self.turns))
        widget.set_on_board(turn.request_id == self._shown_request)
        if len(self.turns) == 1:
            widget.hide_rule()
        self.turn_widgets[turn.request_id] = widget
        widget.case_requested.connect(self.case_requested)
        widget.show_requested.connect(self.show_requested)
        widget.ask_requested.connect(self.ask_requested)
        widget.ask_requested.connect(
            lambda question, rid=turn.request_id, scope=turn.scope: self.ask_scoped_requested.emit(question, scope, rid)
        )
        widget.cancel_requested.connect(self.cancel_requested)
        widget.tab_order_changed.connect(self.tab_order_changed)
        self._layout.insertWidget(self._layout.count() - 1, widget)
        self._refresh_header()
        self.tab_order_changed.emit()
        QTimer.singleShot(0, lambda: self._scroll_to_turn(turn.request_id))

    def _scroll_to_turn(self, request_id: int) -> None:
        widget = self.turn_widgets.get(request_id)
        if widget is not None:
            self._scroll_area.ensureWidgetVisible(widget)

    def turn(self, request_id: int | None) -> Turn | None:
        return self._turns.get(request_id) if request_id is not None else None

    def history_before(self, request_id: int | None = None) -> tuple[PriorTurn, ...]:
        """Answered turns before ``request_id``, oldest first.

        The turn itself is not included, so Ask again on turn k sends the turns
        before k. Turns that were never answered have no frozen copy and are skipped.
        """
        found: list[PriorTurn] = []
        for turn in self.turns:
            if request_id is not None and turn.request_id == request_id:
                break
            if turn.sent is not None:
                found.append(turn.sent)
        return tuple(found)

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
        self.tab_order_changed.emit()
