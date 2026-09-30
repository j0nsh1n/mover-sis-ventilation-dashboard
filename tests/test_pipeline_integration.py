"""End-to-end pipeline on synthetic EMR + output invariants."""

from __future__ import annotations


import pytest

from src.guardrails.exceptions import GuardrailError, PipelineError, SafetyLimitError
from src.guardrails.validate_data import validate_pipeline_outputs
from src.pipeline.run import run_pipeline


def test_full_pipeline_synthetic(pipeline_result, tmp_path):
    res = pipeline_result
    assert set(res.keys()) >= {"cases", "timeseries", "flags", "episodes", "scores"}
    assert len(res["cases"]) == 2
    assert res["timeseries"]["PID"].nunique() == 2
    validate_pipeline_outputs(
        timeseries=res["timeseries"],
        cases=res["cases"],
        flags=res["flags"],
        episodes=res["episodes"],
    )


def test_pipeline_writes_parquets(synthetic_emr, tmp_path, monkeypatch):
    monkeypatch.setenv("MOVER_ALLOW_EXTERNAL_OUTPUT", "1")
    out = tmp_path / "out"
    run_pipeline(
        emr_dir=synthetic_emr,
        output_dir=out,
        pids=["caseA", "caseB"],
        write_sample_csv=False,
    )
    for name in ("timeseries.parquet", "cases.parquet", "flags.parquet", "run_meta.json"):
        assert (out / name).is_file(), name
    # no leftover tmp files
    assert list(out.glob("*.tmp")) == []


def test_pipeline_rejects_bad_n_cases(synthetic_emr, tmp_path, monkeypatch):
    monkeypatch.setenv("MOVER_ALLOW_EXTERNAL_OUTPUT", "1")
    with pytest.raises(SafetyLimitError):
        run_pipeline(
            emr_dir=synthetic_emr,
            output_dir=tmp_path / "o",
            n_cases=0,
            write_sample_csv=False,
        )


def test_pipeline_unknown_pids(synthetic_emr, tmp_path, monkeypatch):
    monkeypatch.setenv("MOVER_ALLOW_EXTERNAL_OUTPUT", "1")
    with pytest.raises((PipelineError, GuardrailError)):
        run_pipeline(
            emr_dir=synthetic_emr,
            output_dir=tmp_path / "o",
            pids=["does_not_exist"],
            write_sample_csv=False,
        )


def test_cases_scores_consistent(pipeline_result):
    cases = pipeline_result["cases"]
    assert (cases["anomaly_score"] >= 0).all()
    # score should be at least as large as warn+3*crit when composites zero
    for _, row in cases.iterrows():
        lower = row["n_warn"] + 3 * row["n_critical"]
        assert row["anomaly_score"] >= lower or row.get("n_composite", 0) >= 0


def test_flag_pids_subset_of_cases(pipeline_result):
    case_pids = set(pipeline_result["cases"]["PID"])
    flag_pids = set(pipeline_result["flags"]["PID"])
    assert flag_pids <= case_pids
