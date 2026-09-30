"""Anesthesia phase labels, per-rule phase skips, flag/episode phase columns, config checks."""

from __future__ import annotations

import copy

import numpy as np
import pandas as pd
import pytest

from src.config import load_thresholds
from src.guardrails.exceptions import ConfigValidationError
from src.guardrails.validate_config import validate_thresholds
from src.pipeline.features import add_features
from src.pipeline.flags import collapse_episodes, flag_anomalies
from src.pipeline.phases import assign_phases, classify_event_names

T0 = pd.Timestamp("2016-01-01 08:00:00")
N = 120  # minutes 0..119
# Case layout: intubation at minute 10, incision at 25, surgery end at 95, extubation at 100
INTUB, INCISION, SURG_END, EXTUB = 10, 25, 95, 100


def _t(m: float) -> pd.Timestamp:
    return T0 + pd.Timedelta(minutes=m)


def _ts(pid: str = "x", n: int = N, **signals) -> pd.DataFrame:
    base = {
        "PID": [pid] * n,
        "Obs_time": [_t(m) for m in range(n)],
        "t_min": [float(m - INCISION) for m in range(n)],
        "case_start": [_t(INCISION)] * n,
        "case_end": [_t(SURG_END)] * n,
        "case_duration_min": [float(SURG_END - INCISION)] * n,
        "case_frac": [(m - INCISION) / (SURG_END - INCISION) for m in range(n)],
        "Age": [50.0] * n,
        "IBW_kg": [70.0] * n,
        "Agent": ["S"] * n,
        "TV": [500.0] * n,
        "RR": [12.0] * n,
        "PIP": [18.0] * n,
        "PEEP": [5.0] * n,
        "ETCO2": [38.0] * n,
        "FIO2": [50.0] * n,
        "HR": [75.0] * n,
        "SPO2": [99.0] * n,
        "nMAP": [80.0] * n,
    }
    for k, v in signals.items():
        base[k] = v if isinstance(v, list) else [v] * n
    return pd.DataFrame(base)


def _events(pid: str = "x", **named: float | list[float]) -> pd.DataFrame:
    rows = []
    for name, minutes in named.items():
        for m in minutes if isinstance(minutes, list) else [minutes]:
            rows.append({"PID": pid, "Event_time": _t(m), "Event_name": name.replace("_", " ")})
    return pd.DataFrame(rows, columns=["PID", "Event_time", "Event_name"])


def _phase_at(out: pd.DataFrame, minute: int) -> str:
    return out.loc[out["Obs_time"] == _t(minute), "phase"].iloc[0]


def _run(ts: pd.DataFrame, events: pd.DataFrame | None, thresholds: dict) -> pd.DataFrame:
    """assign_phases -> add_features -> flag_anomalies, as process_frames does."""
    return flag_anomalies(add_features(assign_phases(ts, events, thresholds), thresholds), thresholds)


# ---------------------------------------------------------------- phase assignment


def test_phases_from_events(thresholds):
    ev = _events(Intubation=INTUB, Incision=INCISION, Extubation=EXTUB)
    out = assign_phases(_ts(), ev, thresholds)
    cfg = thresholds["phases"]
    ind_end = INTUB + cfg["induction_after_min"]
    em_start = EXTUB - cfg["emergence_before_min"]
    assert _phase_at(out, 0) == "pre_induction"
    assert _phase_at(out, INTUB - 1) == "pre_induction"
    assert _phase_at(out, INTUB) == "induction"
    assert _phase_at(out, ind_end - 1) == "induction"
    assert _phase_at(out, ind_end) == "maintenance"
    assert _phase_at(out, em_start - 1) == "maintenance"
    assert _phase_at(out, em_start) == "emergence"
    assert _phase_at(out, EXTUB - 1) == "emergence"
    assert _phase_at(out, EXTUB) == "post_emergence"
    assert _phase_at(out, N - 1) == "post_emergence"
    assert set(out["phase_source"]) == {"events"}
    assert len(out) == N


def test_phases_fall_back_to_case_times_without_events(thresholds):
    # Ventilator data starts at minute 8: airway start proxy
    ts = _ts()
    ts.loc[ts.index < 8, ["TV", "RR", "PIP", "PEEP", "ETCO2", "FIO2"]] = np.nan
    out = assign_phases(ts, pd.DataFrame(columns=["PID", "Event_time", "Event_name"]), thresholds)
    assert _phase_at(out, 7) == "pre_induction"
    assert _phase_at(out, 8) == "induction"
    assert _phase_at(out, INCISION - 1) == "induction"
    assert _phase_at(out, INCISION) == "maintenance"
    assert _phase_at(out, SURG_END - 1) == "maintenance"
    assert _phase_at(out, SURG_END) == "emergence"
    assert _phase_at(out, N - 1) == "emergence"  # no extubation event: emergence runs to the end
    assert "post_emergence" not in set(out["phase"])
    assert set(out["phase_source"]) == {"fallback"}


def test_none_events_and_missing_pid_use_fallback(thresholds):
    out = assign_phases(_ts(), None, thresholds)
    assert set(out["phase_source"]) == {"fallback"}
    other = _events(pid="someone_else", Intubation=INTUB, Extubation=EXTUB)
    assert set(assign_phases(_ts(), other, thresholds)["phase_source"]) == {"fallback"}


def test_mixed_source_when_only_one_boundary_has_an_event(thresholds):
    out = assign_phases(_ts(), _events(Intubation=INTUB), thresholds)
    assert set(out["phase_source"]) == {"mixed"}
    assert _phase_at(out, INTUB) == "induction"
    assert _phase_at(out, SURG_END) == "emergence"  # fallback boundary
    out = assign_phases(_ts(), _events(Extubation=EXTUB), thresholds)
    assert set(out["phase_source"]) == {"mixed"}
    assert _phase_at(out, EXTUB) == "post_emergence"


def test_odd_event_names_are_matched_case_insensitively(thresholds):
    names = pd.Series([
        "INTUBATION", "  intubation complete ", "Re-Intubation", "Airway Secured",
        "Extubation", "EXTUBATED", "Incision", "", "Tubing change", "LMA removed",
    ])
    roles = classify_event_names(names, thresholds["phases"]).tolist()
    assert roles == [
        "intubation", "intubation", "intubation", "intubation",
        "extubation", "extubation", "", "", "", "extubation",
    ]
    ev = _events(**{"Endotracheal_INTUBATION": INTUB})  # substring, mixed case
    ev = pd.concat([ev, _events(**{"pt_extubated": EXTUB})])
    out = assign_phases(_ts(), ev, thresholds)
    assert set(out["phase_source"]) == {"events"}


def test_extubation_is_never_read_as_intubation(thresholds):
    roles = classify_event_names(pd.Series(["Extubation", "Intubation"]), thresholds["phases"])
    assert roles.tolist() == ["extubation", "intubation"]
    cfg = copy.deepcopy(thresholds["phases"])
    cfg["intubation_patterns"] = ["tubation"]  # also matches "Extubation"
    assert classify_event_names(pd.Series(["Extubation"]), cfg).tolist() == ["extubation"]


def test_multiple_intubations_use_earliest_and_latest_extubation(thresholds):
    ev = _events(Intubation=[INTUB, 50], Extubation=[60, EXTUB])
    out = assign_phases(_ts(), ev, thresholds)
    assert _phase_at(out, INTUB) == "induction"
    assert _phase_at(out, 50) == "maintenance"  # re-intubation does not restart induction
    assert _phase_at(out, 70) == "maintenance"  # earlier extubation does not start emergence
    assert _phase_at(out, EXTUB) == "post_emergence"


def test_unusable_events_are_ignored(thresholds):
    # Extubation before the intubation, and events days away from the case
    ev = _events(Intubation=INTUB, Extubation=[5])
    assert set(assign_phases(_ts(), ev, thresholds)["phase_source"]) == {"mixed"}
    far = _events(Intubation=[INTUB + 3 * 24 * 60], Extubation=[EXTUB + 3 * 24 * 60])
    assert set(assign_phases(_ts(), far, thresholds)["phase_source"]) == {"fallback"}


def test_phases_are_per_case_and_keep_row_order(thresholds):
    a, b = _ts("a"), _ts("b")
    ts = pd.concat([a, b], ignore_index=True)
    ev = pd.concat([_events("a", Intubation=INTUB, Extubation=EXTUB), _events("b", Intubation=30)])
    out = assign_phases(ts, ev, thresholds)
    assert (out["PID"].to_numpy() == ts["PID"].to_numpy()).all()
    assert (out["Obs_time"].to_numpy() == ts["Obs_time"].to_numpy()).all()
    assert set(out.loc[out["PID"] == "a", "phase_source"]) == {"events"}
    assert set(out.loc[out["PID"] == "b", "phase_source"]) == {"mixed"}
    assert _phase_at(out[out["PID"] == "b"], INTUB) == "pre_induction"


def test_short_case_keeps_boundaries_ordered(thresholds):
    # Extubation 4 minutes after intubation: induction still ends before emergence starts
    out = assign_phases(_ts(), _events(Intubation=10, Extubation=14), thresholds)
    order = {"pre_induction": 0, "induction": 1, "maintenance": 2, "emergence": 3, "post_emergence": 4}
    ranks = out["phase"].map(order).to_numpy()
    assert (np.diff(ranks) >= 0).all()


def test_without_phases_config_ts_is_unchanged(thresholds):
    cfg = {k: v for k, v in thresholds.items() if k != "phases"}
    ts = _ts()
    assert assign_phases(ts, _events(Intubation=INTUB), cfg) is ts


# ------------------------------------------------------------------- skip_phases


def _drift_signal() -> list[float]:
    """Sevoflurane wash-in over minutes 10-19 then steady; wash-out over 90-99."""
    et = np.full(N, 2.0)
    et[10:20] = np.linspace(0.2, 2.0, 10)
    et[90:100] = np.linspace(2.0, 0.2, 10)
    et[100:] = 0.0
    return et.tolist()


def test_agent_drift_is_not_raised_on_wash_in_and_wash_out(thresholds):
    ts = _ts(Agent_Et=_drift_signal(), Agent_Fi=_drift_signal())
    ev = _events(Intubation=INTUB, Incision=INCISION, Extubation=EXTUB)
    before = _run(ts, None, {k: v for k, v in thresholds.items() if k != "phases"})
    assert "agent_drift" in set(before["rule_id"])  # the old behavior: ramps are flagged
    after = _run(ts, ev, thresholds)
    assert "agent_drift" not in set(after["rule_id"])


def test_real_drift_in_maintenance_is_still_flagged(thresholds):
    et = np.full(N, 2.0)
    et[50:60] = 3.0  # a 1.0 vol% step for 10 minutes in the middle of maintenance
    ts = _ts(Agent_Et=et.tolist(), Agent_Fi=et.tolist())
    ev = _events(Intubation=INTUB, Incision=INCISION, Extubation=EXTUB)
    flags = _run(ts, ev, thresholds)
    drift = flags[flags["rule_id"] == "agent_drift"]
    assert not drift.empty
    assert set(drift["phase"]) == {"maintenance"}


def test_drift_window_restarts_at_phase_change(thresholds):
    # Steady 1.0 until induction ends, then a steady 2.0: without the restart the
    # rolling median still reads 1.0 for several maintenance minutes
    et = np.where(np.arange(N) < INTUB + 10, 1.0, 2.0)
    ts = _ts(Agent_Et=et.tolist(), Agent_Fi=et.tolist())
    ev = _events(Intubation=INTUB, Extubation=EXTUB)
    out = add_features(assign_phases(ts, ev, thresholds), thresholds)
    first_maint = out[out["phase"] == "maintenance"].iloc[:4]
    assert first_maint["Agent_Et_drift"].isna().all()
    plain = add_features(ts, thresholds)
    assert plain.loc[first_maint.index, "Agent_Et_drift"].max() > 0.5


def test_fio2_high_run_is_measured_over_active_phases_only(thresholds):
    # 100% O2 from minute 40 to the end: 80 minutes, but only 40-89 is maintenance
    fio2 = [50.0] * 40 + [100.0] * (N - 40)
    ts = _ts(FIO2=fio2)
    ev = _events(Intubation=INTUB, Extubation=EXTUB)
    flags = _run(ts, ev, thresholds)
    hit = flags[flags["rule_id"] == "fio2_high_long"]
    assert not hit.empty
    assert set(hit["phase"]) == {"maintenance"}
    assert hit["t_min"].max() < EXTUB - thresholds["phases"]["emergence_before_min"] - INCISION
    # Pre-oxygenation only (minutes 0-9, then 50%) is never flagged
    pre = _run(_ts(FIO2=[100.0] * 10 + [50.0] * (N - 10)), ev, thresholds)
    assert "fio2_high_long" not in set(pre["rule_id"])


def test_etco2_zero_vent_skipped_in_induction_but_not_maintenance(thresholds):
    etco2 = np.full(N, 38.0)
    etco2[11:15] = 0.0  # induction, tube not yet confirmed
    etco2[60:65] = 0.0  # maintenance: disconnect
    ts = _ts(ETCO2=etco2.tolist())
    flags = _run(ts, _events(Intubation=INTUB, Extubation=EXTUB), thresholds)
    hit = flags[flags["rule_id"] == "etco2_zero_vent"]
    assert set(hit["phase"]) == {"maintenance"}
    assert hit["Obs_time"].min() >= _t(60)
    no_events = _run(ts, None, {k: v for k, v in thresholds.items() if k != "phases"})
    assert (no_events["rule_id"] == "etco2_zero_vent").sum() == 9  # 4 + 5 without phase context


def test_ventilation_rules_skip_pre_induction_and_emergence(thresholds):
    tv = np.full(N, 500.0)
    tv[2:8] = 250.0    # hand-bagged before intubation
    tv[92:98] = 250.0  # weaning at emergence
    tv[50:53] = 250.0  # maintenance: should still flag
    ev = _events(Intubation=INTUB, Extubation=EXTUB)
    flags = _run(_ts(TV=tv.tolist()), ev, thresholds)
    low = flags[flags["rule_id"] == "tv_low_mlkg"]
    assert set(low["phase"]) == {"maintenance"}
    assert len(low) == 3


def test_safety_rules_are_raised_in_every_phase(thresholds):
    skip_all_defaults = [
        r for r, spec in thresholds["rules"].items() if spec.get("skip_phases")
    ]
    for safety in ("spo2_low", "hr_low", "hr_high", "map_low", "pip_high", "etco2_high", "agent_high"):
        assert safety not in skip_all_defaults
    spo2 = np.full(N, 99.0)
    spo2[12:16] = 85.0   # induction
    spo2[96:99] = 85.0   # emergence
    hr = np.full(N, 75.0)
    hr[11:14] = 38.0     # bradycardia during induction
    mapv = np.full(N, 80.0)
    mapv[13:17] = 45.0   # hypotension during induction
    ts = _ts(SPO2=spo2.tolist(), HR=hr.tolist(), nMAP=mapv.tolist())
    flags = _run(ts, _events(Intubation=INTUB, Extubation=EXTUB), thresholds)
    by_rule = flags.groupby("rule_id")["phase"].agg(lambda s: set(s))
    assert by_rule["spo2_low"] == {"induction", "emergence"}
    assert by_rule["hr_low"] == {"induction"}
    assert by_rule["map_low"] == {"induction"}
    sev = flags[(flags["rule_id"] == "spo2_low") & (flags["phase"] == "induction")]["severity"]
    assert (sev == "critical").all()


def test_desat_with_vent_issue_survives_in_induction(thresholds):
    spo2 = np.full(N, 99.0)
    spo2[12:16] = 85.0
    pip = np.full(N, 18.0)
    pip[12:16] = 36.0  # pip_high is on in every phase
    flags = _run(_ts(SPO2=spo2.tolist(), PIP=pip.tolist()), _events(Intubation=INTUB, Extubation=EXTUB), thresholds)
    comp = flags[flags["rule_id"] == "desat_with_vent_issue"]
    assert set(comp["phase"]) == {"induction"}


def test_skip_phases_is_configurable_per_rule(thresholds):
    cfg = copy.deepcopy(thresholds)
    cfg["rules"]["spo2_low"]["skip_phases"] = ["induction"]
    spo2 = np.full(N, 99.0)
    spo2[12:16] = 85.0
    spo2[60:63] = 85.0
    flags = _run(_ts(SPO2=spo2.tolist()), _events(Intubation=INTUB, Extubation=EXTUB), cfg)
    assert set(flags[flags["rule_id"] == "spo2_low"]["phase"]) == {"maintenance"}
    cfg["rules"]["spo2_low"]["skip_phases"] = []
    flags = _run(_ts(SPO2=spo2.tolist()), _events(Intubation=INTUB, Extubation=EXTUB), cfg)
    assert set(flags[flags["rule_id"] == "spo2_low"]["phase"]) == {"induction", "maintenance"}


def test_default_skip_lists_name_valid_phases_and_leave_safety_rules_alone(thresholds):
    valid = {"pre_induction", "induction", "maintenance", "emergence", "post_emergence"}
    for rid, spec in thresholds["rules"].items():
        assert set(spec.get("skip_phases", [])) <= valid, rid
        assert "maintenance" not in spec.get("skip_phases", []), rid
    for rid in ("spo2_low", "hr_low", "hr_high", "map_low", "pip_high", "peep_high", "etco2_high",
                "agent_high", "compliance_concern", "hypoventilation_pattern", "desat_with_vent_issue"):
        assert not thresholds["rules"][rid].get("skip_phases"), rid


def test_flags_without_phase_column_are_unaffected(thresholds):
    ts = _ts(ETCO2=[0.0] * N)
    flags = flag_anomalies(add_features(ts, thresholds), thresholds)
    assert "etco2_zero_vent" in set(flags["rule_id"])  # no phase labels: nothing is skipped
    assert flags["phase"].isna().all()


# ------------------------------------------------------------- flags and episodes


def test_flags_and_episodes_carry_phase(thresholds):
    etco2 = np.full(N, 38.0)
    etco2[6:14] = 55.0  # pre_induction/induction boundary at minute 10
    etco2[60:70] = 55.0
    flags = _run(_ts(ETCO2=etco2.tolist()), _events(Intubation=INTUB, Extubation=EXTUB), thresholds)
    hi = flags[flags["rule_id"] == "etco2_high"]
    assert hi.set_index("Obs_time").loc[_t(7), "phase"] == "pre_induction"
    assert hi.set_index("Obs_time").loc[_t(12), "phase"] == "induction"
    eps = collapse_episodes(flags)
    assert {"phase", "phase_end"} <= set(eps.columns)
    e = eps[eps["rule_id"] == "etco2_high"].sort_values("t_start_min").reset_index(drop=True)
    assert len(e) == 2
    assert (e.loc[0, "phase"], e.loc[0, "phase_end"]) == ("pre_induction", "induction")
    assert (e.loc[1, "phase"], e.loc[1, "phase_end"]) == ("maintenance", "maintenance")


def test_empty_flags_and_episodes_keep_phase_columns():
    empty = collapse_episodes(pd.DataFrame())
    assert {"phase", "phase_end"} <= set(empty.columns)
    assert "phase" in flag_anomalies(pd.DataFrame()).columns


# ------------------------------------------------------------------ config checks


def test_default_phases_config_is_valid(thresholds):
    validate_thresholds(copy.deepcopy(thresholds), preset="default")
    assert thresholds["phases"]["intubation_patterns"]
    assert thresholds["phases"]["extubation_patterns"]


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda c: c["rules"]["agent_drift"].update(skip_phases=["induction", "bogus"]), "unknown phase"),
        (lambda c: c["rules"]["agent_drift"].update(skip_phases="induction"), "list of phase names"),
        (lambda c: c["rules"]["agent_drift"].update(skip_phases=[1]), "list of phase names"),
        (lambda c: c["phases"].update(intubation_patterns="intubat"), "intubation_patterns"),
        (lambda c: c["phases"].update(extubation_patterns=[3]), "extubation_patterns"),
        (lambda c: c["phases"].update(extubation_patterns=[""]), "extubation_patterns"),
        (lambda c: c["phases"].update(intubation_patterns=["(unclosed"]), "regular expression"),
        (lambda c: c["phases"].update(induction_after_min=-1), "induction_after_min"),
        (lambda c: c["phases"].update(emergence_before_min="ten"), "emergence_before_min"),
        (lambda c: c["phases"].update(surprise=1), "unknown key"),
        (lambda c: c.update(phases=[]), "phases"),
    ],
)
def test_invalid_phase_config_is_rejected(mutate, match):
    cfg = load_thresholds("default", validate=False)
    cfg = copy.deepcopy(cfg)
    mutate(cfg)
    with pytest.raises(ConfigValidationError, match=match):
        validate_thresholds(cfg, preset="default")


def test_phases_section_is_optional():
    cfg = copy.deepcopy(load_thresholds("default", validate=False))
    del cfg["phases"]
    validate_thresholds(cfg, preset="default")


def test_presets_keep_skip_phases():
    strict = load_thresholds("strict")
    assert strict["rules"]["spo2_low"]["warn"] == 94
    assert strict["rules"]["agent_drift"]["skip_phases"]
