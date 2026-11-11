"""A threshold ruler: where a rule's limits sit on a scale and where the episode's extreme fell.

The research page's selected-rule panel and the rule reference drawer use it. It is painted from the
design tokens, so it follows the theme, and it carries the same numbers as words for a screen reader.

Exactly these rules get a ruler, in every preset (``test_the_modules_docstring_names_the_rules_that_get_a_ruler``
checks this list against the catalog):

- threshold rules that fire at or beyond a warn and a critical limit: pip_high, peep_high, tv_low_mlkg,
  tv_high_mlkg, rr_low, rr_high, etco2_high, etco2_low, agent_high, spo2_low, hr_high, hr_low, map_low;
- the slope rule pip_rising, which has one limit, written "fires ≥ 0.5";
- agent_low_maint, which has one limit, written "fires < 0.3": the pipeline flags a MAC strictly below it.

Rules without a single numeric limit get no ruler: the duration rules (peep_zero_long, etco2_zero_vent,
fio2_high_long, fio2_room_air_vent, agent_drift, agent_fi_et_gap), the band rule tv_abs_extreme and the
composite rules (compliance_concern, hypoventilation_pattern, desat_with_vent_issue).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from src.desktop.fonts import tabular
from src.desktop.theme import token_color
from src.rules import RuleSpec

_MARGIN = 8.0
_AXIS_Y = 20.0
_FIRST_LABEL_BASELINE = 44.0
_LABEL_ROW = 15.0
_LABEL_GAP = 8.0
_TICKS = 6
_FONT_PIXELS = 12
# Every threshold of a threshold rule is one of these severities, in the order the ruler reads them.
_SEVERITY_KEYS = (("warn", ("warn", "warn_mac")), ("critical", ("critical", "critical_mac")))
_SLOPE_KEY = "min_slope_cmh2o_per_min"
# A threshold rule with this one key fires strictly below it (the pipeline compares with <), so it is written "<".
_BELOW_KEY = "max_mac"


def pretty_unit(unit: str) -> str:
    """The catalog's plain-text unit as the design writes it."""
    return unit.replace("cmH2O", "cmH₂O")


def _number(value: float) -> str:
    return f"{value:g}"


@dataclass(frozen=True)
class RulerMark:
    """One limit on the scale: its value, its severity word, and the text written under it."""

    value: float
    word: str
    label: str
    strict: bool = False  # the rule fires strictly beyond the value, not at it


@dataclass(frozen=True)
class RulerSpec:
    """What a ruler shows: the rule, its limits, and the extreme the episode reached (None when not recorded)."""

    rule: str
    unit: str
    direction: str  # "high": the rule fires at or above its limits; "low": at or below
    marks: tuple[RulerMark, ...]
    observed: float | None
    observed_label: str
    episode: bool = True  # False for the rule reference, where no episode was measured

    def description(self) -> str:
        """The ruler's numbers in words, for a screen reader and for tests."""
        unit = f" {self.unit}" if self.unit else ""

        def side(mark: RulerMark) -> str:
            if mark.strict:
                return "more than" if self.direction == "high" else "less than"
            return "or more" if self.direction == "high" else "or less"

        limits = "; ".join(
            f"{mark.word} at {side(mark)} {_number(mark.value)}{unit}" if mark.strict
            else f"{mark.word} at {_number(mark.value)}{unit} {side(mark)}"
            for mark in self.marks
        )
        if not self.episode:
            return f"Threshold ruler for {self.rule}. Limits: {limits}."
        if self.observed is None:
            seen = "No value was recorded for this signal during the episode."
        else:
            extreme = "maximum" if self.direction == "high" else "minimum"
            seen = f"Observed {extreme} during the episode: {_number(self.observed)}{unit}."
        return f"Threshold ruler for {self.rule}. Limits: {limits}. {seen}"


def ruler_for(spec: RuleSpec, observed: float | None, *, episode: bool = True) -> RulerSpec | None:
    """The ruler for ``spec`` in the loaded preset, or None when the rule has no single numeric limit.

    ``observed`` is the episode's maximum for a rule that fires high and its minimum for one that fires low.
    ``episode=False`` is the rule reference, which has no episode: its description then says nothing of one.
    """
    if spec.direction not in ("high", "low"):
        return None
    sign = "≥" if spec.direction == "high" else "≤"
    marks: list[RulerMark] = []
    if spec.kind == "threshold":
        for word, keys in _SEVERITY_KEYS:
            for key in keys:
                value = spec.params.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    marks.append(RulerMark(float(value), word, f"{word} {sign} {_number(float(value))}"))
        value = spec.params.get(_BELOW_KEY)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and not marks:
            below = "<" if spec.direction == "low" else ">"
            marks.append(RulerMark(float(value), "fires", f"fires {below} {_number(float(value))}", strict=True))
    elif spec.kind == "slope":
        value = spec.params.get(_SLOPE_KEY)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            marks.append(RulerMark(float(value), "fires", f"fires {sign} {_number(float(value))}"))
    if not marks:
        return None
    word = "max" if spec.direction == "high" else "min"
    label = "" if observed is None else f"{word} {_number(observed)}"
    return RulerSpec(spec.label, pretty_unit(spec.unit), spec.direction, tuple(marks), observed, label, episode)


def _nice_step(raw: float) -> float:
    exponent = math.floor(math.log10(raw))
    fraction = raw / 10 ** exponent
    for nice in (1, 2, 2.5, 5, 10):
        if fraction <= nice:
            return nice * 10 ** exponent
    return 10 ** (exponent + 1)


def scale_for(spec: RulerSpec) -> tuple[float, float, float]:
    """The scale's low end, high end and tick step, rounded out to whole ticks."""
    values = [mark.value for mark in spec.marks] + ([] if spec.observed is None else [spec.observed])
    low, high = min(values), max(values)
    span = high - low if high > low else (abs(high) * 0.2 or 1.0)
    pad = max(span * 0.4, abs(high) * 0.1)
    start, end = low - pad, high + pad
    # A scale that starts at zero when the values are far from it reads as proportion, not just position.
    if low >= 0 and low <= 0.7 * high:
        start = 0.0
    elif low >= 0:
        start = max(start, 0.0)
    step = _nice_step((end - start) / _TICKS)
    return round(math.floor(start / step) * step, 10), round(math.ceil(end / step) * step, 10), step


class ThresholdRuler(QWidget):
    """A scale with the rule's limits marked, the firing side shaded, and the observed extreme as a pointer."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("thresholdRuler")
        self.setAccessibleName("Threshold ruler")
        self._spec: RulerSpec | None = None
        self._items: list[tuple[str, float, str, int]] = []  # text, centre x, style, row
        self.setVisible(False)

    def set_ruler(self, spec: RulerSpec | None) -> None:
        """Show ``spec``, or hide the ruler when the rule has none."""
        self._spec = spec
        self.setAccessibleDescription(spec.description() if spec is not None else "")
        self.setVisible(spec is not None)
        self._arrange()
        self.update()

    def spec(self) -> RulerSpec | None:
        return self._spec

    def label_rows(self) -> list[tuple[str, int]]:
        """The text written under the ruler and the row each sits in, so a test can see what collided."""
        return [(text, row) for text, _x, _style, row in self._items]

    def _font(self, *, bold: bool = False) -> QFont:
        font = tabular(QFont(self.font()))
        font.setPixelSize(_FONT_PIXELS)
        font.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
        return font

    def _x_of(self, value: float, low: float, high: float) -> float:
        return _MARGIN + (value - low) / (high - low) * (self.width() - 2 * _MARGIN)

    def _arrange(self) -> None:
        """Place the labels in as few rows as keep them apart, and size the widget to the rows."""
        self._items = []
        spec = self._spec
        if spec is None or self.width() <= 2 * _MARGIN:
            return
        low, high, _step = scale_for(spec)
        wanted: list[tuple[str, float, str]] = []
        unit = spec.unit
        # The unit sits at the right edge; the others sit under what they describe.
        if unit:
            wanted.append((unit, float(self.width()) - _MARGIN, "unit"))
        wanted.extend((mark.label, self._x_of(mark.value, low, high), "mark") for mark in spec.marks)
        if spec.observed is not None:
            wanted.append((spec.observed_label, self._x_of(spec.observed, low, high), "observed"))
        occupied: list[list[tuple[float, float]]] = []
        for text, x, style in wanted:
            metrics = QFontMetricsF(self._font(bold=style == "observed"))
            width = metrics.horizontalAdvance(text)
            if style == "unit":
                left = x - width
            else:
                left = min(max(x - width / 2, 0.0), self.width() - width)
            row = 0
            while row < len(occupied) and any(left < end + _LABEL_GAP and left + width > start - _LABEL_GAP
                                              for start, end in occupied[row]):
                row += 1
            if row == len(occupied):
                occupied.append([])
            occupied[row].append((left, left + width))
            self._items.append((text, left, style, row))
        self.setFixedHeight(int(_FIRST_LABEL_BASELINE + (len(occupied) - 1) * _LABEL_ROW + 6))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._arrange()

    def paintEvent(self, event) -> None:  # noqa: N802
        spec = self._spec
        if spec is None or self.width() <= 2 * _MARGIN:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        low, high, _step = scale_for(spec)
        ink, ink_2, ink_3 = token_color("ink"), token_color("ink_2"), token_color("ink_3")
        end_x = self.width() - _MARGIN
        painter.setPen(QPen(ink_3, 1.2))
        painter.drawLine(QPointF(_MARGIN, _AXIS_Y), QPointF(end_x, _AXIS_Y))
        for value in tick_values(spec):
            x = self._x_of(value, low, high)
            painter.drawLine(QPointF(x, _AXIS_Y - 3), QPointF(x, _AXIS_Y + 3))
        firing = token_color("ink")
        firing.setAlphaF(0.10)
        edge = self._x_of(min(mark.value for mark in spec.marks), low, high) if spec.direction == "high" \
            else self._x_of(max(mark.value for mark in spec.marks), low, high)
        left, right = (edge, end_x) if spec.direction == "high" else (_MARGIN, edge)
        painter.fillRect(QRectF(left, _AXIS_Y - 5, right - left, 10), firing)
        painter.setPen(QPen(ink, 2))
        for mark in spec.marks:
            x = self._x_of(mark.value, low, high)
            painter.drawLine(QPointF(x, _AXIS_Y - 11), QPointF(x, _AXIS_Y + 11))
        if spec.observed is not None:
            x = self._x_of(spec.observed, low, high)
            pointer = QPainterPath()
            pointer.moveTo(x, _AXIS_Y - 10)
            pointer.lineTo(x - 5, _AXIS_Y - 17)
            pointer.lineTo(x + 5, _AXIS_Y - 17)
            pointer.closeSubpath()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(token_color("accent"))
            painter.drawPath(pointer)
        colors = {"mark": ink_2, "observed": ink, "unit": ink_3}
        for text, left_x, style, row in self._items:
            painter.setFont(self._font(bold=style == "observed"))
            painter.setPen(colors[style])
            painter.drawText(QPointF(left_x, _FIRST_LABEL_BASELINE + row * _LABEL_ROW), text)


def tick_values(spec: RulerSpec) -> Sequence[float]:
    """The values the ruler's ticks sit at."""
    low, high, step = scale_for(spec)
    first = math.ceil(low / step - 1e-9)
    return [round(k * step, 10) for k in range(first, int(math.floor(high / step + 1e-9)) + 1)]
