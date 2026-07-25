"""Keyword search filters."""

from __future__ import annotations

import pandas as pd

from src.search import filter_cases_by_keywords, tokenize_query


def test_tokenize_phrases_and_words():
    assert tokenize_query('chole "gastric bypass" sevo') == [
        "gastric bypass",
        "chole",
        "sevo",
    ]
    assert tokenize_query("") == []


def test_filter_and_or():
    cases = pd.DataFrame(
        {
            "PID": ["aaa", "bbb", "ccc"],
            "Procedure_short": [
                "Laparoscopic Cholecystectomy",
                "Appendectomy",
                "Gastric bypass revision",
            ],
            "primary_agent_name": ["sevoflurane", "desflurane", "sevoflurane"],
            "anomaly_score": [1, 2, 3],
            "top_rules": ["pip_high", "etco2_low", "pip_high, agent_high"],
        }
    )
    out = filter_cases_by_keywords(cases, "chole sevo", match_all=True)
    assert list(out["PID"]) == ["aaa"]

    out_or = filter_cases_by_keywords(cases, "append gastric", match_all=False)
    assert set(out_or["PID"]) == {"bbb", "ccc"}


def test_filter_matches_flag_rules():
    cases = pd.DataFrame(
        {
            "PID": ["p1", "p2"],
            "Procedure_short": ["X", "Y"],
            "anomaly_score": [0, 0],
        }
    )
    flags = pd.DataFrame(
        {
            "PID": ["p1", "p1", "p2"],
            "rule_id": ["pip_high", "etco2_low", "agent_drift"],
        }
    )
    out = filter_cases_by_keywords(cases, "pip_high", flags=flags)
    assert list(out["PID"]) == ["p1"]


def test_empty_query_returns_all():
    cases = pd.DataFrame({"PID": ["a"], "Procedure_short": ["x"], "anomaly_score": [1]})
    assert len(filter_cases_by_keywords(cases, "  ")) == 1
