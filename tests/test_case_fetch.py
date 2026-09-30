"""On-demand EMR retrieval: index shortlist, then flag only the shortlist."""

from __future__ import annotations

import pytest

from src.services import case_fetch


@pytest.fixture(autouse=True)
def _clear_fetch_caches():
    case_fetch.clear_caches()
    yield
    case_fetch.clear_caches()


def test_index_lists_every_surgery_with_vent_flag(synthetic_emr):
    idx = case_fetch.emr_index(synthetic_emr)
    assert len(idx) == 2
    assert set(idx["PID"]) == {"caseA", "caseB"}
    assert idx["has_vent"].all()  # both synthetic cases have ventilator rows
    # wave probing is opt-in: it costs one filesystem lookup per surgery
    assert "has_wave" not in idx.columns


def test_index_is_memoised_per_emr_dir(synthetic_emr):
    first = case_fetch.emr_index(synthetic_emr)
    second = case_fetch.emr_index(synthetic_emr)
    assert first is second


def test_search_matches_procedure_text(synthetic_emr):
    hit = case_fetch.search_emr(text="cholecystectomy", emr=synthetic_emr)
    assert list(hit["PID"]) == ["caseA"]


def test_search_falls_back_to_any_term_when_all_terms_miss(synthetic_emr):
    # "laparoscopic" matches caseA; "craniotomy" matches nothing
    hit = case_fetch.search_emr(text="laparoscopic craniotomy", emr=synthetic_emr)
    assert "caseA" in set(hit["PID"])


def test_search_filters_by_gender_and_age(synthetic_emr):
    women = case_fetch.search_emr(gender="F", emr=synthetic_emr)
    assert list(women["PID"]) == ["caseB"]
    near_55 = case_fetch.search_emr(age=55, age_tolerance=3, emr=synthetic_emr)
    assert list(near_55["PID"]) == ["caseA"]


def test_search_respects_limit(synthetic_emr):
    assert len(case_fetch.search_emr(limit=1, emr=synthetic_emr)) == 1


def test_fetch_computes_flags_on_demand(synthetic_emr, monkeypatch):
    monkeypatch.setenv("MOVER_ALLOW_EXTERNAL_OUTPUT", "1")
    res = case_fetch.fetch_cases(["caseA"], emr=synthetic_emr)
    assert res.computed == ["caseA"]
    assert res.reused == []
    assert not res.cases.empty
    assert not res.flags.empty
    assert set(res.cases["PID"]) == {"caseA"}
    # caseA is built with a rising PIP ramp, so it must raise pip flags
    assert res.flags["rule_id"].str.contains("pip").any()


def test_fetch_reuses_cache_on_second_call(synthetic_emr, monkeypatch):
    monkeypatch.setenv("MOVER_ALLOW_EXTERNAL_OUTPUT", "1")
    case_fetch.fetch_cases(["caseA"], emr=synthetic_emr)

    calls = {"n": 0}
    real = case_fetch._run_for

    def counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(case_fetch, "_run_for", counting)
    res = case_fetch.fetch_cases(["caseA"], emr=synthetic_emr)
    assert calls["n"] == 0, "cached PID must not trigger another pipeline run"
    assert res.reused == ["caseA"]
    assert not res.cases.empty


def test_cache_is_keyed_by_preset(synthetic_emr, monkeypatch):
    """Flags depend on the preset, so a preset switch must recompute."""
    monkeypatch.setenv("MOVER_ALLOW_EXTERNAL_OUTPUT", "1")
    case_fetch.fetch_cases(["caseA"], emr=synthetic_emr, preset="default")
    res = case_fetch.fetch_cases(["caseA"], emr=synthetic_emr, preset="strict")
    assert res.computed == ["caseA"], "strict preset must not reuse default-preset flags"


def test_fetch_skips_pids_without_ventilator_rows(synthetic_emr, monkeypatch):
    monkeypatch.setenv("MOVER_ALLOW_EXTERNAL_OUTPUT", "1")
    res = case_fetch.fetch_cases(["caseA", "ghost-pid"], emr=synthetic_emr)
    assert "ghost-pid" in res.skipped
    assert set(res.cases["PID"]) == {"caseA"}


def test_fetch_empty_request_is_harmless():
    res = case_fetch.fetch_cases([])
    assert res.cases.empty and res.computed == []


def test_fetch_respects_max_n_cases(synthetic_emr, monkeypatch):
    """The pipeline guardrail caps a single call; the rest are reported, not dropped silently."""
    monkeypatch.setattr(case_fetch, "MAX_N_CASES", 1)
    monkeypatch.setenv("MOVER_ALLOW_EXTERNAL_OUTPUT", "1")
    res = case_fetch.fetch_cases(["caseA", "caseB"], emr=synthetic_emr)
    assert len(res.computed) == 1
    assert len(res.skipped) == 1


def test_find_and_fetch_goes_index_then_pipeline(synthetic_emr, monkeypatch):
    monkeypatch.setenv("MOVER_ALLOW_EXTERNAL_OUTPUT", "1")
    res = case_fetch.find_and_fetch(text="appendectomy", emr=synthetic_emr)
    assert set(res.cases["PID"]) == {"caseB"}


def test_find_and_fetch_with_no_match_runs_no_pipeline(synthetic_emr, monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(
        case_fetch, "_run_for", lambda *a, **k: called.__setitem__("n", called["n"] + 1)
    )
    res = case_fetch.find_and_fetch(text="zzzznotaprocedure", emr=synthetic_emr)
    assert res.cases.empty
    assert called["n"] == 0


def test_vent_capable_pids_handles_missing_file(tmp_path):
    assert case_fetch.vent_capable_pids(tmp_path) == set()


def test_index_handles_missing_emr(tmp_path):
    assert case_fetch.emr_index(tmp_path).empty
