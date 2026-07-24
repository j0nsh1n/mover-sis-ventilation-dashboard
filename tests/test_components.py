"""Dashboard figure builders (no Streamlit runtime)."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from src.dashboard.components import case_timeline_figure, flag_bar_by_rule, top_cases_bar


def test_timeline_figure(pipeline_result):
    ts = pipeline_result["timeseries"]
    flags = pipeline_result["flags"]
    pid = ts["PID"].iloc[0]
    fig = case_timeline_figure(ts[ts.PID == pid], flags[flags.PID == pid])
    assert isinstance(fig, go.Figure)
    assert len(fig.data) >= 1


def test_top_cases_bar(pipeline_result):
    fig = top_cases_bar(pipeline_result["cases"], n=5)
    assert isinstance(fig, go.Figure)


def test_flag_bar_empty():
    empty = pd.DataFrame(
        columns=["PID", "Obs_time", "t_min", "rule_id", "severity", "value", "message"]
    )
    fig = flag_bar_by_rule(empty)
    assert isinstance(fig, go.Figure)


def test_flag_bar(pipeline_result):
    fig = flag_bar_by_rule(pipeline_result["flags"])
    assert isinstance(fig, go.Figure)
