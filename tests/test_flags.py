"""Anomaly flag rules and scoring."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.pipeline.features import add_features
from src.pipeline.flags import (
    _sev_threshold,
    collapse_episodes,
    flag_anomalies,
    score_cases,
)


def test_sev_threshold_higher():
    assert _sev_threshold(40, 30, 35, True) == "critical"
    assert _sev_threshold(32, 30, 35, True) == "warn"
    assert _sev_threshold(20, 30, 35, True) is None
    assert _sev_threshold(np.nan, 30, 35, True) is None


def test_sev_threshold_lower():
    assert _sev_threshold(85, 92, 88, False) == "critical"
    assert _sev_threshold(90, 92, 88, False) == "warn"
    assert _sev_threshold(98, 92, 88, False) is None


def _mini_ts(thresholds) -> pd.DataFrame:
    n = 20
    t0 = pd.Timestamp("2016-01-01 08:00:00")
    df = pd.DataFrame(
        {
            "PID": ["x"] * n,
            "Obs_time": [t0 + pd.Timedelta(minutes=i) for i in range(n)],
            "t_min": list(range(n)),
            "case_frac": [i / max(n - 1, 1) for i in range(n)],
            "Age": [50] * n,
            "IBW_kg": [70.0] * n,
            "Agent": ["S"] * n,
            "Agent_Et": [2.0] * n,
            "Agent_Fi": [2.2] * n,
            "TV": [500] * n,
            "RR": [12] * n,
            "PEEP": [5.0] * n,
            "PIP": [20 + i for i in range(n)],  # rising
            "ETCO2": [40.0] * 10 + [55.0] * 10,
            "FIO2": [50] * n,
            "ETN2O": [0.0] * n,
            "HR": [80] * n,
            "SPO2": [99] * n,
            "nMAP": [80] * n,
            "case_duration_min": [n] * n,
        }
    )
    return add_features(df, thresholds)


def test_flag_pip_high_and_etco2(thresholds):
    ts = _mini_ts(thresholds)
    flags = flag_anomalies(ts, thresholds)
    assert not flags.empty
    assert "pip_high" in set(flags["rule_id"])
    assert "etco2_high" in set(flags["rule_id"])
    assert set(flags["severity"]).issubset({"info", "warn", "critical"})


def test_score_cases_non_negative(thresholds):
    ts = _mini_ts(thresholds)
    flags = flag_anomalies(ts, thresholds)
    scores = score_cases(flags, thresholds)
    assert (scores["anomaly_score"] >= 0).all()
    assert "x" in set(scores["PID"])


def test_score_empty_flags(thresholds):
    empty = pd.DataFrame(
        columns=["PID", "Obs_time", "t_min", "rule_id", "severity", "value", "message"]
    )
    scores = score_cases(empty, thresholds)
    assert list(scores.columns) == [
        "PID",
        "n_info",
        "n_warn",
        "n_critical",
        "n_composite",
        "anomaly_score",
        "top_rules",
    ]
    assert len(scores) == 0


def test_collapse_episodes_contiguous(thresholds):
    ts = _mini_ts(thresholds)
    flags = flag_anomalies(ts, thresholds)
    eps = collapse_episodes(flags)
    if not eps.empty:
        assert (eps["t_end_min"] >= eps["t_start_min"]).all()
        assert (eps["duration_min"] >= 1).all()


def test_pipeline_flags_on_synthetic(pipeline_result):
    flags = pipeline_result["flags"]
    # caseA engineered to trip several rules
    a = flags[flags["PID"] == "caseA"]
    assert len(a) > 0
    rules = set(a["rule_id"])
    assert "etco2_high" in rules or "pip_high" in rules or "pip_rising" in rules
