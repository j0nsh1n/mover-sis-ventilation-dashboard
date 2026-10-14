"""Retrieved extracts go to the existing agent; vectors do not."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from src.llm.agent import run_agent
from src.llm.ollama_client import ChatResult
from src.llm.retrieve import answer_from_retrieval, format_grounded_extracts
from src.llm.tools import SessionState
from src.services.retrieval import SearchCandidate


class FakeClient:
    def __init__(self, content: str = "In this extract, caseA is age 55 M."):
        self.messages = None
        self.content = content

    def chat_turn(self, model, messages, **_kwargs):
        self.messages = messages
        return ChatResult(content=self.content, tool_calls=[])


class DraftClient:
    """Returns the drafts in order and keeps every message list it was sent."""

    def __init__(self, *drafts: str):
        self.drafts = drafts
        self.sent: list[list[dict]] = []

    def chat_turn(self, model, messages, **_kwargs):
        self.sent.append([dict(m) for m in messages])
        draft = self.drafts[min(len(self.sent), len(self.drafts)) - 1]
        return ChatResult(content=draft, tool_calls=[])

    def rewrite_request(self) -> str:
        return self.sent[1][-1]["content"]


@pytest.fixture(autouse=True)
def synthetic_emr_dir(tmp_path: Path, monkeypatch) -> Path:
    """Medication lookups read this empty synthetic EMR, never the default data dir."""
    emr = tmp_path / "EMR"
    emr.mkdir()
    monkeypatch.setenv("MOVER_EMR_DIR", str(emr))
    return emr


def _write_medications(emr: Path, rows: list[dict]) -> None:
    pd.DataFrame(rows).to_csv(emr / "patient_medication.csv", index=False)


def _answer(client, *candidates: SearchCandidate):
    return answer_from_retrieval(
        question="laparoscopic cholecystectomy high PIP",
        model="fake",
        candidates=list(candidates),
        extracts=[f"SOURCE EXTRACT PID={c.pid}\nPIP median 22" for c in candidates],
        client=client,
    )


def _candidate() -> SearchCandidate:
    return SearchCandidate(
        pid="caseA",
        source_label="analyzed case",
        analyzed=True,
        procedure="Laparoscopic Cholecystectomy",
        age=55.0,
        gender="M",
        source_text="PID caseA. Procedure: Laparoscopic Cholecystectomy.",
        rank_method="embedding",
        score=0.91,
    )


def _session() -> SessionState:
    cases = pd.DataFrame(
        [
            {
                "PID": "caseA",
                "Age": 55,
                "Gender": "M",
                "Procedure_short": "Laparoscopic cholecystectomy",
                "primary_agent_name": "sevoflurane",
                "anomaly_score": 4,
            }
        ]
    )
    return SessionState(cases=cases, timeseries=pd.DataFrame(), flags=pd.DataFrame())


def test_format_omits_scores_and_vectors():
    text = format_grounded_extracts(
        candidates=[_candidate()],
        extracts=["SOURCE EXTRACT (analyzed case) PID=caseA\nPIP median 22"],
    )
    assert "analyzed case" in text
    assert "PID=caseA" in text
    assert "PIP median 22" in text
    assert "0.91" not in text
    assert "vector" not in text.lower()
    assert "Cite the extract text only" in text


def test_format_limits_the_model_to_three_extracts():
    candidates = [replace(_candidate(), pid=f"case{i}") for i in range(4)]
    text = format_grounded_extracts(
        candidates=candidates,
        extracts=[f"private source {i}" for i in range(4)],
    )
    assert "private source 2" in text
    assert "private source 3" not in text


def test_answer_from_retrieval_uses_extracts_not_sample_prefetch():
    client = FakeClient()
    extract = "SOURCE EXTRACT (analyzed case) PID=caseA\nDocumented PIP median 22."
    result = answer_from_retrieval(
        question="55y man laparoscopic cholecystectomy high PIP",
        model="fake",
        candidates=[_candidate()],
        extracts=[extract],
        client=client,
    )
    user = client.messages[1]["content"]
    assert extract in user
    assert "[auto] find_similar" not in user
    assert "0.91" not in user
    assert "research" in result.answer.lower() or "Research" in result.answer


def test_research_answer_cannot_call_case_tools_outside_shortlist():
    class RogueClient:
        def __init__(self):
            self.messages = None
            self.tools = object()

        def chat_turn(self, model, messages, **kwargs):
            self.messages = messages
            self.tools = kwargs["tools"]
            return ChatResult(
                content="caseA has a recorded PIP median of 22.",
                tool_calls=[{
                    "id": "rogue",
                    "function": {"name": "select_case", "arguments": {"pid": "caseB"}},
                }],
            )

    client = RogueClient()
    result = answer_from_retrieval(
        question="Where does pressure peak?",
        model="fake",
        candidates=[_candidate()],
        extracts=["SOURCE EXTRACT PID=caseA\nPIP median 22"],
        client=client,
    )
    assert client.tools is None
    assert len(client.messages) == 2
    assert "caseB" not in str(client.messages)
    assert result.active_pid is None
    assert "caseA" in result.answer


def test_run_agent_skip_prefetch_does_not_search_loaded_sample():
    client = FakeClient("In this extract, caseA is age 55.")
    session = _session()
    run_agent(
        model="fake",
        question="hysterectomy sevoflurane",
        session=session,
        client=client,
        skip_prefetch=True,
        extra_prefetched="SOURCE EXTRACT PID=caseA\nprocedure cholecystectomy",
    )
    user = client.messages[1]["content"]
    assert "SOURCE EXTRACT PID=caseA" in user
    assert "[auto] find_similar_cases" not in user
    assert session.focus_pids == []


def test_wrong_age_on_a_cited_case_is_rewritten_to_the_record_age():
    client = DraftClient(
        "caseA is a 55-year-old man with a PIP median of 22.",
        "caseA is a 60-year-old man with a PIP median of 22.",
    )
    result = _answer(client, replace(_candidate(), age=60.0))
    assert len(client.sent) == 2
    assert "60" in client.rewrite_request()
    assert "60-year-old" in result.answer
    assert "55-year-old" not in result.answer


def test_dose_absent_from_the_cited_case_medications_is_rewritten(synthetic_emr_dir):
    _write_medications(
        synthetic_emr_dir,
        [
            {"PID": "caseA", "Drug_name": "Fentanyl", "Dose": "100", "Drug_units": "mcg"},
            {"PID": "caseZ", "Drug_name": "Fentanyl", "Dose": "250", "Drug_units": "mcg"},
        ],
    )
    client = DraftClient(
        "caseA, a 55-year-old man, received fentanyl 250 mcg.",
        "caseA, a 55-year-old man, received fentanyl 100 mcg.",
    )
    result = _answer(client, _candidate())
    assert len(client.sent) == 2
    assert "100 mcg" in client.rewrite_request()
    assert "fentanyl 100 mcg" in result.answer
    assert "250 mcg" not in result.answer


def test_pid_outside_the_cited_set_is_rewritten():
    cited = replace(_candidate(), pid="aabb0011")
    client = DraftClient(
        "aabb0011 had a PIP median of 22, like deadbeef99.",
        "aabb0011 had a PIP median of 22.",
    )
    result = _answer(client, cited)
    assert len(client.sent) == 2
    assert "deadbeef99" in client.rewrite_request()
    assert "deadbeef99" not in result.answer
    assert "aabb0011" in result.answer


def test_correct_draft_makes_exactly_one_chat_call(synthetic_emr_dir):
    _write_medications(
        synthetic_emr_dir,
        [{"PID": "caseA", "Drug_name": "Fentanyl", "Dose": "100", "Drug_units": "mcg"}],
    )
    client = DraftClient("caseA, a 60-year-old man, received fentanyl 100 mcg.")
    result = _answer(client, replace(_candidate(), age=60.0))
    assert len(client.sent) == 1
    assert "Verification note" not in result.answer


def test_only_the_wrong_age_among_three_cited_cases_is_rewritten():
    cases = [
        replace(_candidate(), pid="caseA", age=40.0),
        replace(_candidate(), pid="caseB", age=50.0),
        replace(_candidate(), pid="caseC", age=60.0),
    ]
    client = DraftClient(
        "caseA is a 40-year-old woman. caseB is a 50-year-old man. "
        "caseC is a 65-year-old man.",
        "caseA is a 40-year-old woman. caseB is a 50-year-old man. "
        "caseC is a 60-year-old man.",
    )
    result = _answer(client, *cases)
    assert len(client.sent) == 2
    problems = client.rewrite_request().split("Problems found:")[1].split("Rewrite the")[0]
    assert "caseC" in problems and "60" in problems
    assert "caseA" not in problems and "caseB" not in problems
    for stated in ("40-year-old", "50-year-old", "60-year-old"):
        assert stated in result.answer
    assert "65-year-old" not in result.answer


def test_missing_age_on_a_cited_case_neither_rewrites_nor_invents_an_age():
    client = DraftClient("caseA is a 55-year-old man with a PIP median of 22.")
    result = _answer(client, replace(_candidate(), age=None))
    assert len(client.sent) == 1
    assert "Verification note" not in result.answer
    assert "55" in result.answer


def test_ask_selection_never_enters_the_research_answer(synthetic_emr_dir):
    _write_medications(
        synthetic_emr_dir,
        [{"PID": "caseB", "Drug_name": "Fentanyl", "Dose": "100", "Drug_units": "mcg"}],
    )
    ask = _session()
    ask.cases = pd.concat(
        [ask.cases, pd.DataFrame([{"PID": "caseB", "Age": 70, "Gender": "F"}])],
        ignore_index=True,
    )
    ask.active_pid = "caseB"
    ask.focus_pids = ["caseB"]
    run_agent(model="fake", question="caseB", session=ask, client=FakeClient("caseB."))

    client = DraftClient("caseA, a 55-year-old man, received fentanyl 250 mcg.")
    result = _answer(client, _candidate())
    assert "caseB" not in str(client.sent)
    assert len(client.sent) == 1, "caseB's medications must not gate a caseA answer"
    assert result.active_pid is None
    assert result.focus_pids == []
