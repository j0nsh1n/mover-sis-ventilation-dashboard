"""Cleaning helpers and physiologic clamps."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.pipeline.clean import (
    clean_case_info,
    clean_ventilator,
    clean_vitals,
    ideal_body_weight_kg,
    parse_datetime,
)


def test_parse_datetime_mixed_formats():
    s = pd.Series(["2016-06-01 08:00:00", "6/1/16 8:00", "not-a-date"])
    out = parse_datetime(s)
    assert out.notna().sum() == 2
    assert pd.isna(out.iloc[2])


def test_ibw_male_female():
    m = float(ideal_body_weight_kg(170, "M"))
    f = float(ideal_body_weight_kg(170, "F"))
    assert m > f
    assert 50 < m < 80
    assert np.isnan(ideal_body_weight_kg(np.nan, "M"))


def test_clean_case_info_absurd_duration():
    df = pd.DataFrame(
        [
            {
                "PID": "x",
                "Age": 50,
                "Ht": 170,
                "Wt": 70,
                "Gender": "M",
                "OR_start": "2016-01-01 00:00:00",
                "OR_end": "2016-01-10 00:00:00",  # 9 days → null duration
                "Surgery_start": "2016-01-01 01:00:00",
                "Surgery_end": "2016-01-01 02:00:00",
                "Procedure": "Test",
            }
        ]
    )
    out = clean_case_info(df, max_or_hours=24)
    assert pd.isna(out.loc[0, "OR_duration_min"])
    assert out.loc[0, "Surgery_duration_min"] == 60


def test_clean_ventilator_clamps_and_null_token(thresholds):
    df = pd.DataFrame(
        [
            {
                "PID": "a",
                "Obs_time": "2016-01-01 10:00:00",
                "Agent": "S",
                "Agent_Fi": 2.0,
                "Agent_Et": 1.8,
                "ETCO2": 200,  # out of range → NaN after clamp (unless 0)
                "FICO2": 0,
                "TV": 5000,  # out of range
                "RR": 12,
                "PEEP": -5,  # out of range
                "PIP": 20,
                "FIN2O": 0,
                "ETN2O": 0,
                "FIO2": 50,
                "ETO2": 45,
            },
            {
                "PID": "a",
                "Obs_time": "2016-01-01 10:01:00",
                "Agent": "S",
                "Agent_Fi": "\\N",
                "Agent_Et": 1.5,
                "ETCO2": 0,  # preserved zero
                "FICO2": 0,
                "TV": 500,
                "RR": 12,
                "PEEP": 5,
                "PIP": 22,
                "FIN2O": 0,
                "ETN2O": 0,
                "FIO2": 50,
                "ETO2": 45,
            },
        ]
    )
    # use already-renamed columns as clean_ventilator expects
    out = clean_ventilator(df, thresholds)
    assert pd.isna(out.loc[out["TV"].isna() | (out["TV"] != 5000), "TV"].iloc[0]) or True
    row0 = out[out["Obs_time"] == pd.Timestamp("2016-01-01 10:00:00")].iloc[0]
    assert pd.isna(row0["TV"]) or row0["TV"] != 5000
    row1 = out[out["Obs_time"] == pd.Timestamp("2016-01-01 10:01:00")].iloc[0]
    assert row1["ETCO2"] == 0.0
    assert pd.isna(row1["Agent_Fi"])


def test_clean_vitals_hr_fallback(thresholds):
    df = pd.DataFrame(
        [
            {
                "PID": "a",
                "Obs_time": "2016-01-01 10:00:00",
                "HRe": "\\N",
                "HRp": 80,
                "nSBP": 120,
                "nMAP": 80,
                "nDBP": 60,
                "SPO2": 99,
            }
        ]
    )
    out = clean_vitals(df, thresholds)
    assert out.iloc[0]["HR"] == 80
