"""The Handoff page's timeline: its numbers in words, and a token-coloured chart of them.

Three tracks (arterial MAP, noninvasive MAP, SpO2) share a minute axis with a lane of drug doses under
them. A track with no usable reading is hatched and says "no samples"; a trace breaks where a minute
has no reading. Everything drawn comes from the authored case, nothing from a data folder.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import matplotlib
import pandas as pd
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle
from matplotlib.ticker import MaxNLocator
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

from src.desktop.charts import LOGICAL_DPI, chart_family, gap_spans, px_to_pt, token_rgba
from src.desktop.theme import active_theme, token_color, tokens
from src.modules.handoff_trainer.grading import MAP_RANGE, SPO2_RANGE, Dose, PracticeCase, classify_dose, format_number

# Layout in logical pixels, as the case page's chart does it.
LABEL_WIDTH = 176  # track names, units and the y-axis numbers
RIGHT_PAD = 16
MIN_PLOT = 40
MIN_WIDTH = LABEL_WIDTH + RIGHT_PAD + MIN_PLOT  # the narrowest picture that still has a plot
TOP_PAD = 26  # room for the highlight's tag
TRACK_GAP = 14
BASE_TRACK = 84
LANE_ROW = 26
LANE_PAD = 12
AXIS_HEIGHT = 36
MAX_LANE_ROWS = 6
_MARK_HALF = 6
_LABEL_GAP = 6
_ROW_GAP = 8

MinuteSpan = tuple[int, int]


@dataclass(frozen=True)
class Track:
    key: str  # the Sample field
    label: str
    unit: str
    token: str
    usable: tuple[float, float]  # the grader's range; a reading outside it is not drawn
    limits: tuple[float, float]  # the least y range, so runs of one scenario compare by eye
    weight: float


TRACKS: tuple[Track, ...] = (
    Track("map_art", "Arterial MAP", "mmHg", "s_pip", MAP_RANGE, (50.0, 90.0), 1.5),
    Track("nmap", "Noninvasive MAP", "mmHg", "s_map", MAP_RANGE, (50.0, 90.0), 1.0),
    Track("spo2", "SpO₂", "%", "s_spo2", SPO2_RANGE, (85.0, 100.0), 1.0),
)


def is_unusable(track: Track, value: float | None) -> bool:
    """A reading outside the sensor's range, which the grader ignores and the chart does not draw."""
    return value is not None and not track.usable[0] <= value <= track.usable[1]


def readings(case: PracticeCase, track: Track) -> dict[int, float]:
    """The usable readings of one track by minute; a minute with none is absent."""
    found: dict[int, float] = {}
    for sample in sorted(case.samples, key=lambda item: item.minute):
        value = getattr(sample, track.key)
        if value is not None and not is_unusable(track, value):
            found[sample.minute] = float(value)
    return found


def unusable_count(case: PracticeCase) -> int:
    """Readings the grader ignores for being outside the sensor's range; the chart does not draw them."""
    return sum(1 for track in TRACKS for sample in case.samples if is_unusable(track, getattr(sample, track.key)))


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def dose_amount(dose: Dose) -> str:
    return "dose not recorded" if dose.amount is None else f"{format_number(dose.amount)} {dose.unit}".strip()


def dose_label(dose: Dose) -> str:
    """Name and amount, as the events lane writes it."""
    return f"{dose.drug} {dose_amount(dose)}"


def dose_is_infusion(dose: Dose) -> bool:
    return classify_dose(dose) == "infusion"


def dose_span(dose: Dose) -> MinuteSpan:
    end = dose.end_minute if dose_is_infusion(dose) and dose.end_minute is not None else dose.minute
    return dose.minute, end


def dose_line(dose: Dose) -> str:
    """One dose in words, for the description and the table: name, amount, when, and bolus or infusion."""
    kind = classify_dose(dose)
    start, end = dose_span(dose)
    when = f"from minute {start} to minute {end}" if kind == "infusion" else f"at minute {dose.minute}"
    return f"{dose_label(dose)} {when} ({kind})"


def minute_range(case: PracticeCase) -> tuple[int, int] | None:
    minutes = [sample.minute for sample in case.samples]
    for dose in case.doses:
        minutes.extend(dose_span(dose))
    return (min(minutes), max(minutes)) if minutes else None


def minutes_text(span: MinuteSpan) -> str:
    start, end = span
    return f"minute {start}" if start == end else f"minutes {start} to {end}"


def chart_description(case: PracticeCase) -> str:
    """What the chart shows, in words and numbers, without naming a finding: finding them is the exercise."""
    span = minute_range(case)
    parts = [f"Invented timeline, minutes {span[0]} to {span[1]}." if span else "Invented timeline with no rows."]
    for track in TRACKS:
        found = readings(case, track)
        if not found:
            parts.append(f"{track.label}: no samples.")
            continue
        first, last = min(found), max(found)
        silent = (last - first + 1) - len(found)
        text = (f"{track.label} ({track.unit}): {_plural(len(found), 'reading', 'readings')} from minute {first} "
                f"to minute {last}, lowest {format_number(min(found.values()))}, "
                f"highest {format_number(max(found.values()))}")
        if silent:
            verb = "has" if silent == 1 else "have"
            parts.append(f"{text}, {_plural(silent, 'minute', 'minutes')} between them {verb} no reading.")
        else:
            parts.append(text + ".")
    ignored = unusable_count(case)
    if ignored:
        verb = "is" if ignored == 1 else "are"
        parts.append(f"{_plural(ignored, 'reading', 'readings')} outside the usable range {verb} not drawn.")
    if case.doses:
        parts.append("Drug doses: " + "; ".join(dose_line(dose) for dose in case.doses) + ".")
    else:
        parts.append("No drug doses.")
    parts.append("The table view lists the same timeline minute by minute.")
    return " ".join(parts)


@dataclass(frozen=True)
class _Frame:
    low: float
    high: float
    plot_px: float

    @property
    def per_minute(self) -> float:
        return self.plot_px / (self.high - self.low)

    def x_px(self, minute: float) -> float:
        return (minute - self.low) * self.per_minute


def _frame(case: PracticeCase, width_px: float) -> _Frame:
    span = minute_range(case)
    low, high = (span[0] - 0.5, span[1] + 0.5) if span else (-0.5, 9.5)
    return _Frame(low, high, max(width_px - LABEL_WIDTH - RIGHT_PAD, float(MIN_PLOT)))


@dataclass(frozen=True)
class _Placed:
    dose: Dose
    label: str
    start: int
    end: int
    infusion: bool
    row: int
    label_left: bool


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


def _place_doses(case: PracticeCase, frame: _Frame, measure: _Measure) -> list[_Placed]:
    """Give each dose a lane row so labels never overlap; a label that would leave the plot goes to the left."""
    taken: list[list[tuple[float, float]]] = []
    placed: list[_Placed] = []
    for dose in sorted(case.doses, key=lambda item: (item.minute, item.drug)):
        start, end = dose_span(dose)
        label = dose_label(dose)
        text = measure.width(label, 12, 600)
        left, right = frame.x_px(start), frame.x_px(end)
        if dose_is_infusion(dose):
            left, right = left, max(right, left + 2 * _MARK_HALF)
        else:
            left, right = left - _MARK_HALF, left + _MARK_HALF
        label_left = right + _LABEL_GAP + text > frame.plot_px and left - _LABEL_GAP - text >= 0
        extent = (left - _LABEL_GAP - text, right) if label_left else (left, right + _LABEL_GAP + text)
        row = next((index for index, spans in enumerate(taken)
                    if all(extent[0] >= end_ + _ROW_GAP or extent[1] <= start_ - _ROW_GAP for start_, end_ in spans)),
                   None)
        if row is None:
            row = len(taken) if len(taken) < MAX_LANE_ROWS else MAX_LANE_ROWS - 1
            if row == len(taken):
                taken.append([])
        taken[row].append(extent)
        placed.append(_Placed(dose, label, start, end, dose_is_infusion(dose), row, label_left))
    return placed


def chart_height(lane_rows: int) -> int:
    tracks = sum(round(BASE_TRACK * track.weight) for track in TRACKS)
    return TOP_PAD + tracks + (len(TRACKS) - 1) * TRACK_GAP + LANE_PAD + max(lane_rows, 1) * LANE_ROW + AXIS_HEIGHT


@dataclass(frozen=True)
class RenderedTimeline:
    png: bytes
    width: int
    height: int
    xlim: tuple[float, float]
    axes: tuple[QRectF, ...]  # the three tracks, then the doses lane, in widget pixels


def build_timeline_figure(
    case: PracticeCase, width_px: int, theme: str | None, dpr: float = 1.0
) -> tuple[Figure, tuple[QRectF, ...], tuple[float, float]]:
    """The figure for ``case`` at ``width_px`` logical pixels, the axes' boxes in widget pixels and the x limits.

    The height follows from the doses lane's rows. Nothing is drawn to a file here.
    """
    with matplotlib.rc_context({"font.family": chart_family(), "hatch.linewidth": 0.8}):
        return _build(case, width_px, theme, dpr)


def render_timeline(case: PracticeCase, width_px: int, theme: str | None, dpr: float = 1.0) -> RenderedTimeline:
    """The timeline as a PNG, with the geometry the widget paints its highlight from."""
    with matplotlib.rc_context({"font.family": chart_family(), "hatch.linewidth": 0.8}):
        fig, boxes, xlim = _build(case, width_px, theme, dpr)
        buffer = io.BytesIO()
        FigureCanvasAgg(fig).print_png(buffer)
    return RenderedTimeline(buffer.getvalue(), width_px, round(fig.get_figheight() * LOGICAL_DPI), xlim, boxes)


def _build(
    case: PracticeCase, width: int, theme: str | None, dpr: float
) -> tuple[Figure, tuple[QRectF, ...], tuple[float, float]]:
    colors = tokens(theme)
    family = chart_family()
    dpi = LOGICAL_DPI * dpr
    frame = _frame(case, width)
    measure = _Measure(dpi, family)
    placed = _place_doses(case, frame, measure)
    lane_rows = max((item.row for item in placed), default=0) + 1
    height = chart_height(lane_rows)
    fig = Figure(figsize=(width / LOGICAL_DPI, height / LOGICAL_DPI), dpi=dpi)
    FigureCanvasAgg(fig)
    fig.patch.set_facecolor(colors["paper"])
    hatch = token_rgba("hatch", theme)
    left = LABEL_WIDTH / width

    def axes_at(top_px: float, size_px: float):
        ax = fig.add_axes((left, 1 - (top_px + size_px) / height, frame.plot_px / width, size_px / height))
        ax.set_facecolor(colors["paper"])
        for side in ax.spines.values():
            side.set_visible(False)
        ax.set_xlim(frame.low, frame.high)
        ax.set_axisbelow(True)
        ax.tick_params(length=0, labelsize=px_to_pt(11), labelcolor=colors["ink_3"], pad=6)
        ax.xaxis.set_major_locator(MaxNLocator(nbins=8, steps=[1, 2, 5, 10], integer=True))
        ax.grid(True, axis="x", color=colors["grid"], lw=0.8)
        return ax

    def put(x_px: float, y_px: float, text: str, size: float, color: str, weight: int = 400, ha: str = "left",
            va: str = "top"):
        return fig.text(x_px / width, 1 - y_px / height, text, fontsize=px_to_pt(size), color=color,
                        fontfamily=family, fontweight=weight, ha=ha, va=va)

    track_heights = [round(BASE_TRACK * track.weight) for track in TRACKS]
    top: float = TOP_PAD
    axes = []
    for track, size in zip(TRACKS, track_heights, strict=True):
        ax = axes_at(top, size)
        ax.set_label(track.key)
        ax.tick_params(axis="x", labelbottom=False)
        ax.grid(True, axis="y", color=colors["grid"], lw=0.8)
        ax.yaxis.set_major_locator(MaxNLocator(nbins=3, steps=[1, 2, 2.5, 5, 10]))
        found = readings(case, track)
        low_y, high_y = track.limits
        if found:
            lowest, highest = min(found.values()), max(found.values())
            pad = max((highest - lowest) * 0.1, 1.0)
            low_y, high_y = min(low_y, lowest - pad), max(high_y, highest + pad)
        ax.set_ylim(low_y, high_y)
        trace = colors[track.token]
        if found:
            grid = list(range(min(found), max(found) + 1))
            series = pd.Series([found.get(minute, float("nan")) for minute in grid], index=grid)
            ax.plot(grid, series, color=trace, lw=1.5, solid_joinstyle="round", zorder=3)
            lone = [minute for minute in found if minute - 1 not in found and minute + 1 not in found]
            ax.plot(lone, [found[minute] for minute in lone], linestyle="none", marker="o", markersize=4.5,
                    color=trace, zorder=3)
            hatched = gap_spans(series)
        else:
            hatched = [(frame.low, frame.high)]
        for start, end in hatched:
            ax.add_patch(Rectangle((start, low_y), end - start, high_y - low_y, facecolor="none", lw=0, zorder=1,
                                   edgecolor=hatch, hatch="////"))
            if (end - start) * frame.per_minute >= measure.width("no samples", 11, 600) + 8:
                ax.text((start + end) / 2, low_y + (high_y - low_y) * 0.5, "no samples", fontsize=px_to_pt(11),
                        fontfamily=family, fontweight=600, color=colors["ink_2"], ha="center", va="center", zorder=5,
                        bbox={"facecolor": colors["paper"], "edgecolor": "none", "pad": 1.5})
        fig.add_artist(Line2D([4 / width, 16 / width], [1 - (top + 12) / height] * 2, transform=fig.transFigure,
                              color=trace, lw=2.2, solid_capstyle="butt"))
        name = measure.width(track.label, 12, 600)
        put(22, top + 4, track.label, 12, colors["ink"], 600)
        if 22 + name + 5 + measure.width(track.unit, 11, 400) <= LABEL_WIDTH - 36:
            put(22 + name + 5, top + 5, track.unit, 11, colors["ink_3"])
        else:
            put(22, top + 21, track.unit, 11, colors["ink_3"])
        axes.append(ax)
        top += size + TRACK_GAP

    lane_top = top - TRACK_GAP + LANE_PAD
    lane = axes_at(lane_top, lane_rows * LANE_ROW)
    lane.set_label("doses")
    lane.set_yticks([])
    lane.set_ylim(lane_rows, 0)
    lane.spines["bottom"].set_visible(True)
    lane.spines["bottom"].set_color(colors["rule_strong"])
    lane.tick_params(axis="x", labelsize=px_to_pt(11), pad=8)
    put(4, lane_top + 2, "Drug doses", 12, colors["ink"], 600)
    put(4, lane_top + 19, "mark = bolus, bar = infusion", 11, colors["ink_3"])
    put(LABEL_WIDTH - 8, height - AXIS_HEIGHT + 16, "invented minute", 11, colors["ink_3"], ha="right", va="center")
    if not placed:
        lane.text(0.5, 0.5, "No drug doses on this timeline", transform=lane.transAxes, color=colors["ink_2"],
                  ha="center", va="center", fontsize=px_to_pt(12), fontfamily=family)
    for item in placed:
        middle = item.row + 0.5
        near: float
        far: float
        if item.infusion:
            length = max(item.end - item.start, 2 * _MARK_HALF / frame.per_minute)
            corner = (item.start, middle - 0.34)
            lane.add_patch(Rectangle(corner, length, 0.68, facecolor="none", lw=0, zorder=1, edgecolor=hatch,
                                     hatch="////"))
            lane.add_patch(Rectangle(corner, length, 0.68, fill=False, edgecolor=colors["ink_2"], lw=0.9, zorder=2))
            near, far = item.start, item.start + length
        else:
            lane.plot([item.start], [middle], linestyle="none", marker="s", markersize=7, color=colors["ink"],
                      zorder=4)
            near, far = item.start - _MARK_HALF / frame.per_minute, item.start + _MARK_HALF / frame.per_minute
        gap = _LABEL_GAP / frame.per_minute
        if item.label_left:
            lane.text(near - gap, middle, item.label, fontsize=px_to_pt(12), fontfamily=family, fontweight=600,
                      color=colors["ink"], ha="right", va="center", zorder=5)
        else:
            lane.text(far + gap, middle, item.label, fontsize=px_to_pt(12), fontfamily=family, fontweight=600,
                      color=colors["ink"], ha="left", va="center", zorder=5)

    boxes = tuple(
        QRectF(box.x0 * width, (1 - box.y1) * height, box.width * width, box.height * height)
        for ax in [*axes, lane] for box in [ax.get_position()]
    )
    return fig, boxes, (frame.low, frame.high)


class TimelineChart(QWidget):
    """The timeline drawn from the tokens, with a painted highlight over minutes the page asks to show.

    The picture is kept while the highlight moves; only the highlight is repainted. The accessible
    description says what the chart shows in words and numbers.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("handoffChart")
        self.setAccessibleName("Invented timeline chart")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(MIN_WIDTH)
        self._case: PracticeCase | None = None
        self._pixmap = QPixmap()
        self._xlim = (0.0, 1.0)
        self._axes: tuple[QRectF, ...] = ()
        self._highlight: MinuteSpan | None = None
        self._cache: dict[tuple[str, int, float, str], RenderedTimeline] = {}
        self._base_description = ""
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self.redraw)
        self.setFixedHeight(chart_height(1))

    def set_case(self, case: PracticeCase) -> None:
        self._case = case
        self._highlight = None
        self._base_description = chart_description(case)
        self._describe()
        self.redraw()

    def set_highlight(self, span: MinuteSpan | None) -> None:
        """Mark minutes on the chart: a line for one minute, a band for a run. ``None`` clears it."""
        self._highlight = span
        self._describe()
        self.update()

    def highlight(self) -> MinuteSpan | None:
        return self._highlight

    def highlight_rect(self) -> QRectF | None:
        """The highlight's band in widget pixels, from the first track's top to the lane's bottom."""
        if self._highlight is None or not self._axes:
            return None
        low, high = self._xlim
        first, lane = self._axes[0], self._axes[-1]
        start, end = self._highlight
        scale = first.width() / (high - low)
        left = first.left() + (start - 0.5 - low) * scale
        right = first.left() + (end + 0.5 - low) * scale
        return QRectF(QPointF(left, first.top() - 4), QPointF(right, lane.bottom()))

    def redraw(self) -> None:
        """Draw at the current width now; a resize redraws after a short delay."""
        self._timer.stop()
        if self._case is None or self.width() <= 0:
            return
        theme, dpr = active_theme(), self.devicePixelRatioF()
        key = (self._case.case_id, self.width(), dpr, theme)
        if key not in self._cache:
            if len(self._cache) > 8:
                self._cache.clear()
            self._cache[key] = render_timeline(self._case, self.width(), theme, dpr)
        rendered = self._cache[key]
        pixmap = QPixmap()
        pixmap.loadFromData(rendered.png)
        pixmap.setDevicePixelRatio(dpr)
        self._pixmap, self._xlim, self._axes = pixmap, rendered.xlim, rendered.axes
        if self.height() != rendered.height:
            self.setFixedHeight(rendered.height)
        self.update()

    def refresh(self) -> None:
        """Draw again in the active theme, never from a picture made under the other one."""
        self._cache.clear()
        self.redraw()

    def _describe(self) -> None:
        shown = f" Highlighted: {minutes_text(self._highlight)}." if self._highlight else ""
        self.setAccessibleDescription(self._base_description + shown)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._pixmap.isNull():
            self.redraw()
        else:
            self._timer.start()

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.StyleChange) and self._case is not None:
            self.refresh()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), token_color("paper"))
        if self._pixmap.isNull():
            return
        painter.drawPixmap(QPointF(0, 0), self._pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        band = self.highlight_rect()
        if band is not None and self._highlight is not None:
            self._paint_highlight(painter, band)
        if self.hasFocus():
            painter.setPen(QPen(token_color("accent"), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self.rect().adjusted(1, 1, -2, -2))

    def _paint_highlight(self, painter: QPainter, band: QRectF) -> None:
        accent = token_color("accent")
        start, end = self._highlight or (0, 0)
        if start == end:
            x = (band.left() + band.right()) / 2
            painter.setPen(QPen(accent, 1.5))
            painter.drawLine(QPointF(x, band.top()), QPointF(x, band.bottom()))
        else:
            fill = token_color("accent")
            fill.setAlpha(24)
            painter.setPen(QPen(accent, 1.5))
            painter.setBrush(fill)
            painter.drawRect(band)
        text = minutes_text((start, end))
        font = QFont(self.font())
        font.setPixelSize(12)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        tag = QRectF(0, band.top() - 22, painter.fontMetrics().horizontalAdvance(text) + 18, 20)
        tag.moveLeft(min(max((band.left() + band.right()) / 2 - tag.width() / 2, 0.0), self.width() - tag.width()))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(accent)
        painter.drawRoundedRect(tag, 3, 3)
        painter.setPen(token_color("on_accent"))
        painter.drawText(tag, int(Qt.AlignmentFlag.AlignCenter), text)
