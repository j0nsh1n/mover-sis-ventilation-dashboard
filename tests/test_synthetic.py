"""Synthetic SIS generator: quirks, determinism, and flag recall end to end."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from src.pipeline.load import load_ventilator
from src.pipeline.synthetic import generate_synthetic_emr


@pytest.fixture(scope="module")
def synthetic_run(tmp_path_factory):
    from src.pipeline.run import run_pipeline

    root = tmp_path_factory.mktemp("syn")
    emr = generate_synthetic_emr(root / "EMR", n_cases=40, seed=3)
    with pytest.MonkeyPatch.context() as mp:
        # Allow writing processed artifacts under pytest tmp (outside repo)
        mp.setenv("MOVER_ALLOW_EXTERNAL_OUTPUT", "1")
        result = run_pipeline(
            emr_dir=emr,
            output_dir=root / "processed",
            n_cases=40,
            write_sample_csv=False,
            validate=True,
        )
    truth = json.loads((emr / "synthetic_truth.json").read_text())
    return emr, result, truth


def test_generator_is_deterministic(tmp_path):
    a = generate_synthetic_emr(tmp_path / "a", n_cases=3, seed=5)
    b = generate_synthetic_emr(tmp_path / "b", n_cases=3, seed=5)
    for name in ("patient_information.csv", "patient_ventilator.csv", "patient_vitals.csv"):
        assert (a / name).read_text() == (b / name).read_text()


def test_generator_reproduces_sis_quirks(tmp_path):
    emr = generate_synthetic_emr(tmp_path / "EMR", n_cases=10, seed=1)
    vent_text = (emr / "patient_ventilator.csv").read_text()
    header = vent_text.splitlines()[0].split(",")
    assert {"ETC02", "FI02", "ETN20"} <= set(header)
    vitals_text = (emr / "patient_vitals.csv").read_text()
    assert "\\N" in vitals_text  # sparse NIBP
    assert "SP02" in vitals_text.splitlines()[0]
    times = pd.read_csv(emr / "patient_vitals.csv")["Obs_time"].astype(str)
    assert times.str.contains("/").any() and times.str.contains("-").any()
    # Loader renames typos and reads \N as NaN
    vent = load_ventilator(emr)
    assert "ETCO2" in vent.columns and "ETC02" not in vent.columns


def test_vent_row_cap_truncates_like_the_export(tmp_path):
    emr = generate_synthetic_emr(tmp_path / "EMR", n_cases=5, seed=2, vent_row_cap=300)
    assert len(pd.read_csv(emr / "patient_ventilator.csv")) == 300
    truth = json.loads((emr / "synthetic_truth.json").read_text())
    assert any(c["vent_truncated"] for c in truth["cases"].values())


def test_every_injected_anomaly_is_flagged(synthetic_run):
    _, result, truth = synthetic_run
    flags = result["flags"]
    missed = []
    n_anomalies = 0
    for pid, case in truth["cases"].items():
        f = flags[flags["PID"] == pid]
        for a in case["anomalies"]:
            n_anomalies += 1
            lo = pd.Timestamp(a["start"]) - pd.Timedelta(minutes=2)
            hi = pd.Timestamp(a["end"]) + pd.Timedelta(minutes=3)
            got = set(f.loc[f["Obs_time"].between(lo, hi), "rule_id"])
            missed += [(pid, a["kind"], r) for r in a["expected_rules"] if r not in got]
    assert n_anomalies >= 10
    assert missed == []


def test_clean_cases_raise_no_warn_or_critical(synthetic_run):
    _, result, truth = synthetic_run
    flags = result["flags"]
    clean = [
        pid
        for pid, c in truth["cases"].items()
        if c["ventilated"] and not c["anomalies"] and not c["vent_truncated"]
    ]
    assert clean
    bad = flags[flags["PID"].isin(clean) & flags["severity"].isin(["warn", "critical"])]
    assert bad.empty, bad.groupby("PID")["rule_id"].unique().to_dict()


def test_cases_table_has_per_hour_score(synthetic_run):
    _, result, _ = synthetic_run
    cases = result["cases"]
    assert "anomaly_score_per_hour" in cases.columns
    assert (cases["n_minutes"] > 0).all()
    expected = (cases["anomaly_score"] / (cases["n_minutes"] / 60)).round(2)
    assert (cases["anomaly_score_per_hour"] - expected).abs().max() < 1e-9
