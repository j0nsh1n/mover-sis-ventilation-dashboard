"""The record's chart: vital tracks and a drug lane on one minute axis, drawn from the tokens.

A reading recorded as missing is a hatched, labelled box; a trace is never joined across a minute the record has
no value for, and nothing is drawn as zero. A bolus is a mark, an infusion a bar. The cursor, the values at the
cursor and its tag are painted over the picture, so stepping the cursor does not redraw it.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass

import matplotlib
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle
from matplotlib.ticker import MaxNLocator
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QKeyEvent, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

from src.desktop.charts import LOGICAL_DPI, chart_family, px_to_pt, token_rgba
from src.desktop.fonts import tabular
from src.desktop.theme import active_theme, token_color, tokens
from src.modules.medication_vitals.align import END_BEFORE_START, _number
from src.modules.medication_vitals.timeline import Dose, Timeline, Track, cursor_text, describe, value_at

# Layout in logical pixels. Below COMPACT_BELOW the names sit above their tracks instead of beside them, so the
# plot keeps its width on a narrow window.
COMPACT_BELOW = 560
LABEL_WIDTH = 204  # a track's name, unit and value at the cursor, then its y-axis numbers
COMPACT_LEFT = 34
RIGHT_PAD = 16
COMPACT_RIGHT = 12
TOP_PAD = 8
TRACK_HEIGHT = 70
COMPACT_TRACK_HEIGHT = 60
COMPACT_HEAD = 22
TRACK_GAP = 12
LANE_ROW = 26
STACKED_LANE_ROW = 42  # compact: a dose's label above its mark, so the label can use the whole width
LANE_PAD = 12
LANE_HEAD = 26  # compact only: the lane's title above it
AXIS_HEIGHT = 36
COMPACT_AXIS_HEIGHT = 46
MAX_LANE_ROWS = 6
_MARK_HALF = 6
_LABEL_GAP = 6
_ROW_GAP = 8
LEFT_OUT = -1  # the row of a dose the lane has no room to label
# The record's own word for a reading it has a row for but no value, as the table and the description write it.
_MISSING_WORD = "missing"
LANE_KEY = "bolus mark · infusion bar"
REVERSED_KEY = f"open diamond: {END_BEFORE_START}"
KEY_FIRST = 19  # the key's first line sits this far under the lane's top when the names are beside the tracks
KEY_LINE = 13
NOTE_HEIGHT = 18  # the line under the chart that says how many doses it leaves out


def is_compact(width: int) -> bool:
    return width < COMPACT_BELOW


@dataclass(frozen=True)
class _Placed:
    dose: Dose
    row: int
    label_left: bool
    label_x: float  # px from the plot's left edge; set only when the label is stacked above its mark
    fits: bool  # beside its mark, the label has room on one side


class _Measure:
    """Text widths in logical pixels, measured with matplotlib so the layout agrees with what is drawn."""

    def __init__(self, dpi: float, family: str) -> None:
        self._probe = Figure(figsize=(1, 1), dpi=dpi)
        self._canvas = FigureCanvasAgg(self._probe)
        self._scale = dpi / LOGICAL_DPI
        self._family = family

    def width(self, text: str, size: float, weight: int) -> float:
        item = self._probe.text(0, 0, text, fontsize=px_to_pt(size), fontfamily=self._family, fontweight=weight)
        measured = item.get_window_extent(self._canvas.get_renderer()).width / self._scale
        item.remove()
        return measured


def _place_doses(
    timeline: Timeline, plot_px: float, low: float, per_minute: float, measure: _Measure, stacked: bool,
    room: tuple[float, float],
) -> list[_Placed]:
    """Give each dose a lane row so labels never overlap.

    Beside the mark, a label that would leave the plot goes to the mark's left. Stacked, the label sits above
    the mark and is held inside ``room`` (the plot's left and right ends, in px from the plot's left edge).
    """
    taken: list[list[tuple[float, float]]] = []
    placed: list[_Placed] = []
    size = 11 if stacked else 12
    for dose in timeline.doses:
        left, right = (dose.start - low) * per_minute, (dose.end - low) * per_minute
        if dose.infusion:
            right = max(right, left + 2 * _MARK_HALF)
        else:
            left, right = left - _MARK_HALF, left + _MARK_HALF
        text = measure.width(dose.label, size, 600)
        label_left, label_x = False, 0.0
        fits = right + _LABEL_GAP + text <= plot_px or left - _LABEL_GAP - text >= 0
        if stacked:
            label_x = min(max(left, room[0]), max(room[1] - text, room[0]))
            extent = (min(left, label_x), max(right, label_x + text))
        else:
            label_left = right + _LABEL_GAP + text > plot_px and left - _LABEL_GAP - text >= 0
            extent = (left - _LABEL_GAP - text, right) if label_left else (left, right + _LABEL_GAP + text)
        row = next((index for index, spans in enumerate(taken)
                    if all(extent[0] >= end + _ROW_GAP or extent[1] <= start - _ROW_GAP for start, end in spans)),
                   None)
        if row is None:
            if len(taken) == MAX_LANE_ROWS:
                # No row is free of the labels already there: this dose is left to the table, never drawn over another.
                placed.append(_Placed(dose, LEFT_OUT, label_left, label_x, fits))
                continue
            row = len(taken)
            taken.append([])
        taken[row].append(extent)
        placed.append(_Placed(dose, row, label_left, label_x, fits))
    return placed


def y_range(track: Track) -> tuple[float, float]:
    """The track's y range: its least range, widened to hold every reading."""
    low_y, high_y = track.limits
    found = track.present()
    if found:
        lowest, highest = min(found.values()), max(found.values())
        pad = max((highest - lowest) * 0.1, 1.0)
        low_y, high_y = min(low_y, lowest - pad), max(high_y, highest + pad)
    return low_y, high_y


def _frame(timeline: Timeline) -> tuple[float, float]:
    span = timeline.span()
    return (span[0] - 0.5, span[1] + 0.5) if span else (-0.5, 9.5)


def _plot_width(width: int) -> float:
    left, right = (COMPACT_LEFT, COMPACT_RIGHT) if is_compact(width) else (LABEL_WIDTH, RIGHT_PAD)
    return max(width - left - right, 40.0)


def chart_height(timeline: Timeline, width: int, lane_rows: int = 1, stacked: bool | None = None,
                 extra: int = 0) -> int:
    """The height the chart needs at ``width``; the drug lane's rows decide the last part, ``extra`` px more."""
    compact = is_compact(width)
    stacked = compact if stacked is None else stacked
    track = (COMPACT_TRACK_HEIGHT + COMPACT_HEAD) if compact else TRACK_HEIGHT
    tracks = len(timeline.tracks) * (track + TRACK_GAP)
    lane = max(lane_rows, 1) * (STACKED_LANE_ROW if stacked else LANE_ROW) + (LANE_HEAD if compact else 0)
    return TOP_PAD + tracks + LANE_PAD + lane + (COMPACT_AXIS_HEIGHT if compact else AXIS_HEIGHT) + extra


@dataclass(frozen=True)
class RenderedTimeline:
    png: bytes
    width: int
    height: int
    xlim: tuple[float, float]
    axes: tuple[QRectF, ...]  # the tracks, then the drug lane, in widget pixels
    left_out: int = 0  # doses the lane had no room to label, so none is drawn; the table lists them


def build_timeline_figure(
    timeline: Timeline, width: int, theme: str | None, dpr: float = 1.0
) -> tuple[Figure, tuple[QRectF, ...], tuple[float, float]]:
    """The figure for ``timeline`` at ``width`` logical pixels, the axes' boxes in widget pixels and the x limits."""
    with matplotlib.rc_context({"font.family": chart_family(), "hatch.linewidth": 0.8}):
        fig, boxes, xlim, _left_out = _build(timeline, width, theme, dpr)
        return fig, boxes, xlim


def render_timeline(timeline: Timeline, width: int, theme: str | None, dpr: float = 1.0) -> RenderedTimeline:
    with matplotlib.rc_context({"font.family": chart_family(), "hatch.linewidth": 0.8}):
        fig, boxes, xlim, left_out = _build(timeline, width, theme, dpr)
        buffer = io.BytesIO()
        FigureCanvasAgg(fig).print_png(buffer)
    return RenderedTimeline(buffer.getvalue(), width, round(fig.get_figheight() * LOGICAL_DPI), xlim, boxes, left_out)


def _runs(minutes: list[int]) -> list[tuple[int, int]]:
    """Consecutive minutes as (first, last)."""
    runs: list[tuple[int, int]] = []
    for minute in sorted(minutes):
        if runs and minute == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], minute)
        else:
            runs.append((minute, minute))
    return runs


def _wrap(text: str, widths: list[float], measure: _Measure, first: int = 0) -> list[str]:
    """``text`` broken at spaces into lines no wider than the matching entry of ``widths`` (the last one repeats).

    ``first`` is the index of the line the text starts on, so the entry it starts with is the right one.
    """
    lines: list[str] = []
    for word in text.split(" "):
        limit = widths[min(first + max(len(lines) - 1, 0), len(widths) - 1)]
        if lines and measure.width(f"{lines[-1]} {word}", 11, 400) <= limit:
            lines[-1] += f" {word}"
        else:
            lines.append(word)
    return lines


def lane_key(timeline: Timeline) -> tuple[str, ...]:
    """What the drug lane's marks mean; a dose whose end is before its start has a mark of its own, named here."""
    if any(dose.reversed_times for dose in timeline.doses):
        return (LANE_KEY, REVERSED_KEY)
    return (LANE_KEY,)


def left_out_text(count: int) -> str:
    return f"{count} more {'dose is' if count == 1 else 'doses are'} in the table only."


def _build(
    timeline: Timeline, width: int, theme: str | None, dpr: float
) -> tuple[Figure, tuple[QRectF, ...], tuple[float, float], int]:
    colors = tokens(theme)
    family = chart_family()
    dpi = LOGICAL_DPI * dpr
    compact = is_compact(width)
    left_px, right_px = (COMPACT_LEFT, COMPACT_RIGHT) if compact else (LABEL_WIDTH, RIGHT_PAD)
    plot_px = _plot_width(width)
    low, high = _frame(timeline)
    per_minute = plot_px / (high - low)
    measure = _Measure(dpi, family)
    room = (4 - left_px, plot_px + right_px - 4)
    stacked = compact
    placed = _place_doses(timeline, plot_px, low, per_minute, measure, stacked, room)
    if not stacked and not all(item.fits for item in placed):
        # A label with no room beside its mark goes above it, for every dose, so the lane reads one way.
        stacked = True
        placed = _place_doses(timeline, plot_px, low, per_minute, measure, stacked, room)
    lane_rows = max((item.row for item in placed), default=0) + 1
    left_out = sum(1 for item in placed if item.row == LEFT_OUT)
    # The key may need more than one line: beside the track names it sits in their column, under the lane's title
    # when the names are above the tracks. Either way the room for it is made, never taken from a dose's row.
    title_width = measure.width("Drugs", 12, 600)
    key_widths: list[float] = [width - title_width - 16, width - 8] if compact else [LABEL_WIDTH - 12.0]
    key_lines: list[str] = []
    for meaning in lane_key(timeline):
        key_lines += _wrap(meaning, key_widths, measure, len(key_lines))
    extra = NOTE_HEIGHT if left_out else 0
    if compact:
        extra += KEY_LINE * (len(key_lines) - 1)
    else:
        row_px = STACKED_LANE_ROW if stacked else LANE_ROW
        lane_rows = max(lane_rows, math.ceil((KEY_FIRST + KEY_LINE * len(key_lines) - 8) / row_px))
    height = chart_height(timeline, width, lane_rows, stacked, extra)
    fig = Figure(figsize=(width / LOGICAL_DPI, height / LOGICAL_DPI), dpi=dpi)
    FigureCanvasAgg(fig)
    fig.patch.set_facecolor(colors["paper"])
    hatch = token_rgba("hatch", theme)

    def axes_at(top_px: float, size_px: float):
        ax = fig.add_axes((left_px / width, 1 - (top_px + size_px) / height, plot_px / width, size_px / height))
        ax.set_facecolor(colors["paper"])
        for side in ax.spines.values():
            side.set_visible(False)
        ax.set_xlim(low, high)
        ax.set_axisbelow(True)
        ax.tick_params(length=0, labelsize=px_to_pt(11), labelcolor=colors["ink_3"], pad=6)
        ax.xaxis.set_major_locator(MaxNLocator(nbins=8, steps=[1, 2, 5, 10], integer=True))
        ax.grid(True, axis="x", color=colors["grid"], lw=0.8)
        return ax

    def put(x_px: float, y_px: float, text: str, size: float, color: str, weight: int = 400, ha: str = "left",
            va: str = "top"):
        return fig.text(x_px / width, 1 - y_px / height, text, fontsize=px_to_pt(size), color=color,
                        fontfamily=family, fontweight=weight, ha=ha, va=va)

    track_h = COMPACT_TRACK_HEIGHT if compact else TRACK_HEIGHT
    top: float = TOP_PAD
    axes = []
    for track in timeline.tracks:
        head_px = COMPACT_HEAD if compact else 0
        ax = axes_at(top + head_px, track_h)
        ax.set_label(track.subject)
        ax.tick_params(axis="x", labelbottom=False)
        ax.grid(True, axis="y", color=colors["grid"], lw=0.8)
        ax.yaxis.set_major_locator(MaxNLocator(nbins=3, steps=[1, 2, 2.5, 5, 10]))
        found = track.present()
        low_y, high_y = y_range(track)
        ax.set_ylim(low_y, high_y)
        trace = colors[track.token]
        # A line joins only neighbouring minutes that both have a value; every other reading is a dot.
        for first, last in _runs(list(found)):
            minutes = list(range(first, last + 1))
            if len(minutes) > 1:
                ax.plot(minutes, [found[m] for m in minutes], color=trace, lw=1.5, solid_joinstyle="round", zorder=3)
        lone = [m for m in found if m - 1 not in found and m + 1 not in found]
        ax.plot(lone, [found[m] for m in lone], linestyle="none", marker="o", markersize=4.5, color=trace, zorder=3)
        label_w = measure.width(_MISSING_WORD, 11, 600)
        for first, last in _runs(list(track.missing())):
            start, end = first - 0.5, last + 0.5
            # The box grows to its own label's width when that covers no reading; otherwise it keeps its true
            # extent, so a hatch never lies over a value that was recorded.
            need = (label_w + 8) / per_minute
            if end - start < need:
                wide = ((start + end) / 2 - need / 2, (start + end) / 2 + need / 2)
                if not any(wide[0] < minute < wide[1] for minute in found):
                    start, end = wide
            ax.add_patch(Rectangle((start, low_y), end - start, high_y - low_y, facecolor="none", lw=0, zorder=1,
                                   edgecolor=hatch, hatch="////"))
            ax.text((start + end) / 2, low_y + (high_y - low_y) * 0.5, _MISSING_WORD, fontsize=px_to_pt(11),
                    fontfamily=family, fontweight=600, color=colors["ink_2"], ha="center", va="center", zorder=2,
                    bbox={"facecolor": colors["paper"], "edgecolor": "none", "pad": 1.5})
        swatch_y = top + (COMPACT_HEAD / 2 if compact else 12)
        swatch_x = (COMPACT_LEFT - 30) if compact else 4
        fig.add_artist(Line2D([swatch_x / width, (swatch_x + 12) / width], [1 - swatch_y / height] * 2,
                              transform=fig.transFigure, color=trace, lw=2.2, solid_capstyle="butt"))
        name_x = swatch_x + 18
        name = measure.width(track.label, 12, 600)
        put(name_x, top + 4, track.label, 12, colors["ink"], 600)
        if track.unit:
            put(name_x + name + 5, top + 5, track.unit, 11, colors["ink_3"])
        axes.append(ax)
        top += head_px + track_h + TRACK_GAP

    lane_top = top - (TRACK_GAP if timeline.tracks else 0) + LANE_PAD
    if compact:
        put(4, lane_top + 2, "Drugs", 12, colors["ink"], 600)
        put(4 + title_width + 8, lane_top + 3, key_lines[0], 11, colors["ink_3"])
        for index, line in enumerate(key_lines[1:]):
            put(4, lane_top + 17 + KEY_LINE * index, line, 11, colors["ink_3"])
        lane_top += LANE_HEAD + KEY_LINE * (len(key_lines) - 1)
    row_px = STACKED_LANE_ROW if stacked else LANE_ROW
    lane = axes_at(lane_top, lane_rows * row_px)
    lane.set_label("doses")
    lane.set_yticks([])
    lane.set_ylim(lane_rows, 0)
    lane.spines["bottom"].set_visible(True)
    lane.spines["bottom"].set_color(colors["rule_strong"])
    lane.tick_params(axis="x", labelsize=px_to_pt(11), pad=8)
    if not compact:
        put(4, lane_top + 2, "Drugs", 12, colors["ink"], 600)
        for index, line in enumerate(key_lines):
            put(4, lane_top + KEY_FIRST + KEY_LINE * index, line, 11, colors["ink_3"])
    # The axis caption keeps its place under the lane; the note about doses left out takes the last line.
    caption_y = height - (NOTE_HEIGHT if left_out else 0) - (AXIS_HEIGHT - 16 if not compact else 8)
    if left_out:
        put(left_px if not compact else 4, height - 4, left_out_text(left_out), 11, colors["ink_2"], va="bottom")
    if compact:
        put(width - right_px, caption_y, timeline.axis_label, 11, colors["ink_3"], ha="right", va="bottom")
    else:
        put(LABEL_WIDTH - 8, caption_y, timeline.axis_label, 11, colors["ink_3"], ha="right", va="center")
    if not placed:
        said = ("Drug times are clock times, not placed on this axis" if timeline.off_axis
                else "No drug doses on this axis")
        lane.text(0.5, 0.5, said, transform=lane.transAxes, color=colors["ink_2"], ha="center", va="center",
                  fontsize=px_to_pt(12), fontfamily=family)
    for item in placed:
        if item.row == LEFT_OUT:
            continue
        dose = item.dose
        # Stacked, the label is the row's upper line and the mark its lower; beside, both share the row's middle.
        mark_y = item.row + (0.74 if stacked else 0.5)
        half = 0.17 if stacked else 0.34
        near: float
        far: float
        if dose.infusion:
            length = max(dose.end - dose.start, 2 * _MARK_HALF / per_minute)
            corner = (dose.start, mark_y - half)
            lane.add_patch(Rectangle(corner, length, 2 * half, facecolor="none", lw=0, zorder=1, edgecolor=hatch,
                                     hatch="////"))
            lane.add_patch(Rectangle(corner, length, 2 * half, fill=False, edgecolor=colors["ink_2"], lw=0.9,
                                     zorder=2))
            near, far = dose.start, dose.start + length
        elif dose.reversed_times:
            # Neither a bolus nor an infusion: the record's end is before its start, and the page does not pick one.
            lane.plot([dose.start], [mark_y], linestyle="none", marker="D", markersize=7, markerfacecolor=colors["paper"],
                      markeredgecolor=colors["ink"], markeredgewidth=1.6, zorder=4)
            near, far = dose.start - _MARK_HALF / per_minute, dose.start + _MARK_HALF / per_minute
        else:
            lane.plot([dose.start], [mark_y], linestyle="none", marker="s", markersize=7, color=colors["ink"],
                      zorder=4)
            near, far = dose.start - _MARK_HALF / per_minute, dose.start + _MARK_HALF / per_minute
        gap = _LABEL_GAP / per_minute
        size = px_to_pt(11 if stacked else 12)
        if stacked:
            lane.text(low + item.label_x / per_minute, item.row + 0.27, dose.label, fontsize=size,
                      fontfamily=family, fontweight=600, color=colors["ink"], ha="left", va="center", zorder=5)
        elif item.label_left:
            lane.text(near - gap, mark_y, dose.label, fontsize=size, fontfamily=family, fontweight=600,
                      color=colors["ink"], ha="right", va="center", zorder=5)
        else:
            lane.text(far + gap, mark_y, dose.label, fontsize=size, fontfamily=family, fontweight=600,
                      color=colors["ink"], ha="left", va="center", zorder=5)

    boxes = tuple(
        QRectF(box.x0 * width, (1 - box.y1) * height, box.width * width, box.height * height)
        for ax in [*axes, lane] for box in [ax.get_position()]
    )
    return fig, boxes, (low, high), left_out


class MedicationChart(QWidget):
    """The timeline picture with a cursor you can move; the picture is kept while the cursor moves.

    Left and Right step to the neighbouring minute that has a row, Home and End go to the first and the last, and a
    click puts the cursor on the nearest such minute. The accessible description carries the chart's numbers in
    words and, after them, what the cursor stands on.
    """

    cursor_changed = Signal(object)  # the minute (int) or None

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("medicationChart")
        self.setAccessibleName("Drugs and measurements chart")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(0)
        self._timeline: Timeline | None = None
        self._cursor: int | None = None
        self._pixmap = QPixmap()
        self._xlim = (0.0, 1.0)
        self._axes: tuple[QRectF, ...] = ()
        self._compact = False
        self._cache: dict[tuple[int, int, float, str], RenderedTimeline] = {}
        self._serial = 0
        self._base_description = ""
        self._left_out = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self.redraw)
        self.setFixedHeight(120)

    def set_timeline(self, timeline: Timeline | None, cursor: int | None = None) -> None:
        self._timeline = timeline
        self._serial += 1
        self._cache.clear()
        self._left_out = 0
        self._base_description = describe(timeline) if timeline is not None else ""
        minutes = timeline.cursor_minutes() if timeline is not None else ()
        self._cursor = cursor if cursor in minutes else (minutes[0] if minutes else None)
        self._describe()
        self.redraw()

    def timeline(self) -> Timeline | None:
        return self._timeline

    def cursor_minute(self) -> int | None:
        return self._cursor

    def set_cursor(self, minute: int | None, *, emit: bool = True) -> None:
        """Move the cursor to ``minute`` when the record has a row there; anything else clears nothing."""
        if self._timeline is None or minute not in self._timeline.cursor_minutes() or minute == self._cursor:
            return
        self._cursor = minute
        self._describe()
        self.update()
        if emit:
            self.cursor_changed.emit(minute)

    def step(self, direction: int) -> None:
        minutes = self._timeline.cursor_minutes() if self._timeline is not None else ()
        if not minutes or self._cursor is None:
            return
        index = max(0, min(len(minutes) - 1, minutes.index(self._cursor) + direction))
        self.set_cursor(minutes[index])

    def _describe(self) -> None:
        text = self._base_description
        if self._left_out:
            text = f"{text} {left_out_text(self._left_out)}"
        if self._timeline is not None and self._cursor is not None:
            text = f"{text} {cursor_text(self._timeline, self._cursor)}"
        self.setAccessibleDescription(text)

    # —— geometry ——

    def cursor_x(self) -> float | None:
        """The cursor's x in widget pixels."""
        if self._cursor is None or not self._axes:
            return None
        low, high = self._xlim
        first = self._axes[0]
        return first.left() + (self._cursor - low) * first.width() / (high - low)

    def minute_at(self, x: float) -> int | None:
        """The cursor minute nearest to widget pixel ``x``."""
        if self._timeline is None or not self._axes:
            return None
        low, high = self._xlim
        first = self._axes[0]
        raw = low + (x - first.left()) * (high - low) / first.width()
        minutes = self._timeline.cursor_minutes()
        return min(minutes, key=lambda minute: abs(minute - raw)) if minutes else None

    def redraw(self) -> None:
        """Draw at the current width now; a resize redraws after a short delay."""
        self._timer.stop()
        if self._timeline is None or self.width() <= 0:
            return
        theme, dpr = active_theme(), self.devicePixelRatioF()
        key = (self._serial, self.width(), dpr, theme)
        if key not in self._cache:
            if len(self._cache) > 8:
                self._cache.clear()
            self._cache[key] = render_timeline(self._timeline, self.width(), theme, dpr)
        rendered = self._cache[key]
        pixmap = QPixmap()
        pixmap.loadFromData(rendered.png)
        pixmap.setDevicePixelRatio(dpr)
        self._pixmap, self._xlim, self._axes = pixmap, rendered.xlim, rendered.axes
        if rendered.left_out != self._left_out:
            self._left_out = rendered.left_out
            self._describe()
        self._compact = is_compact(self.width())
        if self.height() != rendered.height:
            self.setFixedHeight(rendered.height)
        self.update()

    def refresh(self) -> None:
        """Draw again in the active theme, never from a picture made under the other one."""
        self._cache.clear()
        self.redraw()

    # —— events ——

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._pixmap.isNull():
            self.redraw()
        else:
            self._timer.start()

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.StyleChange) and self._timeline is not None:
            self.refresh()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        if key == Qt.Key.Key_Left:
            self.step(-1)
        elif key == Qt.Key.Key_Right:
            self.step(1)
        elif key in (Qt.Key.Key_Home, Qt.Key.Key_End) and self._timeline is not None:
            minutes = self._timeline.cursor_minutes()
            if minutes:
                self.set_cursor(minutes[0] if key == Qt.Key.Key_Home else minutes[-1])
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        minute = self.minute_at(event.position().x())
        if minute is not None:
            self.set_cursor(minute)
            self.setFocus(Qt.FocusReason.MouseFocusReason)
        super().mousePressEvent(event)

    # —— painting ——

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), token_color("paper"))
        if self._pixmap.isNull():
            return
        painter.drawPixmap(QPointF(0, 0), self._pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._timeline is not None and self._cursor is not None:
            self._paint_cursor(painter, self._timeline, self._cursor)
        if self.hasFocus():
            painter.setPen(QPen(token_color("accent"), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self.rect().adjusted(1, 1, -2, -2))

    def _font(self, size: int, weight: QFont.Weight) -> QFont:
        font = tabular(QFont(self.font()))
        font.setPixelSize(size)
        font.setWeight(weight)
        return font

    def _paint_cursor(self, painter: QPainter, timeline: Timeline, minute: int) -> None:
        x = self.cursor_x()
        if x is None:
            return
        accent = token_color("accent")
        first, lane = self._axes[0], self._axes[-1]
        painter.setPen(QPen(accent, 1.5))
        painter.drawLine(QPointF(x, first.top() - 2), QPointF(x, lane.bottom()))
        low_x = self._axes[0].left()
        for track, box in zip(timeline.tracks, self._axes, strict=False):
            state, value = value_at(track, minute)
            if value is not None:
                low_y, high_y = y_range(track)
                y = box.bottom() - (value - low_y) / (high_y - low_y) * box.height()
                painter.setPen(QPen(accent, 1.8))
                painter.setBrush(token_color("paper"))
                painter.drawEllipse(QPointF(x, y), 4.5, 4.5)
            self._paint_value(painter, track, box, state, value, low_x)
        self._paint_tag(painter, lane, x, f"{minute} min")

    def _paint_value(self, painter: QPainter, track: Track, box: QRectF, state: str, value: float | None,
                     plot_left: float) -> None:
        """The track's value at the cursor: beside its name when wide, at the right end of its name line when not."""
        text = _number(value) if value is not None else ("missing" if state == "missing" else "no row")
        quiet = value is None
        font = self._font(13 if self._compact or quiet else 20, QFont.Weight.Normal if quiet else QFont.Weight.DemiBold)
        font.setItalic(quiet)
        painter.setFont(font)
        painter.setPen(token_color("ink_3" if quiet else "ink"))
        if self._compact:
            rect = QRectF(plot_left, box.top() - COMPACT_HEAD, box.width(), COMPACT_HEAD)
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), text)
        else:
            rect = QRectF(22, box.top() + 20, LABEL_WIDTH - 60, 30)
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop), text)

    def _paint_tag(self, painter: QPainter, lane: QRectF, x: float, text: str) -> None:
        painter.setFont(self._font(12, QFont.Weight.DemiBold))
        width = painter.fontMetrics().horizontalAdvance(text) + 18
        tag = QRectF(0, lane.bottom() + 4, width, 20)
        tag.moveLeft(min(max(x - width / 2, 0.0), self.width() - width))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(token_color("accent"))
        painter.drawRoundedRect(tag, 3, 3)
        painter.setPen(token_color("on_accent"))
        painter.drawText(tag, int(Qt.AlignmentFlag.AlignCenter), text)
