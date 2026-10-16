"""Fast matplotlib charts for the desktop UI (Plotly-free)."""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import to_hex  # noqa: E402
import pandas as pd  # noqa: E402
from PySide6.QtCore import QPointF, Qt, QRectF, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget


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
    try:
        from src.desktop.theme import chart_style

        return chart_style()
    except Exception:
        return {
            "figure_facecolor": "#ffffff",
            "axes_facecolor": "#ffffff",
            "text": "#0f172a",
            "muted": "#64748b",
            "grid": "#e2e8f0",
            "spine": "#cbd5e1",
            "bar": "#ea580c",
            "info": "#3b82f6",
            "warn": "#d97706",
            "critical": "#dc2626",
        }


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


def top_cases_figure(cases: pd.DataFrame, n: int = 15, *, figsize: tuple[float, float] | None = None) -> plt.Figure:
    style = _style()
    d = cases.nlargest(min(n, len(cases)), "anomaly_score").iloc[::-1]
    labels = d["PID"].astype(str).str.slice(0, 8)
    if "Procedure_short" in d.columns:
        labels = labels + " | " + d["Procedure_short"].fillna("").astype(str).str.slice(0, 24)
    fig, ax = _new_fig(figsize or (7.5, max(2.5, 0.28 * len(d))), style)
    ax.barh(
        range(len(d)),
        d["anomaly_score"].astype(float),
        color=style["bar"],
        height=0.72,
        edgecolor="none",
    )
    ax.set_yticks(range(len(d)))
    ax.set_yticklabels(list(labels), fontsize=8, color=style["text"])
    ax.set_xlabel("Anomaly score")
    ax.set_title(f"Top {len(d)} cases by anomaly score", fontsize=11, pad=8)
    ax.grid(True, axis="x", alpha=0.35, color=style["grid"])
    ax.grid(False, axis="y")
    fig.tight_layout()
    return fig


def flag_rules_figure(
    flags: pd.DataFrame, *, count_column: str | None = None, figsize: tuple[float, float] | None = None,
) -> plt.Figure:
    style = _style()
    fig, ax = _new_fig(figsize or (7.5, 3.2), style)
    if flags is None or flags.empty:
        ax.text(
            0.5,
            0.5,
            "No flags",
            ha="center",
            va="center",
            color=style["muted"],
            fontsize=11,
        )
        ax.set_axis_off()
        fig.tight_layout()
        return fig
    grouped = flags.groupby(["rule_id", "severity"], observed=False)
    counts = (grouped[count_column].sum() if count_column else grouped.size()).unstack(fill_value=0)
    for col in ("info", "warn", "critical"):
        if col not in counts.columns:
            counts[col] = 0
    counts = counts[["info", "warn", "critical"]]
    counts = counts.loc[counts.sum(axis=1).sort_values(ascending=False).index]
    counts = counts.head(20)
    colors = {
        "info": style["info"],
        "warn": style["warn"],
        "critical": style["critical"],
    }
    bottom = None
    x = range(len(counts))
    for sev in ("info", "warn", "critical"):
        vals = counts[sev].values
        ax.bar(x, vals, bottom=bottom, label=sev, color=colors[sev], width=0.78, edgecolor="none")
        bottom = vals if bottom is None else bottom + vals
    # Headroom keeps the legend off the tallest bars
    ax.set_ylim(0, max(float(counts.sum(axis=1).max()), 1.0) * 1.22)
    ax.set_xticks(list(x))
    ax.set_xticklabels(list(counts.index), rotation=45, ha="right", fontsize=8, color=style["text"])
    ax.set_ylabel("Minute-flags")
    ax.set_title("Flag counts by rule", fontsize=11, pad=8)
    leg = ax.legend(fontsize=8, frameon=True, fancybox=True, framealpha=0.9, loc="upper right", ncol=3)
    leg.get_frame().set_facecolor(style["figure_facecolor"])
    leg.get_frame().set_edgecolor(style["spine"])
    for text in leg.get_texts():
        text.set_color(style["text"])
    ax.grid(True, axis="y", alpha=0.35, color=style["grid"])
    ax.grid(False, axis="x")
    fig.tight_layout()
    return fig


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


def focused_signals_figure(
    ts: pd.DataFrame, selected_minute: float | None, *,
    flags: pd.DataFrame | None = None, extra_signals: tuple[str, ...] = (),
    figsize: tuple[float, float] | None = None, dpi: float = 110,
) -> plt.Figure:
    """Minute-aligned signal tracks and a severity lane; missing minutes break lines."""
    style = {
        "figure_facecolor": "#142124", "axes_facecolor": "#142124",
        "text": "#dce8dd", "muted": "#a3b8b2", "grid": "#3b5755", "spine": "#35484a",
    }
    tracks = [("PIP", "PIP", "cmH₂O", "#f2b173"),
              ("ETCO2", "ETCO₂", "mmHg", "#8fd8c8"),
              ("HR", "Heart rate", "bpm", "#c7ace0")]
    tracks.extend((signal, signal, "", "#c4ed93") for signal in extra_signals[:3])
    fig, axes = plt.subplots(len(tracks) + 1, 1, figsize=figsize or (11, 2.8 + .65 * len(extra_signals[:3])), dpi=dpi,
                             sharex=True, gridspec_kw={"height_ratios": [1] * len(tracks) + [.5]})
    fig.patch.set_facecolor(style["figure_facecolor"])
    grid = focused_minute_rows(ts)
    for ax, (column, label, unit, color) in zip(axes[:-1], tracks, strict=True):
        _style_axes(ax, style)
        ax.set_label(column)
        if column in grid:
            ax.plot(grid.index, pd.to_numeric(grid[column], errors="coerce"), color=color, lw=1.5)
        if selected_minute is not None:
            ax.axvline(selected_minute, color="#c4ed93", alpha=.8, lw=1, ls="--")
        ax.set_ylabel(f"{label}\n{unit}".strip(), fontsize=8)
    lane = axes[-1]
    _style_axes(lane, style)
    lane.set_label("flags")
    lane.set_yticks([])
    lane.set_ylim(-1, 1)
    lane.set_ylabel("Flags", fontsize=8)
    marks = flag_minutes(flags)
    if marks:
        colors = _style()
        lane.scatter(list(marks), [0] * len(marks), marker="|", s=65,
                     c=[colors[severity] for severity in marks.values()])
    else:
        lane.text(.5, .5, "No flags in this case", transform=lane.transAxes,
                  color=style["text"], ha="center", va="center", fontsize=8)
    if not grid.empty:
        low, high = float(grid.index.min()), float(grid.index.max())
        lane.set_xlim(low - .5, high + .5)
    lane.set_xlabel("Minutes from incision", fontsize=9)
    fig.subplots_adjust(left=max(.10, .72 / fig.get_figwidth()), right=.99, top=.98,
                        bottom=max(.14, .48 / fig.get_figheight()), hspace=.25)
    return fig


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


class FocusedSignalChart(QWidget):
    """Keep the source image stable while the minute cursor moves."""

    minute_clicked = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("chartView")
        self._ts = pd.DataFrame()
        self._rows = pd.DataFrame()
        self._flags = pd.DataFrame()
        self._extras: tuple[str, ...] = ()
        self._images: dict[tuple[tuple[str, ...], int, int, float], _FocusedChartImage] = {}
        self._pixmap = QPixmap()
        self._minute: float | None = None
        self.lane_marks: dict[float, str] = {}
        self._xlim = (0., 1.)
        self._axes_rects: tuple[QRectF, ...] = ()
        self._tracks: tuple[tuple[str, tuple[float, float], str], ...] = ()
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(120)
        self._resize_timer.timeout.connect(self._render_tracks)

    def set_case(self, ts: pd.DataFrame, flags: pd.DataFrame) -> None:
        self._ts, self._flags = ts, flags
        self._rows = focused_minute_rows(ts) if "t_min" in ts else pd.DataFrame()
        self._images.clear()
        self._extras = ()
        self.lane_marks = flag_minutes(flags)
        self._render_tracks()

    def set_tracks(self, extras: list[str]) -> None:
        chosen = tuple(extras[:3])
        if chosen != self._extras and not self._ts.empty:
            self._extras = chosen
            self._render_tracks()

    def _render_tracks(self) -> None:
        self._resize_timer.stop()
        self.setMinimumHeight(250 + 65 * len(self._extras))
        width, height, dpr = self.width(), self.height(), self.devicePixelRatioF()
        key = (self._extras, width, height, dpr)
        if key not in self._images:
            fig = focused_signals_figure(self._ts, None, flags=self._flags, extra_signals=self._extras,
                                        figsize=(width / 110, height / 110), dpi=110 * dpr)
            axes = tuple(QRectF(box.x0 * width, (1 - box.y1) * height, box.width * width, box.height * height)
                         for ax in fig.axes for box in [ax.get_position()])
            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=fig.dpi, facecolor=fig.get_facecolor())
            xlim = fig.axes[-1].get_xlim()
            tracks = tuple((str(ax.get_label()), ax.get_ylim(), to_hex(ax.lines[0].get_color()) if ax.lines else "")
                           for ax in fig.axes[:-1])
            plt.close(fig)
            pixmap = QPixmap()
            pixmap.loadFromData(buf.getvalue())
            pixmap.setDevicePixelRatio(dpr)
            self._images[key] = _FocusedChartImage(pixmap, xlim, axes, tracks)
        rendered = self._images[key]
        self._pixmap, self._xlim, self._axes_rects = rendered.pixmap, rendered.xlim, rendered.axes
        self._tracks = rendered.tracks
        self.update()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if not self._ts.empty:
            self._resize_timer.start()

    def set_minute(self, minute: float) -> None:
        self._minute = minute
        self.update()

    def cursor_points(self) -> list[tuple[str, QPointF, str]]:
        """Each track's value at the cursor minute; a missing value has no point."""
        if self._minute is None or self._rows.empty or not self._tracks:
            return []
        hit = self._rows.index.get_indexer([self._minute], method="nearest", tolerance=1e-6)[0]
        if hit < 0:
            return []
        row = self._rows.iloc[hit]
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

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#142124"))
        if self._pixmap.isNull():
            return
        painter.drawPixmap(QPointF(0, 0), self._pixmap)
        if self._minute is not None:
            low, high = self._xlim
            painter.setPen(QPen(QColor("#c4ed93"), 1, Qt.PenStyle.DashLine))
            for axis in self._axes_rects:
                x = axis.left() + axis.width() * (self._minute - low) / (high - low)
                painter.drawLine(QPointF(x, axis.top()), QPointF(x, axis.bottom()))
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor("#142124"), 1.5))
            for _column, point, color in self.cursor_points():
                painter.setBrush(QColor(color))
                painter.drawEllipse(point, 4, 4)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        point = event.position()
        if self.lane_marks and self._axes_rects and self._axes_rects[-1].contains(point):
            lane = self._axes_rects[-1]
            low, high = self._xlim
            minute = low + (point.x() - lane.left()) / lane.width() * (high - low)
            nearest = min(self.lane_marks, key=lambda marked: abs(marked - minute))
            mark_x = lane.left() + lane.width() * (nearest - low) / (high - low)
            if abs(point.x() - mark_x) <= 6:
                self.minute_clicked.emit(nearest)
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
