"""Feedback on a handoff: the grader's items as plain-worded lines, each with a mark, and a Show link where it has minutes."""

from __future__ import annotations

import html
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from src.desktop.theme import token_color
from src.modules.handoff_trainer.grading import Grade, PracticeCase, detect
from src.modules.handoff_trainer.history import found_counts
from src.modules.handoff_trainer.timeline_chart import MinuteSpan, minutes_text

NOTHING_NEEDED = "Nothing on this timeline needs a handoff line."
# The grader's three statuses, said the way a learner reads them; the raw words are never shown.
LEAD_INS = {
    "mentioned": "Matches the timeline.",
    "unsupported": "Not on the timeline.",
    "missed": "Left out.",
}
_MARK_SIZE = (20, 22)


@dataclass(frozen=True)
class FeedbackLine:
    status: str
    lead_in: str
    evidence: str
    minutes: MinuteSpan | None

    @property
    def text(self) -> str:
        return f"{self.lead_in} {self.evidence}"


@dataclass(frozen=True)
class Feedback:
    lines: tuple[FeedbackLine, ...]
    found: int
    total: int

    @property
    def summary(self) -> str:
        return f"{self.found} of {self.total} found" if self.total else NOTHING_NEEDED


def build_feedback(case: PracticeCase, grades: tuple[Grade, ...]) -> Feedback:
    """Lines in the grader's order. An item has minutes when it is about a timeline event that has them.

    The grader's evidence sentence ends with the event's own sentence, which is how a grade finds its event.
    """
    events = [event for event in detect(case) if event.start_minute is not None and event.end_minute is not None]
    lines: list[FeedbackLine] = []
    for item in grades:
        match = next((event for event in events if event.kind == item.kind and item.evidence.endswith(event.evidence)),
                     None)
        minutes = None if match is None else (int(match.start_minute or 0), int(match.end_minute or 0))
        lines.append(FeedbackLine(item.status, LEAD_INS[item.status], item.evidence, minutes))
    found, total = found_counts(item.status for item in grades)
    return Feedback(tuple(lines), found, total)


class FeedbackMark(QWidget):
    """The line's mark, drawn in ink: a tick, a triangle with a bar, or a ring. Shape and words carry the meaning."""

    def __init__(self, status: str) -> None:
        super().__init__()
        self._status = status
        self.setFixedSize(*_MARK_SIZE)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    @property
    def status(self) -> str:
        return self._status

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(token_color("ink"), 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        x, y = 4.0, 5.0
        if self._status == "mentioned":
            painter.drawPolyline(QPolygonF([QPointF(x, y + 7), QPointF(x + 4, y + 11), QPointF(x + 11, y + 2)]))
        elif self._status == "unsupported":
            painter.drawPolygon(QPolygonF([QPointF(x + 6, y), QPointF(x + 12.5, y + 11.5), QPointF(x - 0.5, y + 11.5)]))
            painter.drawLine(QPointF(x + 6, y + 4), QPointF(x + 6, y + 7.5))
            painter.drawPoint(QPointF(x + 6, y + 9.5))
        else:
            painter.drawEllipse(QRectF(x, y, 12, 12))


class FeedbackView(QWidget):
    """The heading, the count line and one row per item. ``show_requested`` carries the minutes of a Show link."""

    show_requested = Signal(int, int)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("handoffFeedback")
        self.heading = QLabel("Feedback on the text")
        self.heading.setObjectName("stepHeading")
        self.heading.setAccessibleName("Feedback on the text")
        self.note = QLabel("Compares what you wrote with the invented timeline. It is not a judgment of care.")
        self.note.setObjectName("quietText")
        self.note.setWordWrap(True)
        self.summary = QLabel()
        self.summary.setObjectName("stepHeading")
        self.summary.setWordWrap(True)
        self._rows = QVBoxLayout()
        self._rows.setSpacing(10)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.heading)
        layout.addWidget(self.note)
        layout.addSpacing(4)
        layout.addWidget(self.summary)
        layout.addLayout(self._rows)
        self.text_labels: list[QLabel] = []
        self.marks: list[FeedbackMark] = []
        self.show_buttons: list[QPushButton] = []
        self.feedback: Feedback | None = None
        self.hide()

    def set_feedback(self, feedback: Feedback | None) -> None:
        self._clear()
        self.feedback = feedback
        if feedback is None:
            self.hide()
            return
        self.summary.setText(feedback.summary)
        for line in feedback.lines:
            self._add_row(line)
        self.show()

    def _add_row(self, line: FeedbackLine) -> None:
        row = QHBoxLayout()
        row.setSpacing(8)
        mark = FeedbackMark(line.status)
        label = QLabel(f"<b>{html.escape(line.lead_in)}</b> {html.escape(line.evidence)}")
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setWordWrap(True)
        label.setObjectName("bodyText")
        label.setAccessibleName(line.text)
        label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        row.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(label, 1)
        self.marks.append(mark)
        self.text_labels.append(label)
        if line.minutes is not None:
            start, end = line.minutes
            button = QPushButton("Show")
            button.setObjectName("linkBtn")
            button.setAutoDefault(True)
            button.setAccessibleName(f"Show {minutes_text(line.minutes)} on the timeline")
            button.clicked.connect(lambda _checked=False, a=start, b=end: self.show_requested.emit(a, b))
            row.addWidget(button, 0, Qt.AlignmentFlag.AlignTop)
            self.show_buttons.append(button)
        self._rows.addLayout(row)

    def _clear(self) -> None:
        while self._rows.count():
            item = self._rows.takeAt(0)
            self._drop(item)
        self.text_labels.clear()
        self.marks.clear()
        self.show_buttons.clear()

    def _drop(self, item) -> None:
        layout = item.layout()
        if layout is not None:
            while layout.count():
                self._drop(layout.takeAt(0))
            layout.deleteLater()
        widget = item.widget()
        if widget is not None:
            widget.hide()
            widget.deleteLater()
