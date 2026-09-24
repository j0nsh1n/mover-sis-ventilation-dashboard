"""Flag windows count real minutes, scoring counts each minute once, total MAC."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.pipeline.features import add_features, build_case_summary
from src.pipeline.flags import flag_anomalies, score_cases
from src.pipeline.load import sample_pids_with_ventilator
from src.pipeline.merge import MAX_GRID_MINUTES, fill_minute_grid

T0 = pd.Timestamp("2016-01-01 08:00:00")


def _case(minutes: list[int], **signals) -> pd.DataFrame:
    n = len(minutes)
    span = max(max(minutes), 1)
    base = {
        "PID": ["x"] * n,
        "Obs_time": [T0 + pd.Timedelta(minutes=m) for m in minutes],
        "t_min": [float(m) for m in minutes],
        "case_duration_min": [float(span)] * n,
        "case_frac": [m / span for m in minutes],
        "Age": [50.0] * n,
        "IBW_kg": [70.0] * n,
        "Agent": ["S"] * n,
        "TV": [500.0] * n,
        "RR": [12.0] * n,
        "PIP": [18.0] * n,
        "PEEP": [5.0] * n,
    }
    for k, v in signals.items():
        base[k] = v if isinstance(v, list) else [v] * n
    return pd.DataFrame(base)


def test_fill_minute_grid_inserts_gap_minutes_with_metadata():
    ts = _case([0, 1, 5], PIP=[20.0, 21.0, 22.0])
    out = fill_minute_grid(ts)
    assert len(out) == 6
    assert list(out["t_min"]) == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    gap = out[out["t_min"].between(2, 4)]
    assert gap["PIP"].isna().all()
    assert (gap["Age"] == 50.0).all()
    assert (gap["PID"] == "x").all()


def test_fill_minute_grid_is_idempotent_and_skips_misaligned():
    ts = _case([0, 3], PIP=20.0)
    once = fill_minute_grid(ts)
    assert fill_minute_grid(once).equals(once)
    off = ts.copy()
    off.loc[1, "Obs_time"] = off.loc[1, "Obs_time"] + pd.Timedelta(seconds=30)
    assert fill_minute_grid(off) is off


def test_fill_minute_grid_leaves_overlong_case():
    ts = _case([0, MAX_GRID_MINUTES + 10], PIP=20.0)
    assert len(fill_minute_grid(ts)) == 2


def test_pip_slope_is_per_minute_across_gaps(thresholds):
    # PIP rises 0.05 cmH2O/min, sampled every 20 minutes
    minutes = [20 * i for i in range(6)]
    ts = _case(minutes, PIP=[20.0 + i for i in range(6)])
    out = add_features(ts, thresholds)
    assert out["PIP_slope"].dropna().empty or (out["PIP_slope"].dropna() < 0.5).all()
    flags = flag_anomalies(out, thresholds)
    assert "pip_rising" not in set(flags["rule_id"])


def test_pip_slope_still_detects_dense_rise(thresholds):
    ts = _case(list(range(20)), PIP=[20.0 + i for i in range(20)])
    out = add_features(ts, thresholds)
    assert np.isclose(out["PIP_slope"].dropna().iloc[-1], 1.0)
    assert "pip_rising" in set(flag_anomalies(out, thresholds)["rule_id"])


def test_duration_rule_needs_real_minutes(thresholds):
    # 16 zero-PEEP samples, but 10 minutes apart: no 15-minute run exists
    minutes = [10 * i for i in range(16)]
    sparse = add_features(_case(minutes, PEEP=0.0), thresholds)
    assert "peep_zero_long" not in set(flag_anomalies(sparse, thresholds)["rule_id"])
    dense = add_features(_case(list(range(16)), PEEP=0.0), thresholds)
    assert "peep_zero_long" in set(flag_anomalies(dense, thresholds)["rule_id"])


def _flags(rows: list[tuple[int, str, str]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "PID": ["x"] * len(rows),
            "Obs_time": [T0 + pd.Timedelta(minutes=m) for m, _, _ in rows],
            "t_min": [float(m) for m, _, _ in rows],
            "rule_id": [r for _, r, _ in rows],
            "severity": [s for _, _, s in rows],
            "value": [1.0] * len(rows),
            "message": ["m"] * len(rows),
        }
    )


def test_score_counts_each_minute_once_at_worst_severity(thresholds):
    flags = _flags([(0, "pip_high", "warn"), (0, "spo2_low", "critical")])
    s = score_cases(flags, thresholds).iloc[0]
    assert s["n_warn"] == 1 and s["n_critical"] == 1
    assert s["anomaly_score"] == thresholds["scoring"]["critical_minute_weight"]


def test_score_weights_composites_per_episode(thresholds):
    run = [(m, "hypoventilation_pattern", "warn") for m in range(4)]
    flags = _flags(run + [(10, "hypoventilation_pattern", "warn")])
    s = score_cases(flags, thresholds).iloc[0]
    assert s["n_composite"] == 2
    sc = thresholds["scoring"]
    assert s["anomaly_score"] == 5 * sc["warn_minute_weight"] + 2 * sc["composite_episode_weight"]


def test_total_mac_adds_nitrous_oxide(thresholds):
    ts = _case(list(range(3)), Agent_Et=1.8, ETN2O=[0.0, 52.0, np.nan])
    ts["Age"] = 40.0
    out = add_features(ts, thresholds)
    assert np.allclose(out["MAC_total_Et"], [1.0, 1.5, 1.0])
    no_agent = add_features(_case([0], Agent="N", Agent_Et=np.nan, ETN2O=np.nan), thresholds)
    assert no_agent["MAC_total_Et"].isna().all()


def test_agent_high_uses_total_mac(thresholds):
    # 1.2 MAC volatile + 0.5 MAC N2O = 1.7 MAC >= warn 1.5
    ts = _case(list(range(3)), Agent_Et=1.2 * 1.8, ETN2O=52.0)
    ts["Age"] = 40.0
    flags = flag_anomalies(add_features(ts, thresholds), thresholds)
    assert "agent_high" in set(flags["rule_id"])


def test_case_summary_omits_missing_columns_and_counts_observed_minutes(thresholds):
    ts = add_features(_case([0, 1, 5], PIP=20.0), thresholds)
    ts = ts.drop(columns=["Age"])
    summary = build_case_summary(ts)
    assert "Age" not in summary.columns
    assert summary.loc[0, "n_minutes"] == 3


def test_sampling_scans_whole_ventilator_file(tmp_path):
    emr = tmp_path / "EMR"
    emr.mkdir()
    early = pd.DataFrame({"PID": ["early"] * 40, "Obs_time": "2016-01-01 08:00:00"})
    late = pd.DataFrame({"PID": ["late"] * 40, "Obs_time": "2016-01-01 08:00:00"})
    pd.concat([early, late]).to_csv(emr / "patient_ventilator.csv", index=False)
    pids = sample_pids_with_ventilator(emr, n=10, min_vent_rows=30)
    assert sorted(pids) == ["early", "late"]
    assert sample_pids_with_ventilator(emr, n=10, min_vent_rows=30, vent_nrows_scan=40) == [
        "early"
    ]
