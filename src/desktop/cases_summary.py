"""The Cases page's three summary charts, each with its title, its caption and the numbers it shows in words.

The pictures are matplotlib figures drawn from the design tokens in ``charts.py``; this module decides what
each one counts, writes what it counts in a caption, and keeps the same numbers as an accessible description.
"""

from __future__ import annotations

import io
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QPainter, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QStackedLayout, QVBoxLayout, QWidget

from src.desktop.charts import (
    LOGICAL_DPI,
    SUMMARY_CHART_HEIGHT,
    duration_ticks_figure,
    histogram_edges,
    rule_minutes_figure,
    score_histogram_figure,
)
from src.desktop.theme import active_theme, token_color

TOP_RULES = 5
# Rules tied with the last one shown are shown too, up to this many bars; the chart's height has room for no more
MOST_RULES_SHOWN = 8

SCORE_CAPTION = (
    "Anomaly score counts each flagged minute once at its worst severity and composites per episode. "
    "It is a count, not a risk score."
)
NO_SCORES = "No anomaly scores in this scope."
NO_FLAGGED_RULES = "No rule flagged a minute in these cases."
NO_DURATIONS = "No surgery durations in this scope."

# A chart builder returns a figure for a width and height in logical pixels, a pixel ratio and a theme name.
Build = Callable[[int, int, float, str], Any]


@dataclass(frozen=True)
class ChartSpec:
    """What one chart shows: its picture (None when there is nothing to draw), its words and its numbers in words."""

    caption: str
    description: str
    build: Build | None = None
    empty: str = ""


def _number(value: float) -> str:
    return f"{int(value):,}" if float(value).is_integer() else f"{value:,.1f}"


def _plural(count: int, word: str) -> str:
    return f"{count:,} {word}" if count == 1 else f"{count:,} {word}s"


def _column(frame: pd.DataFrame | None, name: str) -> pd.Series:
    if frame is None or frame.empty or name not in frame:
        return pd.Series(dtype=float)
    return pd.to_numeric(frame[name], errors="coerce").dropna()


def score_spec(cases: pd.DataFrame | None) -> ChartSpec:
    """Cases per anomaly-score range."""
    scores = _column(cases, "anomaly_score")
    if scores.empty:
        return ChartSpec(SCORE_CAPTION, NO_SCORES, empty=NO_SCORES)
    values = [float(v) for v in scores]
    edges = histogram_edges(values)
    counts = np.histogram(values, bins=edges)[0]
    ranges = "; ".join(
        # a score on an edge belongs to the range that starts there, so a range ends "under" its upper edge
        f"{edges[i]:g} to under {edges[i + 1]:g}, {int(count):,}" for i, count in enumerate(counts)
    )
    description = (
        f"Histogram of the anomaly score of {_plural(len(values), 'case')}, from {_number(min(values))} to "
        f"{_number(max(values))}. Cases in each score range: {ranges}."
    )
    return ChartSpec(
        SCORE_CAPTION, description,
        lambda width, height, dpr, theme: score_histogram_figure(
            values, width=width, height=height, dpi=LOGICAL_DPI * dpr, theme=theme),
    )


def rules_spec(flags: pd.DataFrame | None, labels: Mapping[str, str], *, minutes_column: str | None) -> ChartSpec:
    """The most frequent rules by flagged minutes: one row per flagged minute, or the episodes' own minutes."""
    if flags is None or flags.empty or "rule_id" not in flags:
        return ChartSpec("Minutes a rule flagged, summed over these cases.", NO_FLAGGED_RULES, empty=NO_FLAGGED_RULES)
    ids = flags["rule_id"].astype(str)
    if minutes_column and minutes_column in flags:
        totals = pd.to_numeric(flags[minutes_column], errors="coerce").fillna(0).groupby(ids).sum()
    else:
        totals = ids.groupby(ids).size()
    totals = totals[totals > 0].sort_values(ascending=False, kind="stable")
    if totals.empty:
        return ChartSpec("Minutes a rule flagged, summed over these cases.", NO_FLAGGED_RULES, empty=NO_FLAGGED_RULES)
    shown = len(totals)
    tied_out = 0
    if len(totals) > TOP_RULES:
        # the totals fall, so the rules tied with the fifth run from it to the last one at its total
        through_ties = int((totals >= totals.iloc[TOP_RULES - 1]).sum())
        shown = through_ties if through_ties <= MOST_RULES_SHOWN else TOP_RULES
        tied_out = through_ties - shown
    top = [(labels.get(str(rule), str(rule)), float(total)) for rule, total in totals.head(shown).items()]
    if len(totals) > shown:
        caption = (
            f"Minutes each rule flagged, summed over these cases. The {shown} most frequent of "
            f"{_plural(len(totals), 'rule')}."
        )
        if tied_out:
            caption += f" {tied_out:,} more tie with the last one shown."
    else:
        caption = f"Minutes each rule flagged, summed over these cases. All {_plural(len(totals), 'rule')} that flagged."
    description = "Most frequent rules by flagged minutes: " + ", ".join(
        f"{name}, {_number(value)}" for name, value in top) + "."
    return ChartSpec(
        caption, description,
        lambda width, height, dpr, theme: rule_minutes_figure(
            top, width=width, height=height, dpi=LOGICAL_DPI * dpr, theme=theme),
    )


def duration_spec(cases: pd.DataFrame | None) -> ChartSpec:
    """One tick per case on the surgery-duration axis, with the median."""
    durations = _column(cases, "case_duration_min")
    if durations.empty:
        return ChartSpec("Each tick is one case.", NO_DURATIONS, empty=NO_DURATIONS)
    values = [float(v) for v in durations]
    median = float(np.median(values))
    caption = f"Each tick is one case. Median {_number(round(median, 1))} min."
    description = (
        f"Surgery duration of {_plural(len(values), 'case')}, from {_number(min(values))} to "
        f"{_number(max(values))} minutes. Median {_number(round(median, 1))} minutes."
    )
    return ChartSpec(
        caption, description,
        lambda width, height, dpr, theme: duration_ticks_figure(
            values, width=width, height=height, dpi=LOGICAL_DPI * dpr, theme=theme),
    )


class SummaryChart(QWidget):
    """One matplotlib picture, drawn when the widget is painted so it always has its real size and the current theme.

    It takes keyboard focus only while it holds a picture: with a placeholder sentence there is nothing to read
    beyond the sentence itself.
    """

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("summaryChart")
        self.setFixedHeight(SUMMARY_CHART_HEIGHT)
        self.setAccessibleName(name)
        self._build: Build | None = None
        self._pixmap = QPixmap()
        self._key: tuple[int, int, int, float, str] | None = None

    def set_build(self, build: Build | None, description: str) -> None:
        self._build = build
        self._key = None
        self.setAccessibleDescription(description)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus if build is not None else Qt.FocusPolicy.NoFocus)
        self.update()

    def has_chart(self) -> bool:
        return self._build is not None

    def refresh(self) -> None:
        """Draw again in the current theme, never from an image made under the other one."""
        self._key = None
        self.update()

    def rendered(self) -> QPixmap:
        """The picture for the widget's present size and theme; drawn now if it is out of date."""
        self._render()
        return self._pixmap

    def _render(self) -> None:
        width, height, dpr, theme = self.width(), self.height(), self.devicePixelRatioF(), active_theme()
        key = (id(self._build), width, height, dpr, theme)
        if self._build is None or width <= 0 or key == self._key:
            return
        import matplotlib.pyplot as plt

        figure = self._build(width, height, dpr, theme)
        buffer = io.BytesIO()
        figure.savefig(buffer, format="png", dpi=figure.dpi, facecolor=figure.get_facecolor())
        plt.close(figure)
        pixmap = QPixmap()
        pixmap.loadFromData(buffer.getvalue())
        pixmap.setDevicePixelRatio(dpr)
        self._pixmap, self._key = pixmap, key

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), token_color("paper"))
        self._render()
        if not self._pixmap.isNull() and self._build is not None:
            painter.drawPixmap(QPointF(0, 0), self._pixmap)
        if self.hasFocus():
            painter.setPen(QPen(token_color("accent"), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self.rect().adjusted(1, 1, -2, -2))


class SummaryBlock(QWidget):
    """A chart under its title, with its caption below; a sentence takes the chart's place when there is no data."""

    def __init__(self, title: str, unit: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)
        heading = QHBoxLayout()
        heading.setSpacing(6)
        self.title = QLabel(title)
        self.title.setObjectName("stepHeading")
        heading.addWidget(self.title)
        self.unit = QLabel(unit)
        self.unit.setObjectName("quietText")
        # hidden, never shown, before it has a parent: showing a parentless widget opens a window of its own
        if not unit:
            self.unit.hide()
        heading.addWidget(self.unit)
        heading.addStretch(1)
        column.addLayout(heading)
        self.chart = SummaryChart(title)
        self.placeholder = QLabel()
        self.placeholder.setObjectName("quietText")
        self.placeholder.setWordWrap(True)
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        # The layout belongs to its holder before widgets join it, or the first one would show as a window of its own
        holder = QWidget()
        self._stack = QStackedLayout(holder)
        self._stack.addWidget(self.chart)
        self._stack.addWidget(self.placeholder)
        holder.setFixedHeight(SUMMARY_CHART_HEIGHT)
        column.addWidget(holder)
        self.caption = QLabel()
        self.caption.setObjectName("quietText")
        self.caption.setWordWrap(True)
        self.caption.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        column.addWidget(self.caption)
        column.addStretch(1)

    def set_spec(self, spec: ChartSpec) -> None:
        self.caption.setText(spec.caption)
        self.chart.set_build(spec.build, spec.description)
        self.placeholder.setText(spec.empty)
        self._stack.setCurrentWidget(self.chart if spec.build is not None else self.placeholder)

    def refresh(self) -> None:
        self.chart.refresh()
