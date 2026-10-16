"""Research search over flags and signals: tokenizer, analysis facts, ranking."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.services.retrieval import (
    RetrievalEngine,
    build_analysis_facts,
    build_case_search_text,
    tokenize_search_text,
)

QUESTIONS = [
    "Where does pressure peak?",
    "58-year-old laparoscopic case with high airway pressure",
    "Which cases have missing ETCO2?",
    "Which cases have ETCO2 flags?",
    "Why did pressure rise?",
]


@pytest.mark.parametrize(
    ("text", "tokens"),
    [
        ("Where does pressure peak?", ["pressure", "peak"]),
        ("ETCO₂ flags", ["etco2", "flag"]),
        ("ETCO2, flag", ["etco2", "flag"]),
        ("58-year-old", ["58", "year", "old"]),
        ("Which cases have the flow?", ["flow"]),
        ("high_pip (pip_high)", ["high", "pip", "pip", "high"]),
    ],
)
def test_tokenizer(text, tokens):
    assert tokenize_search_text(text) == tokens


def test_matching_is_whole_token_not_substring(tmp_path):
    index = pd.DataFrame([
        {"PID": "flowcase", "Procedure": "flow study", "Age": 50, "Gender": "F", "has_vent": True},
        {"PID": "lowcase", "Procedure": "low study", "Age": 50, "Gender": "F", "has_vent": True},
    ])
    engine = RetrievalEngine(emr=tmp_path, cache_path=tmp_path / "c.sqlite")
    engine._records = engine._records_from_index(index)
    assert [c.pid for c in engine.search("low").candidates] == ["lowcase"]


@pytest.fixture(scope="module")
def processed(tmp_path_factory):
    from src.pipeline.run import run_pipeline
    from src.pipeline.synthetic import generate_synthetic_emr

    root = tmp_path_factory.mktemp("facts")
    mp = pytest.MonkeyPatch()
    mp.setenv("MOVER_ALLOW_EXTERNAL_OUTPUT", "1")
    try:
        emr = generate_synthetic_emr(root / "EMR", n_cases=30, seed=8)
        data = run_pipeline(
            emr_dir=emr, output_dir=root / "processed", n_cases=30, seed=8,
            write_sample_csv=False, validate=True, preset="default",
        )
    finally:
        mp.undo()
    return emr, data, root


@pytest.fixture(scope="module")
def engine(processed):
    emr, data, root = processed
    facts = build_analysis_facts(data["timeseries"], data["flags"])
    return RetrievalEngine(
        emr=emr,
        cache_path=root / "cache.sqlite",
        analyzed_pids=data["cases"]["PID"].astype(str).tolist(),
        analysis_facts=facts,
        candidate_limit=500,
    )


def test_facts_for_analyzed_case_only(processed):
    _, data, _ = processed
    facts = build_analysis_facts(data["timeseries"], data["flags"])
    pid = str(data["flags"].loc[data["flags"]["rule_id"] == "etco2_high", "PID"].iloc[0])
    text = facts[pid]
    assert "Peak inspiratory pressure (PIP)" in text
    assert "End-tidal CO2 (ETCO2)" in text
    assert "Flag raised: High end-tidal CO2 (etco2_high)." in text
    rules = sorted(data["flags"].loc[data["flags"]["PID"] == pid, "rule_id"].unique())
    assert text.count("Flag raised:") == len(rules)
    assert text == build_analysis_facts(data["timeseries"], data["flags"])[pid]
    args = dict(pid=pid, procedure="Appendectomy", age=40, gender="F", has_vent=True)
    analyzed = build_case_search_text(**args, analyzed=True, facts=text)
    assert analyzed.endswith(text)
    bare = build_case_search_text(**args, analyzed=False)
    assert build_case_search_text(**args, analyzed=False, facts=text) == bare
    assert "Flag raised" not in bare and "Signals" not in bare


def test_unanalyzed_records_have_no_facts(processed):
    emr, data, root = processed
    facts = build_analysis_facts(data["timeseries"], data["flags"])
    pid = str(data["cases"]["PID"].iloc[0])
    other = RetrievalEngine(emr=emr, cache_path=root / "o.sqlite", analyzed_pids=[], analysis_facts=facts)
    other.prepare()
    record = next(r for r in other._records if r.pid == pid)
    assert "Flag raised" not in record.source_text
    assert record.source_text == build_case_search_text(
        pid=pid, procedure=record.procedure, age=record.age, gender=record.gender,
        has_vent=record.has_vent, analyzed=False,
    )


@pytest.mark.parametrize("question", QUESTIONS)
def test_driver_questions_find_candidates(engine, question):
    result = engine.search(question)
    assert result.rank_method == "keyword"
    assert len(result.candidates) >= 1


def _ranks(engine, question):
    return {c.pid: i for i, c in enumerate(engine.search(question, limit=500).candidates)}


def test_etco2_flagged_cases_rank_above_the_rest(engine, processed):
    _, data, _ = processed
    flagged = set(data["flags"].loc[data["flags"]["rule_id"].str.startswith("etco2"), "PID"].astype(str))
    others = set(data["cases"]["PID"].astype(str)) - flagged
    assert flagged and others
    ranks = _ranks(engine, "Which cases have ETCO2 flags?")
    assert max(ranks[p] for p in flagged) < min(ranks[p] for p in others)


def _interior_gap_pids(ts: pd.DataFrame, column: str) -> set[str]:
    out = set()
    for pid, g in ts.sort_values(["PID", "t_min"]).groupby("PID"):
        seen = g[column].notna().to_numpy()
        if seen.any() and (~seen[np.argmax(seen): len(seen) - np.argmax(seen[::-1])]).any():
            out.add(str(pid))
    return out


def test_missing_etco2_cases_rank_first(engine, processed):
    _, data, _ = processed
    gaps = _interior_gap_pids(data["timeseries"], "ETCO2")
    ranks = _ranks(engine, "Which cases have missing ETCO2?")
    rest = set(data["cases"]["PID"].astype(str)) - gaps
    assert gaps and rest
    assert max(ranks[p] for p in gaps) < min(ranks[p] for p in rest)


def test_missing_minutes_ignore_unrecorded_edges_and_planted_gap(tmp_path):
    minutes = np.arange(10, dtype=float)
    base = pd.DataFrame({"t_min": minutes, "PIP": 15.0, "ETCO2": 35.0, "nMAP": 80.0})
    edge = base.assign(PID="edge", ETCO2=[np.nan] * 2 + [35.0] * 6 + [np.nan] * 2)
    planted = base.assign(PID="planted", ETCO2=[35.0, 35.0, np.nan, np.nan, 35.0] + [35.0] * 5)
    cuff = base.assign(PID="cuff", nMAP=[80.0, np.nan, np.nan, 80.0] + [80.0] * 6)
    ts = pd.concat([edge, planted, cuff], ignore_index=True)
    flags = pd.DataFrame({"PID": ["edge"], "rule_id": ["pip_high"]})
    facts = build_analysis_facts(ts, flags)
    assert "Missing minutes" not in facts["edge"]
    assert "Missing minutes: End-tidal CO2 (ETCO2)." in facts["planted"]
    assert "Missing minutes" not in facts["cuff"]

    index = pd.DataFrame([
        {"PID": p, "Procedure": "Appendectomy", "Age": 40, "Gender": "F", "has_vent": True}
        for p in ("edge", "planted", "cuff")
    ])
    engine = RetrievalEngine(emr=tmp_path, cache_path=tmp_path / "c.sqlite",
                             analyzed_pids=list(facts), analysis_facts=facts)
    engine._records = engine._records_from_index(index)
    assert engine.search("Which cases have missing ETCO2?").candidates[0].pid == "planted"
    assert engine.search("Which cases have PIP flags?").candidates[0].pid == "edge"
