"""OR-stay data window (induction kept) and per-agent concentration caps."""

from __future__ import annotations

import copy

import numpy as np
import pandas as pd
import pytest

from src.guardrails.exceptions import ConfigValidationError
from src.guardrails.validate_config import validate_thresholds
from src.pipeline.clean import clean_case_info, clean_ventilator, filter_to_case_window
from src.pipeline.features import add_features
from src.pipeline.flags import flag_anomalies
from src.pipeline.merge import merge_vent_vitals


def _cases(or_start="2016-06-01 08:00", surgery_start="2016-06-01 08:30"):
    return clean_case_info(pd.DataFrame([{
        "PID": "c1", "Age": 40, "Ht": 175, "Wt": 80, "Gender": "M",
        "OR_start": or_start, "OR_end": "2016-06-01 10:30",
        "Surgery_start": surgery_start, "Surgery_end": "2016-06-01 10:00",
        "Procedure": "Test",
    }]))


def _vent(times: list[str], agent="S", et=2.0) -> pd.DataFrame:
    return pd.DataFrame({
        "PID": ["c1"] * len(times), "Obs_time": times, "Agent": agent,
        "Agent_Et": et, "Agent_Fi": et, "TV": 500, "RR": 12, "PIP": 18, "PEEP": 5,
        "ETCO2": 36, "FIO2": 50,
    })


def test_window_keeps_induction_before_incision(thresholds):
    cases = _cases()
    vent = clean_ventilator(_vent(["2016-06-01 08:10", "2016-06-01 09:00"]), thresholds)
    kept = filter_to_case_window(vent, cases)
    assert len(kept) == 2
    ts = merge_vent_vitals(kept, pd.DataFrame(), cases)
    # t_min stays anchored at incision: induction minutes are negative
    assert ts["t_min"].min() == -20.0


def test_window_still_drops_rows_outside_or_stay(thresholds):
    vent = clean_ventilator(_vent(["2016-06-01 07:00", "2016-06-01 11:00"]), thresholds)
    assert filter_to_case_window(vent, _cases()).empty


def test_or_times_not_enclosing_surgery_fall_back_to_surgery_window():
    # OR_start recorded after incision (timestamp shift): use the surgery window
    cases = _cases(or_start="2016-06-01 09:00")
    row = cases.iloc[0]
    assert row["window_start"] == row["case_start"]
    assert row["window_end"] == row["case_end"]


def test_desflurane_above_old_12pct_cap_is_kept(thresholds):
    vent = clean_ventilator(_vent(["2016-06-01 09:00"], agent="D", et=13.5), thresholds)
    assert vent.loc[0, "Agent_Et"] == 13.5


def test_sevoflurane_above_its_cap_is_blanked(thresholds):
    vent = clean_ventilator(_vent(["2016-06-01 09:00"], agent="S", et=13.5), thresholds)
    assert np.isnan(vent.loc[0, "Agent_Et"])


def test_desflurane_two_mac_raises_critical_agent_high(thresholds):
    times = [f"2016-06-01 09:{m:02d}" for m in range(5)]
    cases = _cases()
    vent = clean_ventilator(_vent(times, agent="D", et=6.6 * 2.05), thresholds)
    ts = add_features(merge_vent_vitals(vent, pd.DataFrame(), cases), thresholds)
    flags = flag_anomalies(ts, thresholds)
    agent_high = flags[flags["rule_id"] == "agent_high"]
    assert (agent_high["severity"] == "critical").all() and len(agent_high) == 5


def test_agent_cap_above_shared_range_rejected(thresholds):
    cfg = copy.deepcopy(thresholds)
    cfg["agent_clean_max"]["D"] = 25
    with pytest.raises(ConfigValidationError):
        validate_thresholds(cfg)
