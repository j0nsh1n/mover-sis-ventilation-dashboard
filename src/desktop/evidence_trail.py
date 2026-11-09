"""Pieces of the research page's evidence trail: the numbered spine and the observation sparkline."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QVBoxLayout, QWidget

from src.desktop.theme import token_color

PIN = 22
# Space left of a step's content for the pin and the spine, so every step shares one left edge.
GUTTER = 40
STEP_GAP = 28
_SPINE_X = PIN / 2
_SPARK_HEIGHT = 72
_SPARK_GRID_LINES = 8


class TrailStep(QWidget):
    """One step on the spine: a pin at the top left and the step's content to its right.

    ``number`` is drawn in the pin. ``None`` draws the filled "!" pin that marks a limit.
    Each step draws its own stretch of the spine down to its bottom edge, so a hidden step
    leaves no gap in the line.
    """

    def __init__(self, number: int | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._number = number
        self.setObjectName("trailStep")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(GUTTER, 0, 0, STEP_GAP)
        self.body.setSpacing(6)
        self._last = False

    def set_last(self, last: bool) -> None:
        self._last = last
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        ink, rule = token_color("ink"), token_color("rule_strong")
        if not self._last:
            painter.setPen(QPen(rule, 1.5))
            painter.drawLine(QPointF(_SPINE_X, PIN), QPointF(_SPINE_X, self.height()))
        circle = QRectF(1, 1, PIN - 2, PIN - 2)
        font = QFont(self.font())
        font.setPixelSize(13)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        if self._number is None:
            painter.setPen(QPen(ink, 1.5))
            painter.setBrush(ink)
            painter.drawEllipse(circle)
            painter.setPen(token_color("paper"))
            painter.drawText(circle, int(Qt.AlignmentFlag.AlignCenter), "!")
        else:
            painter.setPen(QPen(ink, 1.5))
            painter.setBrush(token_color("paper"))
            painter.drawEllipse(circle)
            painter.setPen(ink)
            painter.drawText(circle, int(Qt.AlignmentFlag.AlignCenter), str(self._number))


class Sparkline(QWidget):
    """The observation's trace: one signal over the case, with the observed minute marked.

    Painted from tokens, so it follows the theme. Missing samples break the line instead of
    being joined across. Without samples it shows its placeholder sentence.
    """

    def __init__(self, placeholder: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sparkline")
        self.setFixedHeight(_SPARK_HEIGHT)
        self.setMinimumWidth(160)
        self._values: tuple[float | None, ...] = ()
        self._marker: int | None = None
        self._placeholder = placeholder
        self._label = "PIP"
        self._color = "s_pip"
        self.setAccessibleName("Signal trace")

    def set_series(self, values: Sequence[float | None], marker: int | None, description: str = "", *,
                   label: str = "PIP", color: str = "s_pip") -> None:
        """Draw ``values`` as the signal ``label`` in the token ``color``, with the minute ``marker`` marked."""
        self._values = tuple(values)
        self._marker = marker if marker is not None and 0 <= marker < len(self._values) else None
        self._label, self._color = label, color
        self.setAccessibleDescription(description)
        self.update()

    def clear(self, placeholder: str) -> None:
        self._values = ()
        self._marker = None
        self._placeholder = placeholder
        self.setAccessibleDescription(placeholder)
        self.update()

    def state(self) -> tuple[str, tuple[float | None, ...], int | None]:
        """What is drawn, so a test can tell whether the board changed."""
        return self._placeholder, self._values, self._marker

    def signal(self) -> tuple[str, str]:
        """The drawn signal's name and colour token."""
        return self._label, self._color

    def has_series(self) -> bool:
        return bool(self._values)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        width, height = self.width(), self.height()
        if not self._values:
            painter.setPen(token_color("ink_3"))
            font = QFont(self.font())
            font.setPixelSize(13)
            painter.setFont(font)
            painter.drawText(QRectF(0, 0, width, height), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                             self._placeholder)
            return
        painter.setPen(QPen(token_color("grid"), 1))
        for step in range(_SPARK_GRID_LINES + 1):
            x = round(step * (width - 1) / _SPARK_GRID_LINES) + 0.5
            painter.drawLine(QPointF(x, 0), QPointF(x, height))
        known = [value for value in self._values if value is not None]
        low, high = (min(known), max(known)) if known else (0.0, 1.0)
        span = max(high - low, 1.0)
        top, bottom = 8.0, height - 8.0
        last = max(len(self._values) - 1, 1)

        def point(index: int, value: float) -> QPointF:
            return QPointF(2 + index * (width - 4) / last, bottom - (value - low) / span * (bottom - top))

        path = QPainterPath()
        drawing = False
        for index, value in enumerate(self._values):
            if value is None:
                drawing = False
                continue
            if drawing:
                path.lineTo(point(index, value))
            else:
                path.moveTo(point(index, value))
                drawing = True
        painter.setPen(QPen(token_color(self._color), 1.5))
        painter.drawPath(path)
        marker = self._marker
        if marker is not None:
            # The minute line always shows; the dot only where a value was recorded, so a gap stays a gap.
            # Snapped to the pixel grid like the grid lines, so the mark stays crisp.
            x = round(2 + marker * (width - 4) / last) + 0.5
            painter.setPen(QPen(token_color("accent"), 1.5))
            painter.drawLine(QPointF(x, 0), QPointF(x, height))
            value = self._values[marker]
            if value is not None:
                painter.setBrush(token_color("paper"))
                painter.drawEllipse(point(marker, value), 4.0, 4.0)
        font = QFont(self.font())
        font.setPixelSize(11)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(token_color(self._color))
        painter.drawText(QRectF(4, 0, width - 8, 14), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop),
                         self._label)
