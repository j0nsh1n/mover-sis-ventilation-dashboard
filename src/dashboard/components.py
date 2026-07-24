"""Plotly / Streamlit visualization components."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

SEVERITY_COLOR = {
    "info": "#5B8FF9",
    "warn": "#F6BD16",
    "critical": "#E8684A",
}


def case_timeline_figure(
    ts: pd.DataFrame,
    flags: pd.DataFrame | None = None,
    events: pd.DataFrame | None = None,
    show_vitals: bool = True,
) -> go.Figure:
    """Multi-panel intraoperative timeline for one case."""
    rows = 5 if show_vitals else 4
    titles = [
        "Tidal volume (mL)",
        "Airway pressures (cmH₂O)",
        "ETCO₂ (mmHg)",
        "Anesthetic agent (vol % / MAC)",
    ]
    if show_vitals:
        titles.append("Vitals (HR / SpO₂ / MAP)")

    fig = make_subplots(
        rows=rows,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.04,
        subplot_titles=titles,
        row_heights=[1] * rows,
    )

    x = ts["t_min"]

    # Panel 1 — TV
    if "TV" in ts.columns:
        fig.add_trace(
            go.Scatter(x=x, y=ts["TV"], name="TV (mL)", line=dict(color="#1f77b4", width=1.5)),
            row=1, col=1,
        )
    if "TV_mlkg" in ts.columns and ts["TV_mlkg"].notna().any():
        fig.add_trace(
            go.Scatter(
                x=x, y=ts["TV_mlkg"], name="TV (mL/kg)",
                line=dict(color="#aec7e8", width=1, dash="dot"),
                yaxis="y2",
            ),
            row=1, col=1,
        )

    # Panel 2 — PIP / PEEP
    if "PIP" in ts.columns:
        fig.add_trace(
            go.Scatter(x=x, y=ts["PIP"], name="PIP", line=dict(color="#ff7f0e", width=1.5)),
            row=2, col=1,
        )
    if "PEEP" in ts.columns:
        fig.add_trace(
            go.Scatter(x=x, y=ts["PEEP"], name="PEEP", line=dict(color="#2ca02c", width=1.5)),
            row=2, col=1,
        )

    # Panel 3 — ETCO2
    if "ETCO2" in ts.columns:
        fig.add_trace(
            go.Scatter(x=x, y=ts["ETCO2"], name="ETCO₂", line=dict(color="#17becf", width=1.5)),
            row=3, col=1,
        )
        # Reference band 35–45
        fig.add_hrect(y0=35, y1=45, fillcolor="rgba(23,190,207,0.08)", line_width=0, row=3, col=1)

    # Panel 4 — Agent
    if "Agent_Et" in ts.columns:
        fig.add_trace(
            go.Scatter(x=x, y=ts["Agent_Et"], name="Agent Et%", line=dict(color="#9467bd", width=1.5)),
            row=4, col=1,
        )
    if "Agent_Fi" in ts.columns:
        fig.add_trace(
            go.Scatter(
                x=x, y=ts["Agent_Fi"], name="Agent Fi%",
                line=dict(color="#c5b0d5", width=1, dash="dash"),
            ),
            row=4, col=1,
        )
    if "MAC_Et" in ts.columns and ts["MAC_Et"].notna().any():
        fig.add_trace(
            go.Scatter(
                x=x, y=ts["MAC_Et"], name="MAC Et",
                line=dict(color="#8c564b", width=1, dash="dot"),
            ),
            row=4, col=1,
        )

    # Panel 5 — vitals
    if show_vitals:
        if "HR" in ts.columns:
            fig.add_trace(
                go.Scatter(x=x, y=ts["HR"], name="HR", line=dict(color="#d62728", width=1.2)),
                row=5, col=1,
            )
        if "SPO2" in ts.columns:
            fig.add_trace(
                go.Scatter(x=x, y=ts["SPO2"], name="SpO₂", line=dict(color="#1f77b4", width=1.2)),
                row=5, col=1,
            )
        if "nMAP" in ts.columns:
            fig.add_trace(
                go.Scatter(x=x, y=ts["nMAP"], name="nMAP", line=dict(color="#e377c2", width=1.2)),
                row=5, col=1,
            )

    # Flag markers on all panels as vertical lines for critical/warn
    if flags is not None and not flags.empty:
        for sev, color in SEVERITY_COLOR.items():
            sub = flags[flags["severity"] == sev]
            if sub.empty:
                continue
            # sample markers to avoid overplot: unique t_min
            for t in sub["t_min"].dropna().unique()[:: max(1, len(sub) // 40)]:
                fig.add_vline(
                    x=t, line_width=1, line_dash="dot",
                    line_color=color, opacity=0.35,
                )

    # Procedure events
    if events is not None and not events.empty and "case_start" in ts.columns:
        case_start = ts["case_start"].iloc[0] if ts["case_start"].notna().any() else ts["Obs_time"].min()
        for _, ev in events.iterrows():
            t = (ev["Event_time"] - case_start).total_seconds() / 60.0
            fig.add_vline(x=t, line_width=2, line_color="#333", opacity=0.6)
            fig.add_annotation(
                x=t, y=1.02, yref="paper", text=str(ev["Event_name"]),
                showarrow=False, textangle=-90, font=dict(size=10),
            )

    fig.update_layout(
        height=180 * rows + 80,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        margin=dict(l=60, r=20, t=60, b=40),
        hovermode="x unified",
        template="plotly_white",
    )
    fig.update_xaxes(title_text="Minutes from case start", row=rows, col=1)
    return fig


def flag_bar_by_rule(flags: pd.DataFrame) -> go.Figure:
    if flags is None or flags.empty:
        fig = go.Figure()
        fig.add_annotation(text="No flags", showarrow=False)
        return fig
    counts = (
        flags.groupby(["rule_id", "severity"]).size().reset_index(name="n")
    )
    fig = go.Figure()
    for sev in ["info", "warn", "critical"]:
        sub = counts[counts["severity"] == sev]
        if sub.empty:
            continue
        fig.add_trace(go.Bar(
            x=sub["rule_id"], y=sub["n"], name=sev,
            marker_color=SEVERITY_COLOR.get(sev, "#999"),
        ))
    fig.update_layout(
        barmode="stack",
        title="Flag counts by rule",
        xaxis_title="Rule",
        yaxis_title="Minute-flags",
        height=360,
        template="plotly_white",
    )
    return fig


def top_cases_bar(cases: pd.DataFrame, n: int = 15) -> go.Figure:
    if cases is None or cases.empty:
        return go.Figure()
    d = cases.nlargest(n, "anomaly_score").iloc[::-1]
    labels = d["PID"].str.slice(0, 8)
    if "Procedure_short" in d.columns:
        labels = labels + " | " + d["Procedure_short"].fillna("").str.slice(0, 30)
    fig = go.Figure(go.Bar(
        x=d["anomaly_score"],
        y=labels,
        orientation="h",
        marker_color="#E8684A",
        text=d["anomaly_score"],
        textposition="outside",
    ))
    fig.update_layout(
        title=f"Top {n} cases by anomaly score",
        xaxis_title="Anomaly score",
        height=max(320, 28 * n),
        margin=dict(l=20, r=40, t=50, b=40),
        template="plotly_white",
    )
    return fig
