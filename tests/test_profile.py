"""Aggregate-only profile: no identifiers, small-cell suppression, threshold shares."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from src.pipeline.profile import main, profile_processed, render_markdown

T0 = pd.Timestamp("2016-01-01 08:00:00")


def _write_processed(tmp_path, n_cases: int, pip_value, vent_gap: bool = False):
    rows = []
    for c in range(n_cases):
        for m in range(20):
            gap = vent_gap and 5 <= m < 17
            rows.append({
                "PID": f"PID{c:03d}",
                "Obs_time": T0 + pd.Timedelta(days=c, minutes=m),
                "t_min": float(m),
                "PIP": np.nan if gap else float(pip_value(c, m)),
                "TV": np.nan if gap else 500.0,
                "HR": 70.0,
                "SPO2": 98.0,
            })
    ts = pd.DataFrame(rows)
    flags = ts[ts["PIP"] >= 30][["PID", "Obs_time", "t_min"]].assign(
        rule_id="pip_high", severity="warn", value=30.0, message="High PIP"
    )
    ts.to_parquet(tmp_path / "timeseries.parquet")
    flags.to_parquet(tmp_path / "flags.parquet")
    return tmp_path


def test_profile_contains_no_pids_or_timestamps(tmp_path, thresholds):
    root = _write_processed(tmp_path, 12, lambda c, m: 20 + m)
    out = json.dumps(profile_processed(root, thresholds)) + render_markdown(
        profile_processed(root, thresholds)
    )
    assert "PID0" not in out
    assert "2016-" not in out


def test_threshold_share_matches_data(tmp_path, thresholds):
    # 12 cases x 20 minutes; PIP 20..39 -> 10/20 minutes >= 30, 5/20 >= 35
    root = _write_processed(tmp_path, 12, lambda c, m: 20 + m)
    rule = profile_processed(root, thresholds)["rules"]["pip_high"]
    assert rule["pct_minutes_beyond_warn"] == 50.0
    assert rule["pct_minutes_beyond_critical"] == 25.0
    assert rule["cases_flagged"] == 12
    assert rule["cases_flagged_pct"] == 100.0


def test_small_cells_are_suppressed(tmp_path, thresholds):
    # Only case 0 reaches PIP 30; 3 cases total -> distributions suppressed
    root = _write_processed(tmp_path, 3, lambda c, m: 35 if c == 0 else 20)
    p = profile_processed(root, thresholds, min_cell=11)
    assert p["rules"]["pip_high"]["cases_flagged"] == "<11"
    assert p["rules"]["pip_high"]["cases_flagged_pct"] is None
    assert p["signals"]["PIP"].get("suppressed") is True
    assert "p50" not in p["signals"]["PIP"]
    assert "suppressed" in render_markdown(p)


def test_vent_gaps_are_measured_separately_from_vitals(tmp_path, thresholds):
    root = _write_processed(tmp_path, 12, lambda c, m: 20, vent_gap=True)
    cov = profile_processed(root, thresholds)["coverage"]
    assert cov["vent_gap_runs"] == 12
    assert cov["vent_gap_run_max_min"] == 12
    assert cov["vent_cases_with_gap_ge_10_min"] == 12
    assert cov["vitals_gap_runs"] == 0


def test_cli_writes_markdown_and_json(tmp_path, thresholds):
    root = _write_processed(tmp_path, 12, lambda c, m: 20 + m)
    md, js = tmp_path / "p.md", tmp_path / "p.json"
    main(["--processed-dir", str(root), "--out", str(md), "--json", str(js)])
    assert md.read_text().startswith("# MOVER SIS pipeline profile")
    assert json.loads(js.read_text())["coverage"]["n_cases"] == 12
