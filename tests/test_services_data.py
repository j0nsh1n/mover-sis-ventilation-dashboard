"""Shared data service (UI-agnostic)."""

from __future__ import annotations

import pytest

from src.guardrails.exceptions import GuardrailError
from src.services.data import ensure_data, load_processed


def test_ensure_data_and_load(synthetic_emr, tmp_path, monkeypatch):
    monkeypatch.setenv("MOVER_ALLOW_EXTERNAL_OUTPUT", "1")
    out = tmp_path / "processed"
    cases, ts, flags, episodes, events = ensure_data(
        preset="default",
        force=True,
        processed_dir=out,
        emr_dir=synthetic_emr,
        pids=["caseA", "caseB"],
        min_vent_rows=5,
    )
    assert len(cases) >= 1
    assert not ts.empty
    # second call without force reuses files
    again = load_processed(out)
    assert len(again[0]) == len(cases)


def test_ensure_data_rejects_bad_preset(tmp_path, monkeypatch):
    monkeypatch.setenv("MOVER_ALLOW_EXTERNAL_OUTPUT", "1")
    with pytest.raises(GuardrailError):
        ensure_data(preset="nope", processed_dir=tmp_path / "p", force=True)


def test_load_processed_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_processed(tmp_path)
