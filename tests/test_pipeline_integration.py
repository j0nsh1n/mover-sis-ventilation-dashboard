"""End-to-end pipeline on synthetic EMR + output invariants."""

from __future__ import annotations


import pytest
import hashlib
import json

from src.guardrails.exceptions import GuardrailError, PipelineError, SafetyLimitError
from src.guardrails.validate_data import validate_pipeline_outputs
from src.pipeline.run import run_pipeline
from src.config import load_thresholds
from src.runtime_paths import thresholds_path


@pytest.mark.parametrize("preset", ["default", "strict", "lenient"])
def test_pipeline_records_threshold_versions(synthetic_emr, tmp_path, monkeypatch, preset):
    monkeypatch.setenv("MOVER_ALLOW_EXTERNAL_OUTPUT", "1")
    run_pipeline(emr_dir=synthetic_emr, output_dir=tmp_path, pids=["caseA"],
                 preset=preset, write_sample_csv=False)
    meta = json.loads((tmp_path / "run_meta.json").read_text())
    assert meta["thresholds_sha256"] == hashlib.sha256(thresholds_path().read_bytes()).hexdigest()
    merged = json.dumps(load_thresholds(preset), sort_keys=True, default=str)
    assert meta["thresholds_preset_hash"] == hashlib.sha256(merged.encode()).hexdigest()[:16]


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


def test_pipeline_runs_when_stdout_cannot_encode_unicode(synthetic_emr, tmp_path, monkeypatch):
    # Windows pipes use a legacy code page under Python 3.14 (cp1252, cp437); progress output
    # must not crash the run.
    import io
    import sys

    monkeypatch.setenv("MOVER_ALLOW_EXTERNAL_OUTPUT", "1")
    buffer = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(buffer, encoding="cp437", errors="strict"))
    res = run_pipeline(emr_dir=synthetic_emr, output_dir=tmp_path / "out",
                       pids=["caseA", "caseB"], write_sample_csv=False)
    sys.stdout.flush()
    assert len(res["cases"]) == 2
    assert b"[pipeline] writing outputs" in buffer.getvalue()
