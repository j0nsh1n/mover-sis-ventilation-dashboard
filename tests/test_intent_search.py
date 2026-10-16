"""Scenario and comparison questions search, fetch and answer through the bounded extract path."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from src.desktop.research_workers import ResearchWorker
from src.llm.ollama_client import ChatResult
from src.pipeline.synthetic import main as write_synthetic_emr
from src.services.retrieval import RetrievalEngine

# Synthetic EMR, seed 7: women aged 60-80 with an arthroplasty are exactly these four cases
WOMEN_ARTHROPLASTY = {"SYN000583775c", "SYN000757bf6a", "SYN0018828ecc", "SYN0023ed8d50"}
AGENTS = {"SYN000583775c": "sevoflurane", "SYN000757bf6a": "sevoflurane", "SYN0018828ecc": "desflurane"}
LAP_CHOLE, ROBOTIC_PROSTATE, LAP_APPENDECTOMY = "SYN00016867e6", "SYN0003c4178c", "SYN0008de5664"


class RecordingClient:
    """Answers every extract-only request with a figure-free description and records the messages."""

    def __init__(self):
        self.sent: list[list[dict]] = []

    def chat_turn(self, model, messages, **kwargs):
        if kwargs.get("format"):
            return ChatResult(content=json.dumps(dict.fromkeys(kwargs["format"]["required"], "description")))
        assert not kwargs.get("tools")
        self.sent.append([dict(m) for m in messages])
        return ChatResult(content="The extracts describe the cited cases.")


@pytest.fixture(scope="module")
def emr(tmp_path_factory):
    path = tmp_path_factory.mktemp("intent") / "EMR"
    write_synthetic_emr(["--out", str(path), "--n-cases", "30", "--seed", "7"])
    return path


@pytest.fixture
def engine(emr, tmp_path, monkeypatch):
    monkeypatch.setenv("MOVER_EMR_DIR", str(emr))
    built = RetrievalEngine(emr=emr, cache_path=tmp_path / "c.sqlite", case_agents=AGENTS, analyzed_pids=list(AGENTS))
    fetched: list[list[str]] = []

    def fetch(pids, **_kwargs):
        pids = list(pids)[: built.extract_limit]
        fetched.append(pids)
        return tuple(f"SOURCE EXTRACT PID={pid}\nPIP median 22" for pid in pids), tuple(pids)

    built.fetch_extracts = fetch
    built.fetched = fetched
    return built


def run_worker(monkeypatch, engine, question, model="fake"):
    client = RecordingClient()
    monkeypatch.setattr("src.desktop.research_workers.OllamaClient", lambda **_kwargs: client)
    seen = SimpleNamespace(shortlist=[], answers=[], failures=[])
    worker = ResearchWorker(engine, question, model, SimpleNamespace(cases=None, timeseries=None, flags=None, episodes=None, events=None))
    worker.shortlist.connect(seen.shortlist.append)
    worker.answer.connect(seen.answers.append)
    worker.failed.connect(seen.failures.append)
    worker.run()
    assert not seen.failures, seen.failures
    return seen, client


def cited_blocks(client) -> list[str]:
    return [line for line in client.sent[0][-1]["content"].splitlines() if line.startswith("--- source ")]


def test_scenario_filters_the_search_and_answers_from_at_most_three_cases(monkeypatch, engine):
    seen, client = run_worker(monkeypatch, engine, "women aged 60-80 arthroplasty, elevated PIP")
    result = seen.shortlist[0]
    assert {c.pid for c in result.candidates} == WOMEN_ARTHROPLASTY
    assert result.scope_note == "Scenario: female, 60-80, arthroplasty · 4 matching cases · answering from the top 3"
    assert len(engine.fetched[0]) == 3 and set(engine.fetched[0]) <= WOMEN_ARTHROPLASTY
    assert len(cited_blocks(client)) == 3 and len(seen.answers) == 1
    assert "matched that description" in client.sent[0][-1]["content"]


def test_scenario_agent_filter_uses_the_agent_recorded_for_analyzed_cases(monkeypatch, engine):
    seen, _ = run_worker(monkeypatch, engine, "women aged 60-80 arthroplasty with sevoflurane")
    assert {c.pid for c in seen.shortlist[0].candidates} == {"SYN000583775c", "SYN000757bf6a"}
    assert seen.shortlist[0].scope_note.endswith("2 matching cases")


def test_scan_agents_let_the_agent_filter_match_unanalyzed_surgeries(monkeypatch, engine):
    engine.case_agents = {**AGENTS, "SYN0023ed8d50": "sevoflurane"}
    engine.agents_from_scan = True
    seen, _ = run_worker(monkeypatch, engine, "women aged 60-80 arthroplasty with sevoflurane")
    pids = {c.pid for c in seen.shortlist[0].candidates}
    assert pids == {"SYN000583775c", "SYN000757bf6a", "SYN0023ed8d50"}
    assert "SYN0023ed8d50" not in engine.analyzed_pids


@pytest.mark.parametrize(
    ("from_scan", "hint"),
    [
        (False, "a full-EMR scan adds it for every surgery with ventilator data"),
        (True, "surgeries without it never match"),
    ],
)
def test_an_emptied_agent_filter_says_where_agents_come_from(monkeypatch, engine, from_scan, hint):
    engine.agents_from_scan = from_scan
    seen, _ = run_worker(monkeypatch, engine, "women aged 60-80 arthroplasty with isoflurane")
    assert hint in seen.shortlist[0].empty_reason


@pytest.mark.parametrize(
    ("question", "emptied", "left"),
    [
        ("women aged 60-80 arthroplasty with isoflurane", "agent filter (isoflurane)", "4 surgeries passed"),
        ("men aged 60-80 hysterectomy", "procedure filter (hysterectomy)", "passed the filters before it"),
        ("a 5-year-old woman", "age filter (5 ±10)", "30 surgeries passed"),
    ],
)
def test_a_scenario_with_no_match_names_the_filter_that_emptied_it(monkeypatch, engine, question, emptied, left):
    seen, client = run_worker(monkeypatch, engine, question)
    result = seen.shortlist[0]
    assert result.candidates == () and not seen.answers and not client.sent and not engine.fetched
    assert result.scope_note.endswith("no matching cases")
    assert emptied in result.empty_reason and left in result.empty_reason


def test_compare_uses_exactly_the_named_pids_in_any_letter_case(monkeypatch, engine):
    seen, client = run_worker(monkeypatch, engine, f"Compare {ROBOTIC_PROSTATE.lower()} with {LAP_CHOLE} on PIP")
    result = seen.shortlist[0]
    assert [c.pid for c in result.candidates] == [ROBOTIC_PROSTATE, LAP_CHOLE]
    assert result.scope_note == f"Comparing {ROBOTIC_PROSTATE} and {LAP_CHOLE}" and result.compare
    assert engine.fetched == [[ROBOTIC_PROSTATE, LAP_CHOLE]]
    assert len(cited_blocks(client)) == 2
    assert "Contrast the cases" in client.sent[0][-1]["content"]


def test_compare_of_more_pids_than_the_extract_limit_says_so(monkeypatch, engine):
    pids = [LAP_CHOLE, ROBOTIC_PROSTATE, LAP_APPENDECTOMY, "SYN000270cf01"]
    seen, _ = run_worker(monkeypatch, engine, "compare " + " ".join(pids))
    assert [c.pid for c in seen.shortlist[0].candidates] == pids[:3]
    assert "the first 3 of 4 named" in seen.shortlist[0].scope_note


def test_compare_without_named_pids_takes_the_top_two_matches(monkeypatch, engine):
    seen, client = run_worker(monkeypatch, engine, "compare laparoscopic appendectomy cases")
    chosen = [c.pid for c in seen.shortlist[0].candidates]
    assert len(chosen) == 2 and engine.fetched == [chosen]
    assert len(cited_blocks(client)) == 2


def test_compare_with_a_pid_not_in_the_index_does_not_answer(monkeypatch, engine):
    seen, client = run_worker(monkeypatch, engine, f"compare {LAP_CHOLE} and SYN0000000fff")
    result = seen.shortlist[0]
    assert [c.pid for c in result.candidates] == [LAP_CHOLE]
    assert "not in the indexed surgeries: SYN0000000fff" in result.scope_note
    assert "needs two cases" in result.empty_reason and "SYN0000000fff" in result.empty_reason
    assert not seen.answers and not client.sent


def test_a_plain_question_is_unchanged(monkeypatch, engine):
    seen, client = run_worker(monkeypatch, engine, "Where does pressure peak in laparoscopic cases?")
    result = seen.shortlist[0]
    assert result.scope_note == "" and not result.compare and result.filtered_count == result.indexed_count
    assert len(engine.fetched[0]) <= 3 and len(seen.answers) == 1
    assert "Answer the research question" in client.sent[0][-1]["content"]


@pytest.mark.parametrize(
    "question",
    ["Which laparoscopic cases have high pressure?", "55-year-old woman, arthroplasty, elevated PIP", f"Compare {LAP_CHOLE} and {ROBOTIC_PROSTATE}"],
    ids=["question", "scenario", "compare"],
)
def test_extract_prompts_never_tell_the_model_to_call_tools(monkeypatch, engine, question):
    _, client = run_worker(monkeypatch, engine, question)
    sent = "\n".join(m["content"] for m in client.sent[0]).lower()
    for phrase in ("call tools", "call the remaining tools", "tool", "find_similar_cases", "select_case", "verify_selection"):
        assert phrase not in sent, phrase
    assert "source extracts" in sent
