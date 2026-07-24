"""I/O path safety and parameter bounds."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.guardrails.exceptions import PathSafetyError, PipelineError, SafetyLimitError
from src.guardrails.limits import MAX_N_CASES, MIN_N_CASES
from src.guardrails.validate_io import (
    atomic_write_json,
    resolve_output_dir,
    validate_emr_dir,
    validate_pipeline_params,
)


def test_validate_emr_dir_ok(synthetic_emr):
    path = validate_emr_dir(synthetic_emr)
    assert path == synthetic_emr.resolve()


def test_validate_emr_dir_missing(tmp_path):
    with pytest.raises((PathSafetyError, PipelineError)):
        validate_emr_dir(tmp_path)


def test_validate_emr_dir_incomplete(tmp_path):
    emr = tmp_path / "EMR"
    emr.mkdir()
    (emr / "patient_information.csv").write_text("PID\nx\n")
    # missing vent + vitals
    with pytest.raises(PipelineError, match="missing"):
        validate_emr_dir(emr)


def test_params_n_cases_bounds():
    with pytest.raises(SafetyLimitError):
        validate_pipeline_params(
            n_cases=0,
            preset="default",
            seed=1,
            vent_scan_rows=10_000,
            min_vent_rows=30,
        )
    with pytest.raises(SafetyLimitError):
        validate_pipeline_params(
            n_cases=MAX_N_CASES + 1,
            preset="default",
            seed=1,
            vent_scan_rows=10_000,
            min_vent_rows=30,
        )


def test_params_invalid_preset():
    with pytest.raises(SafetyLimitError):
        validate_pipeline_params(
            n_cases=MIN_N_CASES,
            preset="turbo",
            seed=1,
            vent_scan_rows=10_000,
            min_vent_rows=30,
        )


def test_params_empty_pids():
    with pytest.raises(SafetyLimitError):
        validate_pipeline_params(
            n_cases=10,
            preset="default",
            seed=1,
            vent_scan_rows=10_000,
            min_vent_rows=30,
            pids=[],
        )


def test_params_ok():
    validate_pipeline_params(
        n_cases=25,
        preset="strict",
        seed=0,
        vent_scan_rows=50_000,
        min_vent_rows=30,
        pids=["a", "b"],
    )


def test_output_dir_stays_in_repo(tmp_path, monkeypatch):
    # Writing under repo is fine via default
    out = resolve_output_dir(None, create=True)
    assert out.is_dir()


def test_output_dir_external_blocked(tmp_path, monkeypatch):
    monkeypatch.delenv("MOVER_ALLOW_EXTERNAL_OUTPUT", raising=False)
    external = tmp_path / "outside_repo_out"
    # tmp_path is outside the project repo
    with pytest.raises(PathSafetyError, match="outside repository"):
        resolve_output_dir(external, create=True)


def test_output_dir_external_allowed(tmp_path, monkeypatch):
    monkeypatch.setenv("MOVER_ALLOW_EXTERNAL_OUTPUT", "1")
    external = tmp_path / "outside_repo_out"
    out = resolve_output_dir(external, create=True)
    assert out.is_dir()


def test_atomic_write_json(tmp_path):
    path = tmp_path / "meta.json"
    atomic_write_json({"ok": True, "n": 1}, path)
    assert path.is_file()
    assert not path.with_suffix(".json.tmp").exists()
