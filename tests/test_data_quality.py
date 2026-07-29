"""Data-quality gating: implausible anthropometrics must not drive weight-normalised rules."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.pipeline.clean import (
    PLAUSIBLE_HT_CM,
    anthropometric_problems,
    clean_case_info,
    ideal_body_weight_kg,
)


def _case(ht, wt, pid="p1"):
    return pd.DataFrame(
        [{"PID": pid, "Age": 55, "Ht": ht, "Wt": wt, "Gender": "F",
          "OR_start": "2016-06-01 08:00:00", "OR_end": "2016-06-01 10:00:00",
          "Surgery_start": "2016-06-01 08:15:00", "Surgery_end": "2016-06-01 09:45:00"}]
    )


def test_ibw_is_none_for_implausible_height():
    """130 cm produced a positive ~16 kg IBW, inflating mL/kg into critical flags."""
    assert np.isnan(ideal_body_weight_kg(130.0, "F"))
    assert np.isnan(ideal_body_weight_kg(0.0, "F"))
    assert np.isnan(ideal_body_weight_kg(254.0, "F"))
    assert ideal_body_weight_kg(165.0, "F") == pytest.approx(45.5 + 2.3 * (165 / 2.54 - 60))


def test_plausible_height_still_computes():
    for ht in (PLAUSIBLE_HT_CM[0], 170.0, PLAUSIBLE_HT_CM[1]):
        assert not np.isnan(ideal_body_weight_kg(ht, "M"))


def test_problem_reasons_are_specific():
    assert anthropometric_problems(165.0, 70.0) == []
    assert "height missing" in anthropometric_problems(0, 70)[0]
    assert "weight missing" in anthropometric_problems(165, 0)[0]
    assert "outside" in anthropometric_problems(254, 70)[0]
    assert "outside" in anthropometric_problems(165, 839)[0]


def test_clean_case_info_flags_and_nulls_ibw():
    out = clean_case_info(_case(130.0, 70.0))
    assert out["dq_anthropometrics_ok"].iloc[0] is False or not out["dq_anthropometrics_ok"].iloc[0]
    assert "height" in out["dq_reasons"].iloc[0]
    assert pd.isna(out["IBW_kg"].iloc[0])

    ok = clean_case_info(_case(165.0, 70.0))
    assert ok["dq_anthropometrics_ok"].iloc[0]
    assert ok["dq_reasons"].iloc[0] == ""
    assert ok["IBW_kg"].iloc[0] > 0


def test_weight_normalised_flags_cannot_fire_without_ibw():
    """End-to-end: no IBW means TV_mlkg is NaN, so tv_high_mlkg must not fire."""
    from src.config import load_thresholds
    from src.pipeline.features import add_features
    from src.pipeline.flags import flag_anomalies

    ts = pd.DataFrame(
        {
            "PID": ["p1"] * 5,
            "Obs_time": pd.date_range("2016-06-01 08:00", periods=5, freq="1min"),
            "t_min": range(5),
            "TV": [900.0] * 5,          # absurdly high per kg for any adult
            "PIP": [20.0] * 5,
            "PEEP": [5.0] * 5,
            "ETCO2": [38.0] * 5,
            "IBW_kg": [np.nan] * 5,     # gated out
            "Agent": ["S"] * 5,
            "Agent_Et": [2.0] * 5,
            "SPO2": [99.0] * 5,
        }
    )
    feats = add_features(ts)
    assert feats["TV_mlkg"].isna().all()
    flags = flag_anomalies(feats, load_thresholds("default"))
    assert "tv_high_mlkg" not in set(flags.get("rule_id", pd.Series(dtype=str)))
