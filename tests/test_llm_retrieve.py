"""Retrieved extracts go to the existing agent; vectors do not."""

from __future__ import annotations

import pandas as pd

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


def test_answer_from_retrieval_uses_extracts_not_sample_prefetch():
    client = FakeClient()
    session = _session()
    extract = "SOURCE EXTRACT (analyzed case) PID=caseA\nDocumented PIP median 22."
    result = answer_from_retrieval(
        question="55y man laparoscopic cholecystectomy high PIP",
        model="fake",
        session=session,
        candidates=[_candidate()],
        extracts=[extract],
        client=client,
    )
    user = client.messages[1]["content"]
    assert extract in user
    assert "[auto] find_similar" not in user
    assert "0.91" not in user
    assert "research" in result.answer.lower() or "Research" in result.answer


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
