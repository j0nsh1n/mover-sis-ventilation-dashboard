"""Practice page for a fictional handoff: an invented timeline, five I-PASS boxes and feedback on the text.

The page reads no data folder and recommends no treatment. Every run is an authored case.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QFontMetricsF
from PySide6.QtWidgets import (
    QBoxLayout,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.desktop.accessibility import announce, name_combo
from src.desktop.scroll_safe_combo import ScrollSafeComboBox
from src.modules.handoff_trainer.cases import practice_cases
from src.modules.handoff_trainer.feedback import FeedbackView, build_feedback
from src.modules.handoff_trainer.grading import PracticeCase, grade
from src.modules.handoff_trainer.history import READ_FAILED, UNREADABLE, Attempts, load_attempts, record_attempt
from src.modules.handoff_trainer.history_view import HistoryDialog
from src.modules.handoff_trainer.sections import NOT_GRADED, SECTIONS, has_text, heading_of, join_sections
from src.modules.handoff_trainer.timeline_chart import TimelineChart, minutes_text
from src.modules.handoff_trainer.timeline_table import TimelineTable

# Page width from which "Your handoff" sits beside the timeline; below it the handoff goes under it.
SIDE_BY_SIDE_WIDTH = 940
SIDE_WIDTH = 440
# Width of the timeline column from which the picker sits beside the title.
HEADER_ROW_WIDTH = 640
PICKER_WIDTH = 260
# Page width under which the side gutters shrink and the rows of buttons stack, so the page still fits the
# window's least width (the practice pages sit beside the 64 px rail there).
COMPACT_WIDTH = 480
SIDE_MARGINS = (28, 24, 28, 28)
COMPACT_SIDE_MARGINS = (16, 20, 16, 24)

EMPTY_HANDOFF = "Write at least one section first."
SAVED = "Checked and saved."
NOT_SAVED = "Checked, but this attempt could not be saved on this computer."
TABLE_NOTE = "The same timeline minute by minute, for screen readers and close reading."
_BOX_LINES = (2, 8)
# Padding and border the stylesheet puts around a text box's lines.
_BOX_CHROME = 16
# Qt counts the lines a box shows from its viewport less one pixel, so a box exactly as tall as its lines
# would scroll by one line.
_BOX_SLACK = 1


def _text_button(text: str) -> QPushButton:
    """A quiet action: accent text, no box. The Research workspace's own helper cannot be imported here
    without pulling in the EMR stack, which this page must not touch."""
    button = QPushButton(text)
    button.setObjectName("textBtn")
    button.setAutoDefault(True)
    return button


def _link_button(text: str) -> QPushButton:
    """A small in-line link."""
    button = QPushButton(text)
    button.setObjectName("linkBtn")
    button.setAutoDefault(True)
    return button


class GrowingText(QPlainTextEdit):
    """A text box that is as tall as its text, between two bounds, and lets Tab leave it."""

    def __init__(self, min_lines: int = _BOX_LINES[0], max_lines: int = _BOX_LINES[1]) -> None:
        super().__init__()
        self._bounds = (min_lines, max_lines)
        # A handoff has no use for tab characters; Tab moves on to the next control.
        self.setTabChangesFocus(True)
        self.document().documentLayout().documentSizeChanged.connect(self._fit)
        self._fit()

    def _fit(self, *_size) -> None:
        low, high = self._bounds
        # A plain-text document's height is its number of lines, wrapped lines included.
        lines = min(max(math.ceil(self.document().size().height()), low), high)
        margin = round(self.document().documentMargin())
        # The document lays lines out at the font's line spacing rounded up, not to the nearest pixel.
        line = math.ceil(QFontMetricsF(self.document().defaultFont()).lineSpacing())
        self.setFixedHeight(lines * line + 2 * margin + _BOX_CHROME + _BOX_SLACK)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit()


class HandoffPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("handoff-practice")
        self._cases = practice_cases()
        self._case = self._cases[0]
        self._wide: bool | None = None
        self._roomy: bool | None = None
        self._compact: bool | None = None
        self.history_dialog: QWidget | None = None
        self._checked_sections: dict[str, str] | None = None

        self.case_line = QLabel()
        self.case_line.setObjectName("pathHint")
        self.case_line.setWordWrap(True)
        self.history_label = QLabel()
        self.history_label.setObjectName("pathHint")
        # A label reads out its text; the description says what it is.
        self.history_label.setAccessibleDescription("Practice history")
        self.history_button = _link_button("History")
        self.history_button.setAccessibleName("History of saved attempts")
        self.history_button.clicked.connect(self.show_history)
        self.history_note = QLabel()
        self.history_note.setObjectName("quietText")
        self.history_note.setWordWrap(True)
        self.history_note.hide()
        saved = QWidget()
        saved_row = QHBoxLayout(saved)
        saved_row.setContentsMargins(0, 0, 0, 0)
        saved_row.setSpacing(4)
        saved_row.addWidget(self.history_label)
        saved_row.addWidget(self.history_button)
        self.top_row = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.top_row.setSpacing(12)
        self.top_row.addWidget(self.case_line, 1)
        self.top_row.addWidget(saved, 0, Qt.AlignmentFlag.AlignRight)

        self.title_label = QLabel()
        self.title_label.setObjectName("caseTitle")
        self.title_label.setWordWrap(True)
        self.case_box: QComboBox = ScrollSafeComboBox()
        # The picker is as wide as its longest title only when there is room; the rest of the time its width
        # follows the page, so it never sets the page's least width.
        self.case_box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.case_box.setMinimumContentsLength(10)
        self.case_box.setMaximumWidth(PICKER_WIDTH)
        self.case_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        name_combo(self.case_box, "Fictional scenario")
        for case in self._cases:
            self.case_box.addItem(case.title, case.case_id)
        self.title_row = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.title_row.setSpacing(24)
        self.title_row.addWidget(self.title_label, 1)
        self.title_row.addWidget(self.case_box, 0, Qt.AlignmentFlag.AlignBottom)

        self.timeline_chart = TimelineChart()
        self.timeline_table = TimelineTable()
        self.view_stack = QStackedWidget()
        self.view_stack.addWidget(self.timeline_chart)
        self.view_stack.addWidget(self.timeline_table)
        self.table_toggle = _link_button("Show as table")
        self.table_toggle.clicked.connect(self._toggle_view)
        self.table_note = QLabel(TABLE_NOTE)
        self.table_note.setObjectName("quietText")
        self.table_note.setWordWrap(True)
        toggle_row = QHBoxLayout()
        toggle_row.setSpacing(8)
        toggle_row.addWidget(self.table_toggle, 0, Qt.AlignmentFlag.AlignTop)
        toggle_row.addWidget(self.table_note, 1)

        self.left_layout = QVBoxLayout()
        self.left_layout.setSpacing(14)
        self.left_layout.addLayout(self.top_row)
        self.left_layout.addWidget(self.history_note)
        self.left_layout.addLayout(self.title_row)
        self.left_layout.addWidget(self.view_stack)
        self.left_layout.addLayout(toggle_row)
        self.left_layout.addStretch(1)
        left = QWidget()
        left.setLayout(self.left_layout)

        self.side = QFrame()
        self.side.setObjectName("handoffSide")
        side = QVBoxLayout(self.side)
        self.side_layout = side
        side.setContentsMargins(*SIDE_MARGINS)
        side.setSpacing(12)
        heading = QLabel("Your handoff")
        heading.setObjectName("boardHeading")
        hint = QLabel("Name the finding and one concrete detail.")
        hint.setObjectName("quietText")
        hint.setWordWrap(True)
        side.addWidget(heading)
        side.addWidget(hint)
        self.sections: dict[str, GrowingText] = {}
        for key, name in SECTIONS:
            label = QLabel(name)
            label.setObjectName("handoffSectionLabel")
            label.setWordWrap(True)
            box = GrowingText()
            box.setAccessibleName(name)
            label.setBuddy(box)
            self.sections[key] = box
            group = QVBoxLayout()
            group.setSpacing(4)
            group.addWidget(label)
            if key in NOT_GRADED:
                note = QLabel(NOT_GRADED[key])
                note.setObjectName("quietText")
                note.setWordWrap(True)
                box.setAccessibleDescription(NOT_GRADED[key])
                group.addWidget(note)
            group.addWidget(box)
            box.textChanged.connect(self._boxes_edited)
            side.addLayout(group)

        saved_note = QLabel("Saved on this computer only")
        saved_note.setObjectName("quietText")
        saved_note.setWordWrap(True)
        saved_note.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.check_button = QPushButton("Check against timeline")
        self.check_button.setObjectName("primaryBtn")
        self.check_button.setAutoDefault(True)
        self.check_button.clicked.connect(self._check)
        check_row = QHBoxLayout()
        check_row.setSpacing(12)
        check_row.addWidget(saved_note, 1)
        check_row.addWidget(self.check_button, 0)
        side.addLayout(check_row)
        self.status = QLabel()
        self.status.setObjectName("quietText")
        self.status.setWordWrap(True)
        self.status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.status.hide()
        side.addWidget(self.status)

        self.feedback = FeedbackView()
        self.feedback.show_requested.connect(self._show_minutes)
        side.addSpacing(8)
        side.addWidget(self.feedback)
        side.addStretch(1)
        self.try_again_button = QPushButton("Try again")
        self.try_again_button.setAutoDefault(True)
        self.try_again_button.setEnabled(False)
        self.try_again_button.clicked.connect(self._try_again)
        self.next_button = _text_button("Next fictional run →")
        self.next_button.setAccessibleName("Next fictional run")
        self.next_button.clicked.connect(self._next_run)
        self.button_row = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.button_row.setSpacing(8)
        self.button_row.addWidget(self.try_again_button)
        self.button_row.addWidget(self.next_button)
        self.button_row.addStretch(1)
        side.addLayout(self.button_row)

        self.body_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(0)
        self.body_layout.addWidget(left, 1)
        self.body_layout.addWidget(self.side, 0)
        self.page_body = QWidget()
        self.page_body.setObjectName("handoffBody")
        self.page_body.setLayout(self.body_layout)
        self.page_scroll = QScrollArea()
        self.page_scroll.setWidgetResizable(True)
        self.page_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.page_scroll.setFrameShape(QFrame.Shape.NoFrame)
        # Tab goes from control to control; an unnamed scroll area is not a stop.
        self.page_scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.page_scroll.setWidget(self.page_body)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.page_scroll)

        self.case_box.currentIndexChanged.connect(self._select_case)
        self._show_case(self._case)
        self.reflow()

    # —— reading the page ——

    def section_text(self) -> dict[str, str]:
        """What is in each of the five boxes, by section key."""
        return {key: box.toPlainText() for key, box in self.sections.items()}

    def handoff_text(self) -> str:
        """The text the grader receives: the graded sections in order under their headings, blank ones left out."""
        return join_sections(self.section_text())

    # —— the run ——

    def _select_case(self, index: int) -> None:
        if index < 0:
            return
        self._show_case(self._cases[index])

    def _show_case(self, case: PracticeCase) -> None:
        self._case = case
        self.case_line.setText(f"{case.case_id} · invented timeline")
        self.title_label.setText(case.title)
        self.timeline_chart.set_case(case)
        self.timeline_table.set_case(case)
        self._clear_feedback()
        self._refresh_history()

    def _toggle_view(self) -> None:
        to_table = self.view_stack.currentWidget() is self.timeline_chart
        self.view_stack.setCurrentWidget(self.timeline_table if to_table else self.timeline_chart)
        self.table_toggle.setText("Show as chart" if to_table else "Show as table")

    # —— checking ——

    def _check(self) -> None:
        sections = self.section_text()
        if not has_text(sections):
            # Feedback for an earlier handoff must not sit beside "write something first".
            self._clear_feedback()
            self._say(EMPTY_HANDOFF)
            self.sections[SECTIONS[0][0]].setFocus()
            return
        joined = join_sections(sections)
        # Text only in a section that is not graded is saved, and nothing is graded.
        grades = grade(self._case, joined) if joined else ()
        try:
            record_attempt(self._case.case_id, joined, grades, sections=sections)
            saved = SAVED
        except OSError:
            saved = NOT_SAVED
        ungraded = tuple(heading_of(key) for key in NOT_GRADED if sections.get(key, "").strip())
        feedback = build_feedback(self._case, grades, graded=bool(joined), ungraded=ungraded)
        self._checked_sections = sections
        self.feedback.set_feedback(feedback)
        self.try_again_button.setEnabled(True)
        self._chain_focus()
        self._refresh_history()
        self._say(saved, f"{feedback.summary} {feedback.ungraded_note}".strip())
        QTimer.singleShot(0, self, self._scroll_to_feedback)

    def _scroll_to_feedback(self) -> None:
        """The feedback block starts at the top of the view, so the count and the first lines are all in sight."""
        self.body_layout.activate()
        self.page_scroll.verticalScrollBar().setValue(self.feedback.mapTo(self.page_body, QPoint(0, 0)).y())

    def _boxes_edited(self) -> None:
        if self._checked_sections is not None:
            self.feedback.set_stale(self.section_text() != self._checked_sections)

    def _say(self, text: str, then: str = "") -> None:
        self.status.setText(text)
        self.status.show()
        # Saying the same thing twice is still an answer to a second press.
        self.status.setProperty("lastAnnouncement", None)
        announce(self.status, f"{text} {then}".strip())

    def _clear_feedback(self) -> None:
        self._checked_sections = None
        self.feedback.set_feedback(None)
        self.timeline_chart.set_highlight(None)
        self.timeline_table.clearSelection()
        self.status.clear()
        self.status.hide()
        self.try_again_button.setEnabled(False)
        self._chain_focus()

    def _show_minutes(self, start: int, end: int) -> None:
        self.timeline_chart.set_highlight((start, end))
        self.timeline_table.select_minutes(start, end)
        announce(self.timeline_chart, f"Showing {minutes_text((start, end))} on the timeline.")

    def _try_again(self) -> None:
        self._clear_feedback()
        self.sections[SECTIONS[0][0]].setFocus()

    def _next_run(self) -> None:
        for box in self.sections.values():
            box.clear()
        index = (self.case_box.currentIndex() + 1) % len(self._cases)
        if index == self.case_box.currentIndex():
            self._show_case(self._cases[index])
        else:
            self.case_box.setCurrentIndex(index)
        self.sections[SECTIONS[0][0]].setFocus()

    # —— history ——

    def _read_history(self) -> tuple[Attempts, str]:
        """The saved attempts and a sentence about any that could not be read. A failed read never raises."""
        try:
            attempts = load_attempts()
        except OSError:
            return Attempts(), READ_FAILED
        return attempts, UNREADABLE if attempts.unreadable else ""

    def _refresh_history(self) -> None:
        attempts, problem = self._read_history()
        if problem == READ_FAILED:
            self.history_label.setText("Saved attempts: unknown")
        else:
            here = sum(1 for attempt in attempts if attempt.get("case_id") == self._case.case_id)
            self.history_label.setText(f"Saved attempts: {here} for this run · {len(attempts)} in all")
        self.history_note.setText(problem)
        self.history_note.setVisible(bool(problem))

    def show_history(self) -> None:
        attempts, problem = self._read_history()
        dialog = HistoryDialog(attempts, {case.case_id: case.title for case in self._cases}, self, problem)
        dialog.finished.connect(lambda _result: self._history_closed(dialog))
        self.history_dialog = dialog
        dialog.open()

    def _history_closed(self, dialog: QWidget) -> None:
        if self.history_dialog is dialog:
            self.history_dialog = None
        dialog.deleteLater()
        # Qt hands focus back as the dialog hides; this runs after it.
        QTimer.singleShot(0, self, self.history_button.setFocus)

    # —— keyboard order and layout ——

    def _chain_focus(self) -> None:
        """Tab follows the screen: picker, chart or table, the table switch, the boxes, Check, the Show
        links, Try again, Next, then History."""
        order: list[QWidget] = [
            self.case_box, self.timeline_chart, self.timeline_table, self.table_toggle,
            *self.sections.values(), self.check_button, *self.feedback.show_buttons,
            self.try_again_button, self.next_button, self.history_button,
        ]
        for first, second in zip(order, order[1:], strict=False):
            QWidget.setTabOrder(first, second)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.reflow()

    def reflow(self) -> None:
        """The handoff beside the timeline when the page is wide, under it when not; never a sideways scroll."""
        width = self.width()
        wide = width >= SIDE_BY_SIDE_WIDTH
        compact = width < COMPACT_WIDTH
        if wide != self._wide or compact != self._compact:
            self._wide, self._compact = wide, compact
            self.body_layout.setDirection(
                QBoxLayout.Direction.LeftToRight if wide else QBoxLayout.Direction.TopToBottom)
            if wide:
                self.left_layout.setContentsMargins(32, 20, 24, 28)
                self.side.setFixedWidth(SIDE_WIDTH)
            else:
                gutter = 16 if compact else 32
                self.left_layout.setContentsMargins(gutter, 20, gutter, 20)
                self.side.setMinimumWidth(0)
                self.side.setMaximumWidth(16777215)
            self.side_layout.setContentsMargins(*(COMPACT_SIDE_MARGINS if compact else SIDE_MARGINS))
            # One line while there is room; wrapped, it no longer sets the page's least width.
            self.history_label.setWordWrap(compact)
            self.button_row.setDirection(
                QBoxLayout.Direction.TopToBottom if compact else QBoxLayout.Direction.LeftToRight)
            self.side.setProperty("stacked", not wide)
            self.side.style().unpolish(self.side)
            self.side.style().polish(self.side)
        roomy = width - (SIDE_WIDTH if wide else 0) >= HEADER_ROW_WIDTH
        if roomy != self._roomy:
            self._roomy = roomy
            direction = QBoxLayout.Direction.LeftToRight if roomy else QBoxLayout.Direction.TopToBottom
            self.top_row.setDirection(direction)
            self.title_row.setDirection(direction)
            self.title_row.setAlignment(
                self.case_box, Qt.AlignmentFlag.AlignBottom if roomy else Qt.AlignmentFlag.AlignLeft)
            self.case_box.setMinimumWidth(PICKER_WIDTH if roomy else 0)
