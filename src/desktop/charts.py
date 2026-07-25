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
    """PNG chart panel backed by matplotlib."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._label = QLabel("No chart to display.")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setWordWrap(True)
        self._label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._label.setMinimumHeight(180)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._label)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll)

    def clear(self, message: str = "No chart to display.") -> None:
        self._label.setPixmap(QPixmap())
        self._label.setText(message)

    def set_figure(self, fig: Any | None) -> None:
        if fig is None:
            self.clear()
            return
        if not isinstance(fig, plt.Figure):
            self.clear("Unsupported chart type.")
            return
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100, bbox_inches="tight", facecolor="white")
        plt.close(fig)
        buf.seek(0)
        pix = QPixmap()
        pix.loadFromData(buf.read())
        w = max(self.width() - 20, 480)
        if pix.width() > w:
            pix = pix.scaledToWidth(w, Qt.TransformationMode.SmoothTransformation)
        self._label.setText("")
        self._label.setPixmap(pix)


# Back-compat alias used by older imports
LegacyChartView = ChartView


def top_cases_figure(cases: pd.DataFrame, n: int = 15) -> plt.Figure:
    d = cases.nlargest(min(n, len(cases)), "anomaly_score").iloc[::-1]
    labels = d["PID"].astype(str).str.slice(0, 8)
    if "Procedure_short" in d.columns:
        labels = labels + " | " + d["Procedure_short"].fillna("").astype(str).str.slice(0, 24)
    fig, ax = plt.subplots(figsize=(7.5, max(2.5, 0.28 * len(d))))
    ax.barh(range(len(d)), d["anomaly_score"].astype(float), color="#E8684A")
    ax.set_yticks(range(len(d)))
    ax.set_yticklabels(list(labels), fontsize=8)
    ax.set_xlabel("Anomaly score")
    ax.set_title(f"Top {len(d)} cases by anomaly score")
    ax.grid(True, axis="x", alpha=0.3)
    fig.tight_layout()
    return fig


def flag_rules_figure(flags: pd.DataFrame) -> plt.Figure:
    fig, ax = plt.subplots(figsize=(7.5, 3.2))
    if flags is None or flags.empty:
        ax.text(0.5, 0.5, "No flags", ha="center", va="center")
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
    colors = {"info": "#5B8FF9", "warn": "#F6BD16", "critical": "#E8684A"}
    bottom = None
    x = range(len(counts))
    for sev in ("info", "warn", "critical"):
        vals = counts[sev].values
        ax.bar(x, vals, bottom=bottom, label=sev, color=colors[sev])
        bottom = vals if bottom is None else bottom + vals
    ax.set_xticks(list(x))
    ax.set_xticklabels(list(counts.index), rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("Minute-flags")
    ax.set_title("Flag counts by rule")
    ax.legend(fontsize=8)
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    return fig


def case_timeline_figure(
    ts: pd.DataFrame,
    flags: pd.DataFrame | None = None,
    show_vitals: bool = True,
) -> plt.Figure:
    rows = 5 if show_vitals else 4
    fig, axes = plt.subplots(rows, 1, figsize=(10, 1.7 * rows), sharex=True)
    x = ts["t_min"] if "t_min" in ts.columns else range(len(ts))

    def line(ax, col, label, color, ls="-"):
        if col in ts.columns and ts[col].notna().any():
            ax.plot(x, ts[col], color=color, lw=1.1, label=label, ls=ls)

    line(axes[0], "TV", "TV (mL)", "#1f77b4")
    axes[0].set_ylabel("TV", fontsize=8)
    axes[0].grid(True, alpha=0.3)
    _legend(axes[0])

    line(axes[1], "PIP", "PIP", "#ff7f0e")
    line(axes[1], "PEEP", "PEEP", "#2ca02c")
    axes[1].set_ylabel("Press.", fontsize=8)
    axes[1].grid(True, alpha=0.3)
    _legend(axes[1])

    line(axes[2], "ETCO2", "ETCO₂", "#17becf")
    axes[2].axhspan(35, 45, color="#17becf", alpha=0.08)
    axes[2].set_ylabel("ETCO₂", fontsize=8)
    axes[2].grid(True, alpha=0.3)
    _legend(axes[2])

    line(axes[3], "Agent_Et", "Agent Et%", "#9467bd")
    line(axes[3], "Agent_Fi", "Agent Fi%", "#c5b0d5", ls="--")
    line(axes[3], "MAC_Et", "MAC Et", "#8c564b", ls=":")
    axes[3].set_ylabel("Agent", fontsize=8)
    axes[3].grid(True, alpha=0.3)
    _legend(axes[3])

    if show_vitals:
        line(axes[4], "HR", "HR", "#d62728")
        line(axes[4], "SPO2", "SpO₂", "#1f77b4")
        line(axes[4], "nMAP", "nMAP", "#e377c2")
        axes[4].set_ylabel("Vitals", fontsize=8)
        axes[4].set_xlabel("Minutes from case start", fontsize=9)
        axes[4].grid(True, alpha=0.3)
        _legend(axes[4])
    else:
        axes[3].set_xlabel("Minutes from case start", fontsize=9)

    if flags is not None and not flags.empty and "t_min" in flags.columns:
        times = flags["t_min"].dropna().unique()
        step = max(1, len(times) // 25)
        for t in times[::step]:
            for ax in axes:
                ax.axvline(t, color="#E8684A", alpha=0.2, lw=0.7)

    fig.suptitle("Case timeline", fontsize=11)
    fig.tight_layout()
    return fig


def _legend(ax) -> None:
    handles, labels = ax.get_legend_handles_labels()
    if handles:
        ax.legend(loc="upper right", fontsize=7)
