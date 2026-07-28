"""Fast matplotlib charts for the desktop UI (Plotly-free)."""

from __future__ import annotations

import io
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget


class ChartView(QWidget):
    """PNG chart panel backed by matplotlib.

    Renders figures into a QLabel inside a QScrollArea so tall charts
    (e.g. multi-panel case timelines) can be scrolled with the mouse wheel.
    """

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

    def set_figure(self, fig: Any | None) -> None:
        if fig is None:
            self.clear()
            return
        if not isinstance(fig, plt.Figure):
            self.clear("Unsupported chart type.")
            return
        style = _style()
        buf = io.BytesIO()
        fig.savefig(
            buf,
            format="png",
            dpi=120,
            bbox_inches="tight",
            facecolor=style["figure_facecolor"],
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


def top_cases_figure(cases: pd.DataFrame, n: int = 15) -> plt.Figure:
    style = _style()
    d = cases.nlargest(min(n, len(cases)), "anomaly_score").iloc[::-1]
    labels = d["PID"].astype(str).str.slice(0, 8)
    if "Procedure_short" in d.columns:
        labels = labels + " | " + d["Procedure_short"].fillna("").astype(str).str.slice(0, 24)
    fig, ax = _new_fig((7.5, max(2.5, 0.28 * len(d))), style)
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


def flag_rules_figure(flags: pd.DataFrame) -> plt.Figure:
    style = _style()
    fig, ax = _new_fig((7.5, 3.2), style)
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
    counts = (
        flags.groupby(["rule_id", "severity"], observed=False)
        .size()
        .unstack(fill_value=0)
    )
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
    ax.set_xticks(list(x))
    ax.set_xticklabels(list(counts.index), rotation=45, ha="right", fontsize=8, color=style["text"])
    ax.set_ylabel("Minute-flags")
    ax.set_title("Flag counts by rule", fontsize=11, pad=8)
    leg = ax.legend(fontsize=8, frameon=True, fancybox=True, framealpha=0.9)
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
