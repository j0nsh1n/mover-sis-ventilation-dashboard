"""Case retrieval: cached embeddings, filters, keyword fallback, bounded fetch."""

from __future__ import annotations

from dataclasses import asdict

import pandas as pd
import pytest

from src.services.retrieval import (
    CaseFilters,
    RetrievalEngine,
    RetrievalPhase,
    SearchCandidate,
    build_case_search_text,
    source_text_hash,
)


class FakeEmbedder:
    def __init__(self, model_id: str = "fake-embed", *, available: bool = True):
        self.model_id = model_id
        self._available = available
        self.texts: list[list[str]] = []

    def is_available(self) -> bool:
        return self._available

    def embed(self, texts):
        batch = [str(t) for t in texts]
        self.texts.append(batch)
        return [_bow(t) for t in batch]


def _bow(text: str, dim: int = 16) -> list[float]:
    vocab = (
        "cholecystectomy",
        "laparoscopic",
        "appendectomy",
        "hysterectomy",
        "knee",
        "woman",
        "man",
        "pip",
        "etco2",
        "sevoflurane",
    )
    vec = [0.0] * dim
    blob = text.lower()
    for i, token in enumerate(vocab):
        if token in blob:
            vec[i] += 1.0
    return vec


@pytest.fixture
def cache_path(tmp_path):
    return tmp_path / "emb.sqlite"


@pytest.fixture
def engine(synthetic_emr, cache_path):
    return RetrievalEngine(
        emr=synthetic_emr,
        cache_path=cache_path,
        embedder=FakeEmbedder(),
        analyzed_pids=["caseA"],
        candidate_limit=10,
        extract_limit=1,
    )


def test_search_record_is_short_and_hashed():
    text = build_case_search_text(
        pid="caseA",
        procedure="Laparoscopic Cholecystectomy",
        age=55,
        gender="M",
        has_vent=True,
        analyzed=True,
    )
    assert "caseA" in text
    assert "Cholecystectomy" in text
    assert "analyzed case" in text
    assert source_text_hash(text) == source_text_hash(text)
    other = build_case_search_text(
        pid="caseA",
        procedure="Laparoscopic Cholecystectomy",
        age=55,
        gender="M",
        has_vent=True,
        analyzed=False,
    )
    assert source_text_hash(text) != source_text_hash(other)


def test_prepare_caches_by_hash_and_model(engine, cache_path, synthetic_emr):
    first = engine.prepare()
    assert first == 2
    assert engine.search("cholecystectomy").indexed_count == 2
    second = engine.prepare()
    assert second == 0, "unchanged source text must not re-embed"
    other = RetrievalEngine(
        emr=synthetic_emr,
        cache_path=cache_path,
        embedder=FakeEmbedder(model_id="other-model"),
        analyzed_pids=["caseA"],
    )
    rewritten = other.prepare()
    assert rewritten == 2, "a different embedding model must store its own vectors"


def test_question_embedding_ranks_matching_procedure(engine):
    engine.prepare()
    result = engine.search("laparoscopic cholecystectomy")
    assert result.rank_method == "embedding"
    assert result.model_available is True
    assert result.candidates[0].pid == "caseA"
    assert result.candidates[0].source_label == "analyzed case"
    labels = {c.pid: c.source_label for c in result.candidates}
    assert labels.get("caseB") == "indexed surgery"


def test_exact_filters_drop_nonmatching_before_rank(engine):
    engine.prepare()
    women = engine.search(
        "laparoscopic cholecystectomy",
        filters=CaseFilters(gender="F"),
    )
    assert all(c.pid != "caseA" for c in women.candidates)
    near = engine.search("surgery", filters=CaseFilters(age=55, age_tolerance=3))
    assert [c.pid for c in near.candidates] == ["caseA"]


def test_indexed_and_analyzed_stay_distinct(engine):
    engine.prepare()
    only_indexed = engine.search("", filters=CaseFilters(analyzed=False))
    assert [c.pid for c in only_indexed.candidates] == ["caseB"]
    assert only_indexed.candidates[0].analyzed is False
    only_analyzed = engine.search("", filters=CaseFilters(analyzed=True))
    assert [c.pid for c in only_analyzed.candidates] == ["caseA"]
    assert only_analyzed.analyzed_count == 1
    assert only_analyzed.indexed_count == 2


def test_model_unavailable_uses_keyword_fallback(synthetic_emr, tmp_path):
    phases: list[str] = []
    engine = RetrievalEngine(
        emr=synthetic_emr,
        cache_path=tmp_path / "emb.sqlite",
        embedder=FakeEmbedder(available=False),
        analyzed_pids=["caseA"],
    )
    written = engine.prepare(on_progress=lambda p: phases.append(p.phase.value))
    assert written == 0
    assert RetrievalPhase.MODEL_UNAVAILABLE.value in phases
    result = engine.search("appendectomy", on_progress=lambda p: phases.append(p.phase.value))
    assert result.rank_method == "keyword"
    assert result.model_available is False
    assert result.candidates[0].pid == "caseB"
    assert RetrievalPhase.KEYWORD_FALLBACK.value in phases


def test_public_candidates_do_not_carry_vectors(engine):
    engine.prepare()
    result = engine.search("cholecystectomy")
    payload = asdict(result.candidates[0])
    assert "vector" not in payload
    assert "embedding" not in payload
    assert set(payload) == set(SearchCandidate.__dataclass_fields__)


def test_fetch_is_bounded_and_skips_pipeline_when_frames_present(
    engine, monkeypatch
):
    engine.prepare()
    calls: list[list[str]] = []

    def _should_not_run(pids, **_kwargs):
        calls.append(list(pids))
        raise AssertionError("pipeline must not run when session frames exist")

    monkeypatch.setattr("src.services.case_fetch.fetch_cases", _should_not_run)
    frames = {
        "cases": pd.DataFrame(
            [{"PID": "caseA", "Procedure": "Laparoscopic Cholecystectomy", "Age": 55}]
        ),
        "timeseries": pd.DataFrame({"PID": ["caseA"], "t_min": [0], "PIP": [20]}),
        "flags": pd.DataFrame(),
        "episodes": pd.DataFrame(),
        "events": pd.DataFrame(),
    }
    result = engine.retrieve(
        "cholecystectomy",
        session_frames=frames,
    )
    assert calls == []
    assert result.fetched_pids == ("caseA",)
    assert result.extracts
    assert "SOURCE EXTRACT" in result.extracts[0]
    assert "analyzed case" in result.extracts[0]
    assert "caseA" in result.extracts[0]


def test_retrieve_fetches_only_selected_or_top_limit(engine, monkeypatch):
    engine.extract_limit = 1
    engine.prepare()
    seen: list[str] = []

    def fake_fetch(pids, **_kwargs):
        from src.services.case_fetch import FetchResult

        seen.extend(pids)
        pid = pids[0]
        return FetchResult(
            cases=pd.DataFrame([{"PID": pid, "Procedure": "x"}]),
            computed=[pid],
        )

    monkeypatch.setattr("src.services.case_fetch.fetch_cases", fake_fetch)
    result = engine.retrieve("cholecystectomy appendectomy")
    assert len(seen) == 1
    assert result.fetched_pids == (seen[0],)


@pytest.fixture
def emr_with_unventilated_first(synthetic_emr):
    """caseN: same procedure as caseA, listed first, but no ventilator rows."""
    info = pd.read_csv(synthetic_emr / "patient_information.csv")
    extra = info[info["PID"] == "caseA"].assign(PID="caseN")
    pd.concat([extra, info]).to_csv(synthetic_emr / "patient_information.csv", index=False)
    return synthetic_emr


@pytest.mark.parametrize("available", [True, False])
def test_equal_matches_prefer_cases_with_ventilator_data(
    emr_with_unventilated_first, cache_path, available
):
    engine = RetrievalEngine(
        emr=emr_with_unventilated_first,
        cache_path=cache_path,
        embedder=FakeEmbedder(available=available),
        candidate_limit=10,
        extract_limit=1,
    )
    result = engine.search("laparoscopic cholecystectomy")
    pids = [c.pid for c in result.candidates]
    assert pids.index("caseA") < pids.index("caseN")
    no_vent = next(c for c in result.candidates if c.pid == "caseN")
    assert no_vent.has_vent is False
    assert "no ventilator data" in no_vent.source_label


def test_retrieve_extracts_skip_cases_without_ventilator_data(
    emr_with_unventilated_first, cache_path, monkeypatch
):
    engine = RetrievalEngine(
        emr=emr_with_unventilated_first,
        cache_path=cache_path,
        embedder=None,
        extract_limit=1,
    )
    monkeypatch.setattr(engine, "_extract_for", lambda rec, frames: f"extract {rec.pid}")
    # "caseN" alone scores higher here, yet the extract goes to a ventilated case
    monkeypatch.setattr(
        "src.services.retrieval._keyword_score",
        lambda q, text: 1.0 if "caseN" in text else 0.5,
    )
    result = engine.retrieve("laparoscopic cholecystectomy")
    assert result.candidates[0].pid == "caseN"
    assert result.fetched_pids == ("caseA",)
