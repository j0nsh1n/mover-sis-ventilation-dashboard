"""Aggregate-only profile: no identifiers, small-cell suppression, threshold shares."""

from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd
import pytest

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


def _write_events(tmp_path, n_cases: int, rare_cases: int = 3):
    rows = []
    for c in range(n_cases):
        pid = f"PID{c:03d}"
        rows += [
            {"PID": pid, "Event_time": T0 + pd.Timedelta(days=c), "Event_name": "Intubation"},
            {"PID": pid, "Event_time": T0 + pd.Timedelta(days=c, minutes=90), "Event_name": "Extubation"},
            {"PID": pid, "Event_time": T0 + pd.Timedelta(days=c, minutes=30), "Event_name": "Incision | skin"},
        ]
        if c < rare_cases:
            rows.append({"PID": pid, "Event_time": T0 + pd.Timedelta(days=c), "Event_name": "Rare oddity"})
    pd.DataFrame(rows).to_parquet(tmp_path / "events.parquet")


def test_profile_lists_event_names_with_counts_and_roles(tmp_path, thresholds):
    root = _write_processed(tmp_path, 12, lambda c, m: 20)
    _write_events(root, 12)
    p = profile_processed(root, thresholds)
    names = {n["name"]: n for n in p["events"]["names"]}
    assert names["Intubation"]["cases"] == 12 and names["Intubation"]["role"] == "intubation"
    assert names["Extubation"]["events"] == 12 and names["Extubation"]["role"] == "extubation"
    assert names["Incision | skin"]["role"] == ""
    assert p["events"]["cases_with_intubation_match"] == 12
    md = render_markdown(p)
    assert "## Procedure event names" in md
    assert "| Intubation | 12 | 12 | intubation |" in md
    assert "Incision \\| skin" in md


def test_rare_event_names_are_pooled_and_output_has_no_identifiers(tmp_path, thresholds):
    root = _write_processed(tmp_path, 12, lambda c, m: 20)
    _write_events(root, 12, rare_cases=3)
    p = profile_processed(root, thresholds)
    assert "Rare oddity" not in {n["name"] for n in p["events"]["names"]}
    assert p["events"]["pooled_names"] == 1
    out = json.dumps(p) + render_markdown(p)
    assert "Rare oddity" not in out
    assert "PID0" not in out and "2016-" not in out
    assert "fewer than 11 cases" in out


def test_profile_without_events_says_so(tmp_path, thresholds):
    root = _write_processed(tmp_path, 12, lambda c, m: 20)
    p = profile_processed(root, thresholds)
    assert p["events"] == {}
    assert "No `events.parquet`" in render_markdown(p)


def test_profile_reports_phase_minutes_and_sources(tmp_path, thresholds):
    root = _write_processed(tmp_path, 12, lambda c, m: 20)
    ts = pd.read_parquet(root / "timeseries.parquet")
    ts["phase"] = np.where(ts["t_min"] < 5, "induction", "maintenance")
    ts["phase_source"] = "events"
    ts.to_parquet(root / "timeseries.parquet")
    cov = profile_processed(root, thresholds)["coverage"]
    assert cov["observed_minutes_by_phase"]["induction"] == 12 * 5
    assert cov["observed_minutes_by_phase"]["maintenance"] == 12 * 15
    assert cov["cases_by_phase_source"] == {"events": 12, "mixed": 0, "fallback": 0}


def test_header_records_preset_file_hash_case_count_and_seed(tmp_path, thresholds):
    import hashlib
    from src.runtime_paths import thresholds_path

    root = _write_processed(tmp_path, 12, lambda c, m: 20)
    profile = profile_processed(root, thresholds, seed=42)
    header = render_markdown(profile).split("## Coverage")[0]
    assert "Preset: default" in header
    assert hashlib.sha256(thresholds_path().read_bytes()).hexdigest() in header
    assert "Cases: 12. Seed: 42." in header
    assert profile_processed(root, thresholds)["metadata"]["seed"] == "unknown"
    (root / "run_meta.json").write_text(json.dumps({"seed": 517}))
    assert profile_processed(root, thresholds)["metadata"]["seed"] == 517


def test_threshold_percentages_respect_skipped_phases(tmp_path, thresholds):
    import copy

    root = _write_processed(tmp_path, 12, lambda c, m: 40 if m < 5 else 20)
    ts = pd.read_parquet(root / "timeseries.parquet")
    ts["phase"] = np.where(ts["t_min"] < 5, "induction", "maintenance")
    ts.to_parquet(root / "timeseries.parquet")
    before = profile_processed(root, thresholds)["rules"]["pip_high"]
    assert before["pct_minutes_beyond_warn"] == 25.0
    assert before["by_phase"]["induction"]["pct_minutes_beyond_warn"] == 100.0
    assert before["by_phase"]["maintenance"]["pct_minutes_beyond_warn"] == 0.0
    config = copy.deepcopy(thresholds)
    config["rules"]["pip_high"]["skip_phases"] = ["induction"]
    after = profile_processed(root, config)["rules"]["pip_high"]
    assert after["pct_minutes_beyond_warn"] == 0.0
    assert after["n_minutes"] == 180
    assert set(after["by_phase"]) == {"maintenance"}


def test_minutes_and_gap_runs_are_suppressed_below_eleven_cases(tmp_path, thresholds):
    root = _write_processed(tmp_path, 10, lambda c, m: 35, vent_gap=True)
    profile = profile_processed(root, thresholds)
    assert profile["coverage"]["n_cases"] == "<11"
    assert profile["coverage"]["grid_minutes"] is None
    assert profile["coverage"]["observed_minutes"] is None
    assert profile["coverage"]["vent_gap_runs"] is None
    assert profile["coverage"]["vent_gap_run_max_min"] is None
    assert profile["signals"]["PIP"]["n_minutes"] is None
    assert profile["rules"]["pip_high"]["flagged_minutes"] is None
    assert profile["rules"]["pip_high"]["flagged_minutes_per_hour"] is None
    assert profile["rules"]["pip_high"]["pct_minutes_beyond_warn"] is None


def test_rare_rule_minutes_and_gap_runs_are_suppressed_in_large_cohort(tmp_path, thresholds):
    root = _write_processed(tmp_path, 12, lambda c, m: 35 if c == 0 else 20)
    ts = pd.read_parquet(root / "timeseries.parquet")
    ts.loc[ts["PID"].eq("PID000") & ts["t_min"].between(5, 16), ["PIP", "TV"]] = np.nan
    ts.to_parquet(root / "timeseries.parquet")
    profile = profile_processed(root, thresholds)
    assert profile["coverage"]["grid_minutes"] == 240
    assert profile["coverage"]["vent_gap_runs"] is None
    assert profile["rules"]["pip_high"]["flagged_minutes"] is None
    assert profile["rules"]["pip_high"]["pct_minutes_beyond_warn"] is None


def test_diff_limits_changes_to_changed_rule_and_reports_hashes(tmp_path, monkeypatch, capsys):
    import yaml
    import src.config as config
    import src.rules as rules

    root = _write_processed(tmp_path, 12, lambda c, m: 20 + m)
    ts = pd.read_parquet(root / "timeseries.parquet")
    ts["phase"] = np.where(ts["t_min"] < 5, "induction", "maintenance")
    ts.to_parquet(root / "timeseries.parquet")
    path = tmp_path / "thresholds.yaml"
    path.write_bytes(config.THRESHOLDS_PATH.read_bytes())
    monkeypatch.setattr(config, "_thresholds_file", lambda: path)
    monkeypatch.setattr(rules, "thresholds_path", lambda: path)
    before = profile_processed(root, config.load_thresholds())
    data = yaml.safe_load(path.read_text())
    data["rules"]["pip_high"]["warn"] = 31
    path.write_text(yaml.safe_dump(data))
    after = profile_processed(root, config.load_thresholds())
    for rule in before["rules"]:
        assert (before["rules"][rule] != after["rules"][rule]) == (rule == "pip_high")
    left, right = tmp_path / "before.json", tmp_path / "after.json"
    left.write_text(json.dumps(before))
    right.write_text(json.dumps(after))
    main(["--diff", str(left), str(right)])
    output = capsys.readouterr().out
    assert "Thresholds hashes differ" in output
    assert "| pip_high | maintenance | pct_minutes_beyond_warn |" in output
    assert "| pip_high | all eligible | warn | 30 | 31 | 1 |" in output
    assert "| hr_high |" not in output


def test_event_names_cannot_leak_synthetic_identifiers_or_timestamps(tmp_path, thresholds):
    root = _write_processed(tmp_path, 12, lambda c, m: 20)
    pd.DataFrame([
        {"PID": f"PID{c:03d}", "Event_name": "SYN0000000001 at 2016-01-01 08:30:00"}
        for c in range(12)
    ]).to_parquet(root / "events.parquet")
    profile = profile_processed(root, thresholds)
    output = json.dumps(profile) + render_markdown(profile)
    assert "SYN0000000001" not in output
    assert "2016-01-01" not in output
    assert "08:30:00" not in output


@pytest.mark.skipif(sys.platform == "win32", reason="scripts/calibrate.sh is a bash script for Linux")
def test_calibrate_names_missing_emr_path(tmp_path):
    import subprocess
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "scripts" / "calibrate.sh"
    missing = tmp_path / "missing EMR"
    run = subprocess.run(["bash", str(script), str(missing), "missing-test"], text=True, capture_output=True)
    assert run.returncode != 0
    assert str(missing) in run.stderr
