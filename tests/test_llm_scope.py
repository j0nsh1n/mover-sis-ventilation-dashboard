"""Co-pilot tools state whether they cover the loaded sample or the full EMR."""

from __future__ import annotations

import pandas as pd
import pytest

import src.llm.tools as tools
from src.llm.agent import verify_answer
from src.llm.tools import (
    SessionState,
    tool_corpus_overview,
    tool_rule_case_counts,
    tool_search_cases,
    tool_select_case,
    tool_top_anomaly_cases,
    tool_verify_selection,
)


def _row(pid, score, proc="Laparoscopic cholecystectomy", rules="pip_high(5)"):
    return {
        "PID": pid,
        "Age": 50,
        "Gender": "F",
        "Procedure_short": proc,
        "primary_agent_name": "sevoflurane",
        "anomaly_score": score,
        "anomaly_score_per_hour": score / 2,
        "top_rules": rules,
    }


def _episodes(rows):
    return pd.DataFrame(
        [
            {"PID": p, "rule_id": r, "severity": s, "n_minutes": m, "duration_min": m}
            for p, r, s, m in rows
        ]
    )


@pytest.fixture(autouse=True)
def emr_counts(monkeypatch):
    monkeypatch.setattr(tools, "_emr_counts", lambda: (1000, 400))


def _sample_only() -> SessionState:
    return SessionState(
        cases=pd.DataFrame([_row("s1", 10), _row("s2", 30)]),
        episodes=_episodes([("s2", "pip_high", "warn", 5)]),
    )


def _with_scan() -> SessionState:
    session = _sample_only()
    session.corpus = pd.DataFrame(
        [_row("s1", 10), _row("s2", 30), _row("f9", 90, proc="Craniotomy", rules="etco2_high(9)")]
    )
    session.corpus_episodes = _episodes(
        [("s2", "pip_high", "warn", 5), ("f9", "etco2_high", "warn", 9), ("f9", "pip_high", "critical", 2)]
    )
    session.corpus_meta = {
        "n_indexed_surgeries": 1000,
        "n_ventilated_surgeries": 400,
        "preset": "default",
        "created_at": "2026-09-30T00:00:00+00:00",
    }
    return session


def test_sample_results_say_they_are_not_the_whole_emr():
    session = _sample_only()
    for text in (
        tool_corpus_overview(session),
        tool_top_anomaly_cases(session),
        tool_search_cases(session, query="cholecystectomy"),
        tool_rule_case_counts(session),
    ):
        first = text.splitlines()[0]
        assert first.startswith("SCOPE: loaded sample — 2 analyzed cases, NOT the whole EMR")
        assert "400 with ventilator data of 1000 indexed surgeries" in first
    assert "Corpus size" not in tool_corpus_overview(session)


def test_scan_is_used_by_default_and_sample_on_request():
    session = _with_scan()
    top = tool_top_anomaly_cases(session, limit=1)
    assert top.startswith("SCOPE: full-EMR scan — 3 scored surgeries")
    assert "PID=f9" in top
    sample_top = tool_top_anomaly_cases(session, limit=1, scope="sample")
    assert sample_top.startswith("SCOPE: loaded sample")
    assert "PID=s2" in sample_top
    assert "pass scope='full'" in sample_top.splitlines()[0]


def test_full_requested_without_scan_falls_back_and_says_so():
    text = tool_top_anomaly_cases(_sample_only(), scope="full")
    assert "requested but none is loaded" in text.splitlines()[0]


def test_per_hour_ranking():
    session = _sample_only()
    session.cases.loc[0, "anomaly_score_per_hour"] = 99
    assert "PID=s1" in tool_top_anomaly_cases(session, limit=1, per_hour=True)


def test_search_over_scan_matches_rule_ids_in_top_rules():
    text = tool_search_cases(_with_scan(), query="etco2_high")
    assert "PID=f9" in text and "PID=s1" not in text


def test_rule_case_counts_per_scope():
    session = _with_scan()
    full = tool_rule_case_counts(session)
    assert "- pip_high: 2 cases (66.7%) · 2 episodes · 7 flagged minutes" in full
    critical = tool_rule_case_counts(session, severity="critical")
    assert "pip_high: 1 cases" in critical and "etco2_high" not in critical
    sample = tool_rule_case_counts(session, scope="sample")
    assert "- pip_high: 1 cases (50.0%)" in sample


def test_select_case_fetches_scan_only_pid_on_demand(monkeypatch):
    session = _with_scan()
    fetched = []

    def fake_fetch(session_arg, pid):
        fetched.append(pid)
        session_arg.cases = pd.concat(
            [session_arg.cases, pd.DataFrame([_row(pid, 90, proc="Craniotomy")])],
            ignore_index=True,
        )
        return True

    monkeypatch.setattr(tools, "_fetch_into_session", fake_fetch)
    monkeypatch.setattr(tools, "build_case_context", lambda pid, **kw: f"BRIEF {pid}")
    text = tool_select_case(session, pid="f9")
    assert fetched == ["f9"]
    assert text.startswith("SELECTED_CASE: f9")
    assert "analyzed on demand" in text
    assert session.active_pid == "f9"


def test_scan_pids_count_as_known_in_verification():
    session = _with_scan()
    session.focus_pids = ["f9"]
    assert "NOT IN" not in tool_verify_selection(session)
    _, notes = verify_answer("Case f9 had the highest score.", session)
    assert not any(n.startswith("unknown_pids") for n in notes)
