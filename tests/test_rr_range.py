"""RR keep range: rr_low critical (RR <= 4) is reachable across RR 1-4."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.pipeline.clean import clean_ventilator
from src.pipeline.features import add_features
from src.pipeline.flags import flag_anomalies

T0 = pd.Timestamp("2016-01-01 08:00:00")


def _t(m: float) -> pd.Timestamp:
    return T0 + pd.Timedelta(minutes=m)


def _vent_raw(rr_values: list[float], tv: float = 500.0) -> pd.DataFrame:
    n = len(rr_values)
    return pd.DataFrame({
        "PID": ["x"] * n,
        "Obs_time": [_t(m) for m in range(n)],
        "TV": [tv] * n,
        "RR": rr_values,
        "PEEP": [5.0] * n,
        "PIP": [18.0] * n,
        "ETCO2": [38.0] * n,
        "FIO2": [50.0] * n,
    })


def test_rr_range_keeps_rates_below_four(thresholds):
    assert thresholds["clean_ranges"]["RR"][0] <= 1
    vent = clean_ventilator(_vent_raw([0, 1, 2, 3, 4, 5, 40, 41]), thresholds)
    rr = vent["RR"].tolist()
    assert np.isnan(rr[0])                     # 0 = no rate recorded, still blanked
    assert rr[1:7] == [1.0, 2.0, 3.0, 4.0, 5.0, 40.0]
    assert np.isnan(rr[7])                     # above the keep range


@pytest.mark.parametrize("rr", [1, 2, 3, 4])
def test_rr_low_is_critical_across_one_to_four(thresholds, rr):
    vent = clean_ventilator(_vent_raw([rr] * 10), thresholds)
    ts = vent.assign(t_min=np.arange(10, dtype=float), Age=50.0, IBW_kg=70.0, Agent="S")
    flags = flag_anomalies(add_features(ts, thresholds), thresholds)
    low = flags[flags["rule_id"] == "rr_low"]
    assert len(low) == 10
    assert set(low["severity"]) == {"critical"}


def test_rr_low_warns_at_five_and_six_and_needs_delivered_volume(thresholds):
    vent = clean_ventilator(_vent_raw([5, 6, 7, 2], tv=500.0), thresholds)
    ts = vent.assign(t_min=np.arange(4, dtype=float), Age=50.0, IBW_kg=70.0, Agent="S")
    low = flag_anomalies(add_features(ts, thresholds), thresholds)
    low = low[low["rule_id"] == "rr_low"].sort_values("Obs_time")
    assert low["severity"].tolist() == ["warn", "warn", "critical"]
    # RR 2 with no delivered tidal volume is not a ventilation finding
    vent = clean_ventilator(_vent_raw([2] * 5, tv=60.0), thresholds)
    ts = vent.assign(t_min=np.arange(5, dtype=float), Age=50.0, IBW_kg=70.0, Agent="S")
    assert "rr_low" not in set(flag_anomalies(add_features(ts, thresholds), thresholds)["rule_id"])


def test_low_rr_does_not_make_other_rr_gated_rules_fire(thresholds):
    # RR 1-5 is below every min_rr gate, so it is not "likely mechanical ventilation"
    # and does not trigger the zero-ETCO2, zero-PEEP or room-air rules either
    vent = clean_ventilator(_vent_raw([3] * 20).assign(ETCO2=0.0, PEEP=0.0, FIO2=21.0), thresholds)
    ts = vent.assign(t_min=np.arange(20, dtype=float), Age=50.0, IBW_kg=70.0, Agent="S")
    out = add_features(ts, thresholds)
    assert not out["likely_mech_vent"].any()
    rules = set(flag_anomalies(out, thresholds)["rule_id"])
    assert rules.isdisjoint({"etco2_zero_vent", "peep_zero_long", "fio2_room_air_vent"})
    assert "rr_low" in rules
