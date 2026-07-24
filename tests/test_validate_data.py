"""DataFrame invariant validators."""

from __future__ import annotations

import pandas as pd
import pytest

from src.guardrails.exceptions import DataValidationError
from src.guardrails.validate_data import (
    assert_case_summary,
    assert_flags,
    assert_timeseries,
)


def test_timeseries_requires_sort():
    df = pd.DataFrame(
        {
            "PID": ["a", "a"],
            "Obs_time": pd.to_datetime(["2016-01-01 10:01:00", "2016-01-01 10:00:00"]),
            "t_min": [1, 0],
        }
    )
    with pytest.raises(DataValidationError, match="not sorted"):
        assert_timeseries(df)


def test_timeseries_rejects_dup_times():
    t = pd.Timestamp("2016-01-01 10:00:00")
    df = pd.DataFrame(
        {
            "PID": ["a", "a"],
            "Obs_time": [t, t],
            "t_min": [0, 0],
        }
    )
    with pytest.raises(DataValidationError, match="duplicate"):
        assert_timeseries(df)


def test_flags_invalid_severity():
    df = pd.DataFrame(
        [
            {
                "PID": "a",
                "Obs_time": pd.Timestamp("2016-01-01"),
                "t_min": 0,
                "rule_id": "x",
                "severity": "fatal",
                "value": 1,
                "message": "nope",
            }
        ]
    )
    with pytest.raises(DataValidationError, match="severit"):
        assert_flags(df)


def test_case_summary_negative_score():
    df = pd.DataFrame(
        [{"PID": "a", "anomaly_score": -1, "n_warn": 0, "n_critical": 0}]
    )
    with pytest.raises(DataValidationError, match="negative"):
        assert_case_summary(df)


def test_valid_outputs_pass(pipeline_result):
    assert_timeseries(pipeline_result["timeseries"])
    assert_case_summary(pipeline_result["cases"])
    assert_flags(pipeline_result["flags"], allow_empty=True)
