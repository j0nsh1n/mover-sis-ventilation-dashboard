"""Fast matplotlib charts for the desktop UI (Plotly-free)."""

from __future__ import annotations

import io
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, NamedTuple, cast

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.backends.backend_agg import FigureCanvasAgg  # noqa: E402
import pandas as pd  # noqa: E402
from PySide6.QtCore import QEvent, QPointF, Qt, QRectF, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from src.desktop.fonts import make_tabular
from src.desktop.theme import token_color


CHART_DPI = 120


class ChartView(QWidget):
    """PNG chart panel backed by matplotlib.

    Renders figures into a QLabel inside a QScrollArea so tall charts
    (e.g. multi-panel case timelines) can be scrolled with the mouse wheel.
    """

    resized = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("chartView")
        self._label = QLabel("No chart to display.")
        self._label.setObjectName("pathHint")
        self._label.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
        )
        self._label.setWordWrap(True)
        # Fixed size driven by pixmap so QScrollArea can scroll
        self._label.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        self._label.setScaledContents(False)

        self._scroll = QScrollArea()
        # False is required: True forces the label into the viewport and
        # kills scrollbars even when the pixmap is taller than the panel.
        self._scroll.setWidgetResizable(False)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.setWidget(self._label)
        self._scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._scroll.setWidgetResizable(False)
        # Smooth / natural wheel scrolling
        self._scroll.setFocusPolicy(Qt.FocusPolicy.WheelFocus)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(self._scroll)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.resized.emit()

    def figure_size(self, dpi: int = CHART_DPI) -> tuple[float, float]:
        """Inches that make a figure fill the visible area when rendered at ``dpi``."""
        viewport = self._scroll.viewport().size()
        return (max(viewport.width() - 16, 200) / dpi, max(viewport.height() - 8, 120) / dpi)

    def clear(self, message: str = "No chart to display.") -> None:
        self._label.setPixmap(QPixmap())
        self._label.setText(message)
        self._label.setWordWrap(True)
        # Let the empty-state text fill the viewport
        vp = self._scroll.viewport().size()
        w = max(vp.width(), 200)
        h = max(vp.height(), 120)
        self._label.setMinimumSize(0, 0)
        self._label.setMaximumSize(16777215, 16777215)
        self._label.resize(w, h)

    def set_figure(self, fig: Any | None, dpi: int = CHART_DPI) -> None:
        if fig is None:
            self.clear()
            return
        if not isinstance(fig, plt.Figure):
            self.clear("Unsupported chart type.")
            return
        buf = io.BytesIO()
        fig.savefig(
            buf,
            format="png",
            dpi=dpi,
            bbox_inches="tight",
            facecolor=fig.get_facecolor(),
            edgecolor="none",
        )
        plt.close(fig)
        buf.seek(0)
        pix = QPixmap()
        if not pix.loadFromData(buf.read()):
            self.clear("Could not render chart.")
            return

        # Fit width to the scroll viewport when possible; keep full height
        # so tall timelines scroll vertically instead of being clipped.
        avail_w = max(self._scroll.viewport().width() - 16, 400)
        if pix.width() > avail_w > 0:
            pix = pix.scaledToWidth(
                avail_w, Qt.TransformationMode.SmoothTransformation
            )

        self._label.setWordWrap(False)
        self._label.setText("")
        self._label.setPixmap(pix)
        # Explicit size = pixmap size → QScrollArea shows scrollbars
        self._label.setFixedSize(pix.size())
        self._scroll.ensureVisible(0, 0)


# Back-compat alias used by older imports
LegacyChartView = ChartView


def _style() -> dict[str, str]:
    from src.desktop.theme import chart_style, chart_style_from, tokens

    try:
        return chart_style()
    except Exception:
        return chart_style_from(tokens("light"))


def _style_axes(ax, style: dict[str, str]) -> None:
    ax.set_facecolor(style["axes_facecolor"])
    ax.tick_params(colors=style["muted"], labelsize=8)
    ax.xaxis.label.set_color(style["muted"])
    ax.yaxis.label.set_color(style["muted"])
    ax.title.set_color(style["text"])
    for spine in ax.spines.values():
        spine.set_color(style["spine"])
    ax.grid(True, alpha=0.35, color=style["grid"])


def _new_fig(figsize, style: dict[str, str], **kwargs):
    fig, ax = plt.subplots(figsize=figsize, **kwargs)
    fig.patch.set_facecolor(style["figure_facecolor"])
    if hasattr(ax, "__iter__"):
        for a in ax.ravel():
            _style_axes(a, style)
    else:
        _style_axes(ax, style)
    return fig, ax


def case_timeline_figure(
    ts: pd.DataFrame,
    flags: pd.DataFrame | None = None,
    show_vitals: bool = True,
) -> plt.Figure:
    style = _style()
    rows = 5 if show_vitals else 4
    fig, axes = plt.subplots(rows, 1, figsize=(10, 1.7 * rows), sharex=True)
    fig.patch.set_facecolor(style["figure_facecolor"])
    for ax in axes:
        _style_axes(ax, style)

    x = ts["t_min"] if "t_min" in ts.columns else range(len(ts))

    # Palette tuned for both themes
    c_tv = "#38bdf8"
    c_pip = "#fb923c"
    c_peep = "#4ade80"
    c_etco2 = "#22d3ee"
    c_agent = "#c084fc"
    c_agent_fi = "#a78bfa"
    c_mac = "#f472b6"
    c_hr = "#f87171"
    c_spo2 = "#60a5fa"
    c_map = "#e879f9"

    def line(ax, col, label, color, ls="-"):
        if col in ts.columns and ts[col].notna().any():
            ax.plot(x, ts[col], color=color, lw=1.25, label=label, ls=ls)

    line(axes[0], "TV", "TV (mL)", c_tv)
    axes[0].set_ylabel("TV", fontsize=8)
    _legend(axes[0], style)

    line(axes[1], "PIP", "PIP", c_pip)
    line(axes[1], "PEEP", "PEEP", c_peep)
    axes[1].set_ylabel("Press.", fontsize=8)
    _legend(axes[1], style)

    line(axes[2], "ETCO2", "ETCO₂", c_etco2)
    axes[2].axhspan(35, 45, color=c_etco2, alpha=0.08)
    axes[2].set_ylabel("ETCO₂", fontsize=8)
    _legend(axes[2], style)

    line(axes[3], "Agent_Et", "Agent Et%", c_agent)
    line(axes[3], "Agent_Fi", "Agent Fi%", c_agent_fi, ls="--")
    line(axes[3], "MAC_Et", "MAC Et", c_mac, ls=":")
    axes[3].set_ylabel("Agent", fontsize=8)
    _legend(axes[3], style)

    if show_vitals:
        line(axes[4], "HR", "HR", c_hr)
        line(axes[4], "SPO2", "SpO₂", c_spo2)
        line(axes[4], "nMAP", "nMAP", c_map)
        axes[4].set_ylabel("Vitals", fontsize=8)
        axes[4].set_xlabel("Minutes from case start", fontsize=9)
        _legend(axes[4], style)
    else:
        axes[3].set_xlabel("Minutes from case start", fontsize=9)

    if flags is not None and not flags.empty and "t_min" in flags.columns:
        times = flags["t_min"].dropna().unique()
        step = max(1, len(times) // 25)
        for t in times[::step]:
            for ax in axes:
                ax.axvline(t, color=style["critical"], alpha=0.22, lw=0.7)

    fig.suptitle("Case timeline", fontsize=11, color=style["text"], y=0.995)
    fig.tight_layout()
    return fig


def focused_minute_rows(ts: pd.DataFrame) -> pd.DataFrame:
    rows = ts.drop_duplicates("t_min", keep="last").sort_values("t_min").set_index("t_min")
    if rows.empty:
        return rows
    first = float(rows.index[0])
    # Seconds in the incision anchor can give equivalent minute steps different float tails.
    rows.index = (rows.index - first).round(9)
    minutes = pd.RangeIndex(int(round(float(rows.index[-1]))) + 1)
    rows = rows.reindex(rows.index.union(minutes)).sort_index()
    return rows.set_axis(rows.index + first).rename_axis("t_min")


# The focused chart's layout, in logical pixels (the figure's size in inches times LOGICAL_DPI).
LOGICAL_DPI = 110
LABEL_WIDTH = 148  # track name and unit, the value at the cursor, and the y-axis numbers
RIGHT_PAD = 14
TOP_PAD = 26  # room for the incision and closure words
TRACK_GAP = 14
MIN_TRACK = 88
LANE_ROW = 22
LANE_PAD = 10
AXIS_HEIGHT = 34
NAME_OFFSET = 4
VALUE_OFFSET = 24
MAX_LANE_ROWS = 6
BASE_TRACKS = ("PIP", "ETCO2", "HR")


class SignalInfo(NamedTuple):
    label: str
    unit: str
    token: str  # colour token of the trace


# Signals the case page names and colours; the three signal hues are data identity, any other track is ink_2.
_SIGNAL_INFO = {
    "PIP": SignalInfo("PIP", "cmH₂O", "s_pip"),
    "ETCO2": SignalInfo("ETCO₂", "mmHg", "s_etco2"),
    "HR": SignalInfo("Heart rate", "bpm", "s_hr"),
    "SPO2": SignalInfo("SpO₂", "%", "s_spo2"),
    "nMAP": SignalInfo("NIBP MAP", "mmHg", "s_map"),
    "TV": SignalInfo("TV", "mL", "ink_2"),
    "PEEP": SignalInfo("PEEP", "cmH₂O", "ink_2"),
    "RR": SignalInfo("RR", "/min", "ink_2"),
    "FIO2": SignalInfo("FiO₂", "%", "ink_2"),
    "TV_mlkg": SignalInfo("TV per IBW", "mL/kg", "ink_2"),
}


def signal_info(column: str) -> SignalInfo:
    """Name, unit and colour token for a timeseries column."""
    return _SIGNAL_INFO.get(column, SignalInfo(column, "", "ink_2"))


_family: str | None = None


def chart_family() -> str:
    """Inter for the chart's words: matplotlib does not see Qt's bundled fonts, so register the files once."""
    global _family
    if _family is None:
        from matplotlib import font_manager

        from src.desktop.fonts import FONT_DIR, SANS_FAMILY

        files = [FONT_DIR / name for name in ("Inter-Regular.ttf", "Inter-SemiBold.ttf")]
        for path in files:
            if path.exists():
                font_manager.fontManager.addfont(str(path))
        _family = SANS_FAMILY if all(path.exists() for path in files) else "DejaVu Sans"
    return _family


def px_to_pt(px: float) -> float:
    return px * 72 / LOGICAL_DPI


def token_rgba(name: str, theme: str | None) -> tuple[float, float, float, float]:
    from src.desktop.theme import token_color

    color = token_color(name, theme)
    return color.redF(), color.greenF(), color.blueF(), color.alphaF()


# —— the Cases page's summary charts ——

SUMMARY_CHART_HEIGHT = 104
_NICE_STEPS = (1.0, 2.0, 2.5, 5.0)


def _nice_step(raw: float) -> float:
    """The smallest 1, 2, 2.5 or 5 times a power of ten that is at least ``raw``."""
    if raw <= 0:
        return 1.0
    scale = 10.0 ** math.floor(math.log10(raw))
    return next(step * scale for step in (*_NICE_STEPS, 10.0) if step * scale >= raw * (1 - 1e-9))


def histogram_edges(values: Sequence[float], max_bins: int = 8) -> list[float]:
    """Whole-number-friendly bin edges from zero (or the lowest value) up past the highest, at most ``max_bins`` bins."""
    known = [float(v) for v in values if pd.notna(v)]
    high = max(known, default=0.0)
    low = min(0.0, min(known, default=0.0))
    step = _nice_step((high - low) / max_bins) if high > low else 1.0
    first = math.floor(low / step) * step
    count = max(int(math.floor((high - first) / step)) + 1, 1)
    return [round(first + step * i, 10) for i in range(count + 1)]


def tick_values(low: float, high: float, wanted: int = 4) -> list[float]:
    """Round axis values covering ``low`` to ``high``, about ``wanted`` of them."""
    step = _nice_step((high - low) / wanted) if high > low else 1.0
    first = math.floor(low / step) * step
    count = max(int(math.ceil((high - first) / step - 1e-9)), 1)
    return [round(first + step * i, 10) for i in range(count + 1)]


def _summary_canvas(width: int, height: int, dpi: float, theme: str | None):
    """A figure whose one axes is the whole figure in logical pixels, origin at the top left, drawn in the tokens."""
    from src.desktop.theme import tokens

    colors = tokens(theme)
    fig = plt.figure(figsize=(width / LOGICAL_DPI, height / LOGICAL_DPI), dpi=dpi)
    fig.patch.set_facecolor(colors["paper"])
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_xlim(0, width)
    ax.set_ylim(height, 0)
    ax.axis("off")
    return fig, ax, colors


def _label(ax, x: float, y: float, text: str, color: str, px: float, **kwargs: Any) -> None:
    ax.text(x, y, text, color=color, fontsize=px_to_pt(px), **kwargs)


def _elide(text: str, px: float, room: float) -> str:
    """``text`` cut with an ellipsis so it is at most ``room`` logical pixels wide at ``px`` size in the chart font."""
    from matplotlib.font_manager import FontProperties
    from matplotlib.textpath import TextPath

    props = FontProperties(family=chart_family())

    def width(candidate: str) -> float:
        return float(TextPath((0, 0), candidate, size=px, prop=props).get_extents().width) if candidate else 0.0

    if width(text) <= room:
        return text
    shown = text
    while shown and width(shown + "…") > room:
        shown = shown[:-1]
    return shown.rstrip() + "…"


def _count_text(value: float) -> str:
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.1f}"


def score_histogram_figure(
    scores: Sequence[float], *, width: int, height: int = SUMMARY_CHART_HEIGHT,
    dpi: float = LOGICAL_DPI, theme: str | None = None,
) -> plt.Figure:
    """Cases per anomaly-score range: ink bars on the paper, the tallest bar's count at the top left."""
    from matplotlib.patches import Rectangle

    values = [float(v) for v in scores if pd.notna(v)]
    edges = histogram_edges(values)
    counts = [int(n) for n in np.histogram(values, bins=edges)[0]]
    with matplotlib.rc_context({"font.family": chart_family()}):
        fig, ax, colors = _summary_canvas(width, height, dpi, theme)
        left, base, label_gap = 22.0, height - 16.0, 14.0
        slot = (width - left) / len(counts)
        peak = max(max(counts), 1)
        for index, count in enumerate(counts):
            if count:
                bar = (base - label_gap) * count / peak
                ax.add_patch(Rectangle((left + index * slot + 1, base - bar), max(slot - 3, 1.0), bar,
                                       facecolor=colors["ink_2"], edgecolor="none"))
        ax.plot([left, width], [base, base], color=colors["ink_3"], lw=px_to_pt(1), solid_capstyle="butt")
        # With narrow slots every other edge is named, so the numbers never touch.
        every = 1 if slot >= 24 else 2
        last = len(edges) - 1
        for index, edge in enumerate(edges):
            if index % every == 0:
                _label(ax, left + index * slot, height - 3, f"{edge:g}", colors["ink_3"], 11,
                       ha="right" if index == last and index else "center", va="baseline")
        _label(ax, 0, 12, str(peak), colors["ink_3"], 11, ha="left", va="baseline")
    return fig


def rule_minutes_figure(
    rows: Sequence[tuple[str, float]], *, width: int, height: int = SUMMARY_CHART_HEIGHT,
    dpi: float = LOGICAL_DPI, theme: str | None = None,
) -> plt.Figure:
    """Rules, most frequent first, as ink bars with the rule's name before each and its number at the bar's end."""
    from matplotlib.patches import Rectangle

    with matplotlib.rc_context({"font.family": chart_family()}):
        fig, ax, colors = _summary_canvas(width, height, dpi, theme)
        name_room = min(150.0, width * 0.5)
        bar_left = name_room + 8
        room = max(width - bar_left - 34, 10.0)
        peak = max((float(value) for _name, value in rows), default=0.0) or 1.0
        pitch = min(20.0, (height - 4) / max(len(rows), 1))
        for index, (name, value) in enumerate(rows):
            middle = 2 + (index + 0.5) * pitch
            _label(ax, 0, middle, _elide(name, 12, name_room), colors["ink_2"], 12, ha="left", va="center")
            length = max(float(value) / peak * room, 1.0)
            ax.add_patch(Rectangle((bar_left, middle - 4.5), length, 9, facecolor=colors["ink_2"], edgecolor="none"))
            _label(ax, bar_left + length + 6, middle, _count_text(float(value)), colors["ink_3"], 11,
                   ha="left", va="center")
    return fig


def duration_ticks_figure(
    minutes: Sequence[float], *, width: int, height: int = SUMMARY_CHART_HEIGHT,
    dpi: float = LOGICAL_DPI, theme: str | None = None,
) -> plt.Figure:
    """One ink tick per case on a minutes axis, with the median as a heavier line named "median"."""
    values = sorted(float(v) for v in minutes if pd.notna(v))
    median = float(np.median(values)) if values else 0.0
    low, high = (values[0], values[-1]) if values else (0.0, 1.0)
    if high <= low:
        low, high = low - 5, high + 5
    # about one axis number per 55 px, so the numbers of a narrow chart never touch
    ticks = tick_values(low, high, max(2, min(5, int(width // 55))))
    first, last = ticks[0], ticks[-1]
    with matplotlib.rc_context({"font.family": chart_family()}):
        fig, ax, colors = _summary_canvas(width, height, dpi, theme)
        # room at each end for half of the first and last axis numbers, which are centred on their ticks
        pad, pad_right = 14.0, 16.0
        axis_y, label_y = height - 38.0, height - 24.0

        def at(value: float) -> float:
            return pad + (value - first) / (last - first) * (width - pad - pad_right)

        # one collection for every tick: a line per case would cost a second for a few thousand cases
        ax.vlines([at(value) for value in values], axis_y - 32, axis_y - 6, colors=colors["ink_2"], linewidth=px_to_pt(1.3))
        if values:
            ax.plot([at(median)] * 2, [axis_y - 48, axis_y], color=colors["ink"], lw=px_to_pt(2), solid_capstyle="butt")
            near_right = at(median) > width * 0.75
            _label(ax, at(median) + (-4 if near_right else 4), axis_y - 48, "median", colors["ink_2"], 11,
                   ha="right" if near_right else "left", va="top")
        ax.plot([pad, width - pad_right], [axis_y, axis_y], color=colors["ink_3"], lw=px_to_pt(1), solid_capstyle="butt")
        for value in ticks:
            _label(ax, at(value), label_y, f"{value:g}", colors["ink_3"], 11, ha="center", va="top")
        _label(ax, width - pad_right, height - 3, "min", colors["ink_3"], 11, ha="right", va="baseline")
    return fig


@dataclass(frozen=True)
class Band:
    """One rule episode on the lane: its position in the episode frame, its minutes and its row."""

    index: int
    rule_id: str
    severity: str
    start: float
    end: float
    row: int


def case_marks(ts: pd.DataFrame) -> dict[str, float]:
    """Incision and closure, in minutes from incision, for a case whose surgery times were recorded."""
    marks: dict[str, float] = {}
    if "Surgery_start" not in ts or "t_min" not in ts:
        return marks
    start = pd.to_datetime(ts["Surgery_start"], errors="coerce").dropna()
    if start.empty:
        return marks
    marks["Incision"] = 0.0
    if "Surgery_end" in ts:
        end = pd.to_datetime(ts["Surgery_end"], errors="coerce").dropna()
        if not end.empty:
            closure = (end.iloc[0] - start.iloc[0]).total_seconds() / 60
            if closure > 0:
                marks["Closure"] = closure
    return marks


def gap_spans(values: pd.Series) -> list[tuple[float, float]]:
    """Runs of missing minutes inside the span a signal was recorded, as minute-bin edges (start, end)."""
    present = values.notna()
    if not present.any():
        return []
    inside = values.loc[present.idxmax():present[::-1].idxmax()]
    missing = inside.isna()
    spans: list[tuple[float, float]] = []
    run: list[float] = []
    for minute, is_missing in missing.items():
        if is_missing:
            run.append(float(minute))
        elif run:
            spans.append((run[0] - 0.5, run[-1] + 0.5))
            run = []
    return spans


def _episodes_from_flags(flags: pd.DataFrame | None) -> pd.DataFrame:
    """Contiguous same-rule runs of flagged minutes, for callers that pass flags but no episode frame."""
    columns = ["rule_id", "severity", "t_start_min", "t_end_min"]
    if flags is None or flags.empty or not {"t_min", "severity"} <= set(flags.columns):
        return pd.DataFrame(columns=columns)
    rule = flags["rule_id"].astype(str) if "rule_id" in flags else pd.Series("flag", index=flags.index)
    frame = pd.DataFrame({"rule_id": rule, "severity": flags["severity"].astype(str),
                          "t_min": pd.to_numeric(flags["t_min"], errors="coerce")}).dropna(subset=["t_min"])
    frame = frame.sort_values(["rule_id", "t_min"])
    frame["run"] = (frame.groupby("rule_id")["t_min"].diff().gt(1.5) | frame["t_min"].isna()).groupby(
        frame["rule_id"]).cumsum()
    rank = {"critical": 0, "warn": 1, "info": 2}
    grouped = frame.groupby(["rule_id", "run"]).agg(
        severity=("severity", lambda s: min(s, key=lambda word: rank.get(word, 3))),
        t_start_min=("t_min", "min"), t_end_min=("t_min", "max")).reset_index()
    return grouped.drop(columns="run").sort_values(["t_start_min", "rule_id"]).reset_index(drop=True)


def pack_episodes(episodes: pd.DataFrame) -> list[Band]:
    """Give each episode a lane row so overlapping episodes sit one above the other, in the frame's order.

    Past MAX_LANE_ROWS an episode shares the row that frees up first, so the lane never grows without bound.
    """
    bands: list[Band] = []
    row_ends: list[float] = []
    for index, episode in enumerate(cast(list[dict[Any, Any]], episodes.to_dict("records"))):
        start, end = float(episode["t_start_min"]) - 0.5, float(episode["t_end_min"]) + 0.5
        row = next((r for r, last in enumerate(row_ends) if last <= start), None)
        if row is not None:
            row_ends[row] = end
        elif len(row_ends) < MAX_LANE_ROWS:
            row = len(row_ends)
            row_ends.append(end)
        else:
            row = min(range(len(row_ends)), key=row_ends.__getitem__)
            row_ends[row] = max(row_ends[row], end)
        bands.append(Band(index, str(episode["rule_id"]), str(episode["severity"]), start, end, row))
    return bands


def chart_min_height(tracks: int, lane_rows: int) -> int:
    """The least height at which every track and lane row keeps its labels apart."""
    return (TOP_PAD + tracks * MIN_TRACK + (tracks - 1) * TRACK_GAP + LANE_PAD
            + max(lane_rows, 1) * LANE_ROW + AXIS_HEIGHT)


def _build_focused_in_style(
    ts: pd.DataFrame, selected_minute: float | None, *, flags: pd.DataFrame | None,
    episodes: pd.DataFrame | None, rule_labels: Mapping[str, str] | None, extra_signals: tuple[str, ...],
    figsize: tuple[float, float] | None, dpi: float, theme: str | None,
) -> tuple[plt.Figure, list[Band]]:
    from matplotlib.lines import Line2D
    from matplotlib.patches import Rectangle
    from matplotlib.ticker import MaxNLocator

    from src.desktop.theme import tokens

    colors = tokens(theme)
    family = chart_family()
    labels = dict(rule_labels or {})
    columns = [*BASE_TRACKS, *extra_signals[:3]]
    episode_frame = episodes if episodes is not None and not episodes.empty else _episodes_from_flags(flags)
    bands = pack_episodes(episode_frame)
    lane_rows = max((band.row for band in bands), default=0) + 1
    width_in, height_in = figsize or (11.0, chart_min_height(len(columns), lane_rows) / LOGICAL_DPI)
    fig = plt.figure(figsize=(width_in, height_in), dpi=dpi)
    fig.patch.set_facecolor(colors["paper"])
    width, height = width_in * LOGICAL_DPI, height_in * LOGICAL_DPI
    rows = focused_minute_rows(ts)
    low_x, high_x = (float(rows.index.min()) - 0.5, float(rows.index.max()) + 0.5) if not rows.empty else (0.0, 1.0)
    spare = height - TOP_PAD - AXIS_HEIGHT - LANE_PAD - lane_rows * LANE_ROW - (len(columns) - 1) * TRACK_GAP
    track_px = max(spare / len(columns), 40.0)
    left, plot_w = LABEL_WIDTH / width, (width - LABEL_WIDTH - RIGHT_PAD) / width

    def axes_at(top_px: float, size_px: float):
        ax = fig.add_axes((left, 1 - (top_px + size_px) / height, plot_w, size_px / height))
        ax.set_facecolor(colors["paper"])
        for side in ax.spines.values():
            side.set_visible(False)
        ax.set_xlim(low_x, high_x)
        ax.set_axisbelow(True)
        ax.tick_params(length=0, labelsize=px_to_pt(11), labelcolor=colors["ink_3"], pad=6)
        ax.xaxis.set_major_locator(MaxNLocator(nbins=8, steps=[1, 2, 5, 10], integer=True))
        ax.grid(True, axis="x", color=colors["grid"], lw=0.8)
        return ax

    marks = case_marks(ts)
    event_style = {"color": colors["ink_3"], "lw": 1.0, "ls": (0, (1, 3)), "zorder": 2}
    axes = []
    top: float = TOP_PAD
    for column in columns:
        info = signal_info(column)
        trace = colors[info.token]
        ax = axes_at(top, track_px)
        ax.set_label(column)
        ax.tick_params(axis="x", labelbottom=False)
        ax.grid(True, axis="y", color=colors["grid"], lw=0.8)
        ax.yaxis.set_major_locator(MaxNLocator(nbins=3, steps=[1, 2, 2.5, 5, 10]))
        values = pd.to_numeric(rows[column], errors="coerce") if column in rows else pd.Series(dtype=float)
        finite = values.dropna()
        if not finite.empty:
            pad = max(float(finite.max() - finite.min()) * 0.1, 0.5)
            ax.set_ylim(float(finite.min()) - pad, float(finite.max()) + pad)
            ax.plot(rows.index, values, color=trace, lw=1.5, solid_joinstyle="round", zorder=3)
        else:
            ax.set_ylim(0, 1)
        y_low, y_high = ax.get_ylim()
        for start, end in gap_spans(values):
            ax.add_patch(Rectangle((start, y_low), end - start, y_high - y_low, facecolor="none", lw=0, zorder=1,
                                   edgecolor=token_rgba("hatch", theme), hatch="////"))
        for minute in marks.values():
            if low_x <= minute <= high_x:
                ax.axvline(minute, **event_style)
        axes.append((ax, column, info, trace, top))
        top += track_px + TRACK_GAP

    lane_top = top - TRACK_GAP + LANE_PAD
    lane = axes_at(lane_top, lane_rows * LANE_ROW)
    lane.set_label("flags")
    lane.set_yticks([])
    lane.set_ylim(lane_rows, 0)
    lane.spines["bottom"].set_visible(True)
    lane.spines["bottom"].set_color(colors["rule_strong"])
    lane.tick_params(axis="x", labelsize=px_to_pt(11), pad=8)
    for minute in marks.values():
        if low_x <= minute <= high_x:
            lane.axvline(minute, **event_style)
    renderer = cast(FigureCanvasAgg, fig.canvas).get_renderer()
    scale = fig.dpi / LOGICAL_DPI

    def text_px(text: str, size: float, weight: int) -> float:
        probe = fig.text(0, 0, text, fontsize=px_to_pt(size), fontfamily=family, fontweight=weight)
        measured = probe.get_window_extent(renderer).width / scale
        probe.remove()
        return measured

    def put(x_px: float, y_px: float, text: str, size: float, color: str, weight: int = 400, ha: str = "left",
            va: str = "top"):
        return fig.text(x_px / width, 1 - y_px / height, text, fontsize=px_to_pt(size), color=color,
                        fontfamily=family, fontweight=weight, ha=ha, va=va)

    # Every track says its name and unit on one line; the value at the cursor is the widget's to place under it.
    for _ax, _column, info, trace, top_px in axes:
        fig.add_artist(Line2D([4 / width, 16 / width], [1 - (top_px + NAME_OFFSET + 8) / height] * 2,
                              transform=fig.transFigure, color=trace, lw=2.2, solid_capstyle="butt"))
        put(22, top_px + NAME_OFFSET, info.label, 12, colors["ink"], 600)
        if info.unit:
            put(22 + text_px(info.label, 12, 600) + 5, top_px + NAME_OFFSET + 1, info.unit, 11, colors["ink_3"])
    first = axes[0][0]
    for word, minute in marks.items():
        if low_x <= minute <= high_x:
            first.text(minute, 1.0, word, transform=first.get_xaxis_transform(), fontsize=px_to_pt(11), color=colors["ink_3"],
                       fontfamily=family, ha="center", va="bottom", clip_on=False)
    put(4, lane_top + 2, "Rule episodes", 12, colors["ink"], 600)
    put(4, lane_top + 19, "screening only", 11, colors["ink_3"])
    put(LABEL_WIDTH - 8, height - AXIS_HEIGHT + 14, "min from incision", 11, colors["ink_3"], ha="right", va="center")

    # Gaps are hatched in every track; the words go in only where they fit.
    px_per_minute = plot_w * width / (high_x - low_x)
    no_samples = text_px("no samples", 11, 600) + 8
    for ax, column, *_rest in axes:
        values = pd.to_numeric(rows[column], errors="coerce") if column in rows else pd.Series(dtype=float)
        y_low, y_high = ax.get_ylim()
        for start, end in gap_spans(values):
            if (end - start) * px_per_minute >= no_samples:
                ax.text((start + end) / 2, y_low + (y_high - y_low) * 0.62, "no samples", fontsize=px_to_pt(11),
                        fontfamily=family, fontweight=600, color=colors["ink_2"], ha="center", va="center", zorder=5,
                        bbox={"facecolor": colors["paper"], "edgecolor": "none", "pad": 1.5})

    if not bands:
        lane.text(0.5, 0.5, "No flags in this case", transform=lane.transAxes, color=colors["ink_2"],
                  ha="center", va="center", fontsize=px_to_pt(12), fontfamily=family)
    # Info is a hollow circle: a full marker in the ground's colour, so the band's hatch does not run through it.
    shapes = {"info": ("o", "full"), "warn": ("^", "left"), "critical": ("D", "full")}
    taken: dict[int, list[tuple[float, float]]] = {}
    for band in bands:
        lane.add_patch(Rectangle((band.start, band.row + 0.16), band.end - band.start, 0.68, facecolor="none", lw=0,
                                 edgecolor=token_rgba("hatch", theme), hatch="////", zorder=1))
        lane.add_patch(Rectangle((band.start, band.row + 0.16), band.end - band.start, 0.68, fill=False,
                                 edgecolor=colors["ink_2"], lw=0.9, zorder=2))
        taken.setdefault(band.row, []).append((band.start, band.end))
    for severity, (marker, fill) in shapes.items():
        chosen = [band for band in bands if band.severity == severity]
        if chosen:
            lane.plot([band.start for band in chosen], [band.row + 0.5 for band in chosen], linestyle="none",
                      marker=marker, markersize=6.5, fillstyle=fill, color=colors["ink"], markeredgewidth=1.1,
                      markerfacecolor=colors["paper"] if severity == "info" else colors["ink"],
                      markerfacecoloralt=colors["paper"], label=severity, zorder=4)
    lane_left_px = LABEL_WIDTH
    for band in bands:
        name = labels.get(band.rule_id, band.rule_id)
        text_width = text_px(name, 12, 400) + 6
        band_left = lane_left_px + (band.start - low_x) * px_per_minute
        band_right = lane_left_px + (band.end - low_x) * px_per_minute
        neighbours = [(lane_left_px + (s - low_x) * px_per_minute, lane_left_px + (e - low_x) * px_per_minute)
                      for s, e in taken[band.row] if (s, e) != (band.start, band.end)]
        right_room = min([n_left for n_left, _n_right in neighbours if n_left >= band_right] + [width - RIGHT_PAD])
        left_room = max([n_right for _n_left, n_right in neighbours if n_right <= band_left] + [lane_left_px])
        y_mid = band.row + 0.5
        if band_right + 6 + text_width <= right_room:
            lane.text(band.end + 6 / px_per_minute, y_mid, name, fontsize=px_to_pt(12), fontfamily=family,
                      color=colors["ink"], ha="left", va="center", zorder=5)
        elif band_left - 10 - text_width >= left_room:
            lane.text(band.start - 10 / px_per_minute, y_mid, name, fontsize=px_to_pt(12), fontfamily=family,
                      color=colors["ink"], ha="right", va="center", zorder=5)
    if selected_minute is not None:
        for ax in [item[0] for item in axes] + [lane]:
            ax.axvline(selected_minute, color=colors["accent"], lw=1.5, zorder=6)
    return fig, bands


def _build_focused(ts: pd.DataFrame, selected_minute: float | None, **kwargs: Any) -> tuple[plt.Figure, list[Band]]:
    with matplotlib.rc_context({"font.family": chart_family(), "hatch.linewidth": 0.8}):
        return _build_focused_in_style(ts, selected_minute, **kwargs)


def focused_signals_figure(
    ts: pd.DataFrame, selected_minute: float | None, *,
    flags: pd.DataFrame | None = None, extra_signals: tuple[str, ...] = (),
    figsize: tuple[float, float] | None = None, dpi: float = LOGICAL_DPI,
    episodes: pd.DataFrame | None = None, rule_labels: Mapping[str, str] | None = None,
    theme: str | None = None,
) -> plt.Figure:
    """Minute-aligned signal tracks and a rule-episode lane in the design's tokens; missing minutes break lines."""
    return _build_focused(ts, selected_minute, flags=flags, episodes=episodes, rule_labels=rule_labels,
                          extra_signals=extra_signals, figsize=figsize, dpi=dpi, theme=theme)[0]


def flag_minutes(flags: pd.DataFrame | None) -> dict[float, str]:
    if flags is None or flags.empty or "t_min" not in flags:
        return {}
    ranks = {"critical": 0, "warn": 1, "info": 2}
    ranked = flags.assign(_rank=flags["severity"].map(ranks).fillna(2))
    worst = ranked.sort_values(["t_min", "_rank"]).drop_duplicates("t_min")
    return {float(row.t_min): str(row.severity) for row in worst.itertuples() if pd.notna(row.t_min)}


@dataclass
class _FocusedChartImage:
    pixmap: QPixmap
    xlim: tuple[float, float]
    axes: tuple[QRectF, ...]
    tracks: tuple[tuple[str, tuple[float, float], str], ...]
    bands: tuple[tuple[Band, QRectF], ...]
    tick_labels: tuple[QRectF, ...]


class FocusedSignalChart(QWidget):
    """The case page's chart: a matplotlib image kept stable while a painted cursor moves over it.

    The value under each track's name is a real label, so a screen reader can read it; the cursor line,
    the rings on the traces and the minute tag are painted from the tokens on every repaint.
    """

    episode_clicked = Signal(int)
    minute_step = Signal(int)  # the keys' request to move the selected minute by this many minutes
    next_episode_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("focusedChart")
        self.setAccessibleName("Signal chart")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._ts = pd.DataFrame()
        self._rows = pd.DataFrame()
        self._flags = pd.DataFrame()
        self._episodes = pd.DataFrame()
        self._rule_labels: dict[str, str] = {}
        self._extras: tuple[str, ...] = ()
        self._images: dict[tuple[tuple[str, ...], int, int, float, str], _FocusedChartImage] = {}
        self._pixmap = QPixmap()
        self._minute: float | None = None
        self._selected: int | None = None
        self._base_description = ""
        self.lane_marks: dict[float, str] = {}
        self._xlim = (0., 1.)
        self._axes_rects: tuple[QRectF, ...] = ()
        self._tracks: tuple[tuple[str, tuple[float, float], str], ...] = ()
        self._bands: tuple[tuple[Band, QRectF], ...] = ()
        self._tick_labels: tuple[QRectF, ...] = ()
        # One readout per track, under its name. The first three never go away.
        self.value_labels: dict[str, QLabel] = {}
        for column in BASE_TRACKS:
            self._value_label(column)
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(120)
        self._resize_timer.timeout.connect(self._render_tracks)

    def _value_label(self, column: str) -> QLabel:
        label = self.value_labels.get(column)
        if label is None:
            label = QLabel(self)
            label.setObjectName("trackValue")
            label.setAccessibleName(f"{signal_info(column).label} at the selected minute")
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            make_tabular(label)
            label.hide()
            self.value_labels[column] = label
        return label

    def set_case(self, ts: pd.DataFrame, flags: pd.DataFrame, episodes: pd.DataFrame | None = None,
                 rule_labels: Mapping[str, str] | None = None) -> None:
        """Show a case. ``episodes`` is the frame the episode list shows, so a lane band's index is its row."""
        self._ts, self._flags = ts, flags
        self._rows = focused_minute_rows(ts) if "t_min" in ts and not ts.empty else pd.DataFrame()
        self._episodes = episodes if episodes is not None else pd.DataFrame()
        self._rule_labels = dict(rule_labels or {})
        self._images.clear()
        self._extras = ()
        self._selected = None
        self.lane_marks = flag_minutes(flags)
        counts = ", ".join(
            f"{sum(level == severity for level in self.lane_marks.values())} {severity} minutes"
            for severity in ("info", "warn", "critical")
        )
        self._base_description = (
            f"Minute-aligned PIP, ETCO2 and heart rate. Flag lane: {counts}. "
            "Exact measurements and episodes are available in the source rows and the rule episodes list."
        )
        self._render_tracks()

    def set_rule_labels(self, rule_labels: Mapping[str, str]) -> None:
        """New rule names, for example after the loaded preset changed."""
        if dict(rule_labels) != self._rule_labels:
            self._rule_labels = dict(rule_labels)
            self._images.clear()
            self._render_tracks()

    def set_tracks(self, extras: list[str]) -> None:
        chosen = tuple(extras[:3])
        if chosen != self._extras and not self._ts.empty:
            self._extras = chosen
            self._render_tracks()

    def set_selected_episode(self, index: int | None) -> None:
        """Outline the lane band of episode ``index`` of the frame given to ``set_case``."""
        self._selected = index
        self.update()

    def refresh(self) -> None:
        """Draw again in the current theme, never from an image made under the other one."""
        self._images.clear()
        self._render_tracks()

    def _episode_frame(self) -> pd.DataFrame:
        return self._episodes if not self._episodes.empty else _episodes_from_flags(self._flags)

    def _render_tracks(self) -> None:
        from src.desktop.theme import active_theme, tokens

        self._resize_timer.stop()
        if self._rows.empty:
            self._pixmap, self._tracks, self._axes_rects, self._bands, self._tick_labels = QPixmap(), (), (), (), ()
            self._place_labels()
            self.update()
            return
        columns = [*BASE_TRACKS, *self._extras]
        lane_rows = max((band.row for band in pack_episodes(self._episode_frame())), default=0) + 1
        self.setMinimumHeight(chart_min_height(len(columns), lane_rows))
        width, height, dpr = self.width(), max(self.height(), self.minimumHeight()), self.devicePixelRatioF()
        theme = active_theme()
        key = (self._extras, width, height, dpr, theme)
        if key not in self._images:
            fig, bands = _build_focused(
                self._ts, None, flags=self._flags, episodes=self._episodes, rule_labels=self._rule_labels,
                extra_signals=self._extras, figsize=(width / LOGICAL_DPI, height / LOGICAL_DPI),
                dpi=LOGICAL_DPI * dpr, theme=theme)
            axes = tuple(QRectF(box.x0 * width, (1 - box.y1) * height, box.width * width, box.height * height)
                         for ax in fig.axes for box in [ax.get_position()])
            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=fig.dpi, facecolor=fig.get_facecolor())
            xlim = fig.axes[-1].get_xlim()
            palette = tokens(theme)
            tracks = tuple(
                (str(ax.get_label()), ax.get_ylim(),
                 palette[signal_info(str(ax.get_label())).token]
                 if str(ax.get_label()) in self._rows and self._rows[str(ax.get_label())].notna().any() else "")
                for ax in fig.axes[:-1])
            lane = axes[-1]
            row_px = lane.height() / lane_rows
            placed = tuple(
                (band, QRectF(lane.left() + lane.width() * (band.start - xlim[0]) / (xlim[1] - xlim[0]),
                              lane.top() + (band.row + 0.16) * row_px,
                              lane.width() * (band.end - band.start) / (xlim[1] - xlim[0]), 0.68 * row_px))
                for band in bands)
            # Where the time axis's numbers sit, so the minute tag can cover whole numbers and not cut one.
            renderer = cast(FigureCanvasAgg, fig.canvas).get_renderer()
            ticks = tuple(
                QRectF(box.x0 / dpr, height - box.y1 / dpr, box.width / dpr, box.height / dpr)
                for label in fig.axes[-1].get_xticklabels() if label.get_text()
                for box in [label.get_window_extent(renderer)])
            plt.close(fig)
            pixmap = QPixmap()
            pixmap.loadFromData(buf.getvalue())
            pixmap.setDevicePixelRatio(dpr)
            self._images[key] = _FocusedChartImage(pixmap, xlim, axes, tracks, placed, ticks)
        rendered = self._images[key]
        self._pixmap, self._xlim, self._axes_rects = rendered.pixmap, rendered.xlim, rendered.axes
        self._tracks, self._bands, self._tick_labels = rendered.tracks, rendered.bands, rendered.tick_labels
        self._place_labels()
        self.update()

    def _place_labels(self) -> None:
        shown = [column for column, _limits, _color in self._tracks]
        for column in shown:
            self._value_label(column)
        for column in [name for name in self.value_labels if name not in shown]:
            label = self.value_labels[column]
            label.hide()
            if column not in BASE_TRACKS:
                del self.value_labels[column]
                label.deleteLater()
        for axis, column in zip(self._axes_rects, shown, strict=False):
            label = self.value_labels[column]
            label.setGeometry(4, int(axis.top()) + VALUE_OFFSET, LABEL_WIDTH - 34, 28)
            label.show()
        self._update_values()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if not self._ts.empty:
            self._resize_timer.start()

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.StyleChange) and not self._rows.empty:
            self._render_tracks()

    def set_minute(self, minute: float) -> None:
        self._minute = minute
        self._update_values()
        self.update()

    def _row_at_cursor(self) -> pd.Series | None:
        if self._minute is None or self._rows.empty:
            return None
        hit = self._rows.index.get_indexer([self._minute], method="nearest", tolerance=1e-6)[0]
        return None if hit < 0 else self._rows.iloc[hit]

    def _update_values(self) -> None:
        row = self._row_at_cursor()
        for column, _limits, _color in self._tracks:
            label = self.value_labels[column]
            value = pd.to_numeric(row.get(column), errors="coerce") if row is not None else float("nan")
            missing = row is not None and bool(pd.isna(value))
            label.setText("" if row is None else "missing" if missing else f"{float(value):g}")
            if label.property("missing") != missing:
                label.setProperty("missing", missing)
                label.style().unpolish(label)
                label.style().polish(label)
        self.setAccessibleDescription(f"{self._base_description} {self.cursor_summary()}".strip())

    def cursor_summary(self) -> str:
        """The values at the cursor minute in words, for a screen reader; a missing one says so."""
        row = self._row_at_cursor()
        if row is None or self._minute is None:
            return ""
        parts = []
        for column, _limits, _color in self._tracks:
            info = signal_info(column)
            value = pd.to_numeric(row.get(column), errors="coerce")
            parts.append(f"{info.label} {'missing' if pd.isna(value) else f'{float(value):g} {info.unit}'.strip()}")
        return f"Minute {self._minute:g}: " + ", ".join(parts)

    def cursor_points(self) -> list[tuple[str, QPointF, str]]:
        """Each track's value at the cursor minute; a missing value has no point."""
        row = self._row_at_cursor()
        if row is None or not self._tracks:
            return []
        low, high = self._xlim
        points = []
        for axis, (column, (y_low, y_high), color) in zip(self._axes_rects, self._tracks, strict=False):
            value = pd.to_numeric(row.get(column), errors="coerce")
            if pd.isna(value) or not color or y_high == y_low:
                continue
            x = axis.left() + axis.width() * (self._minute - low) / (high - low)
            y = axis.bottom() - axis.height() * (float(value) - y_low) / (y_high - y_low)
            points.append((column, QPointF(x, y), color))
        return points

    def cursor_x(self) -> float | None:
        """The cursor's x position in widget pixels, or None before a chart and a minute exist."""
        if self._minute is None or not self._axes_rects:
            return None
        low, high = self._xlim
        axis = self._axes_rects[0]
        return axis.left() + axis.width() * (self._minute - low) / (high - low)

    def band_rects(self) -> list[tuple[Band, QRectF]]:
        """Each rule episode's band on the lane, in widget pixels."""
        return list(self._bands)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), token_color("paper"))
        if self._pixmap.isNull():
            return
        painter.drawPixmap(QPointF(0, 0), self._pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        accent = token_color("accent")
        if self._selected is not None:
            painter.setPen(QPen(accent, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for band, rect in self._bands:
                if band.index == self._selected:
                    painter.drawRect(rect.adjusted(-2, -2, 2, 2))
        x = self.cursor_x()
        if x is not None:
            painter.setPen(QPen(accent, 1.5))
            first, lane = self._axes_rects[0], self._axes_rects[-1]
            painter.drawLine(QPointF(x, first.top() - 6), QPointF(x, lane.bottom()))
            for _column, point, color in self.cursor_points():
                painter.setPen(QPen(QColor(color), 1.8))
                painter.setBrush(token_color("paper"))
                painter.drawEllipse(point, 4.5, 4.5)
            self._paint_tag(painter, x, lane.bottom() + 6)
        if self.hasFocus():
            painter.setPen(QPen(accent, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self.rect().adjusted(1, 1, -2, -2))

    def _paint_tag(self, painter: QPainter, x: float, top: float) -> None:
        """The minute written on the time axis, where the cursor meets it."""
        text = f"{self._minute:g} min"
        font = QFont(self.font())
        font.setPixelSize(12)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        box = QRectF(0, top, painter.fontMetrics().horizontalAdvance(text) + 18, 20)
        box.moveLeft(min(max(x - box.width() / 2, 0.0), self.width() - box.width()))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(token_color("paper"))
        for label in self._tick_labels:
            if label.intersects(box):
                painter.drawRect(label.adjusted(-3, -2, 3, 2))
        painter.setBrush(token_color("accent"))
        painter.drawRoundedRect(box, 3, 3)
        painter.setPen(token_color("on_accent"))
        painter.drawText(box, int(Qt.AlignmentFlag.AlignCenter), text)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        """Left and Right move the minute by one, with Shift by ten; E asks for the next rule episode.

        The chart only asks: the page moves the slider and the episode list, so a key here does what the same
        key on those controls does.
        """
        # The number pad's arrows carry the keypad flag as well; it does not change what they mean.
        key, modifiers = event.key(), event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right) and modifiers in (
                Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.ShiftModifier):
            step = 10 if modifiers == Qt.KeyboardModifier.ShiftModifier else 1
            self.minute_step.emit(-step if key == Qt.Key.Key_Left else step)
            event.accept()
            return
        if key == Qt.Key.Key_E and modifiers == Qt.KeyboardModifier.NoModifier:
            self.next_episode_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        point = event.position()
        # Bands are drawn in list order, so the last one under the point is the one the user sees on top.
        for margin in (0, 3):
            hit = next((band for band, rect in reversed(self._bands)
                        if rect.adjusted(-margin, -margin, margin, margin).contains(point)), None)
            if hit is not None:
                self.episode_clicked.emit(hit.index)
                break
        super().mousePressEvent(event)



def _legend(ax, style: dict[str, str] | None = None) -> None:
    handles, labels = ax.get_legend_handles_labels()
    if not handles:
        return
    leg = ax.legend(loc="upper right", fontsize=7, frameon=True, fancybox=True, framealpha=0.9)
    if style:
        leg.get_frame().set_facecolor(style["figure_facecolor"])
        leg.get_frame().set_edgecolor(style["spine"])
        for text in leg.get_texts():
            text.set_color(style["text"])
