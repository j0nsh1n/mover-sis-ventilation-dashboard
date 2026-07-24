"""Merge and feature derivation."""

from __future__ import annotations

import pandas as pd
import pytest

from src.pipeline.clean import clean_case_info, clean_ventilator, clean_vitals
from src.pipeline.features import add_features, age_adjusted_mac
from src.pipeline.load import load_case_info, load_ventilator, load_vitals
from src.pipeline.merge import merge_vent_vitals


def test_age_adjusted_mac_decreases_with_age(thresholds):
    mac40 = age_adjusted_mac("S", 40, thresholds)
    mac80 = age_adjusted_mac("S", 80, thresholds)
    assert mac40 > mac80 > 0
    assert pd.isna(age_adjusted_mac("N", 40, thresholds))


def test_merge_minute_alignment(synthetic_emr, thresholds):
    cases = clean_case_info(load_case_info(synthetic_emr))
    vent = clean_ventilator(load_ventilator(synthetic_emr), thresholds)
    vitals = clean_vitals(load_vitals(synthetic_emr), thresholds)
    ts = merge_vent_vitals(vent, vitals, cases)
    assert not ts.empty
    assert ts.duplicated(subset=["PID", "Obs_time"]).sum() == 0
    assert "TV" in ts.columns and "HR" in ts.columns
    assert "t_min" in ts.columns
    assert (ts.groupby("PID")["Obs_time"].is_monotonic_increasing).all() or True
    for _, g in ts.groupby("PID"):
        assert g["Obs_time"].is_monotonic_increasing


def test_features_tv_mlkg_and_mac(synthetic_emr, thresholds):
    cases = clean_case_info(load_case_info(synthetic_emr))
    vent = clean_ventilator(load_ventilator(synthetic_emr), thresholds)
    vitals = clean_vitals(load_vitals(synthetic_emr), thresholds)
    ts = add_features(merge_vent_vitals(vent, vitals, cases), thresholds)
    assert "TV_mlkg" in ts.columns
    assert ts["TV_mlkg"].notna().any()
    assert "MAC_Et" in ts.columns
    assert ts.loc[ts["Agent"] == "S", "MAC_Et"].notna().any()
