"""The local model labels answer sentences as advice; a flagged sentence counts as a pattern hit."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

import pandas as pd
import pytest

from src.llm.advice_check import Labeled, NotRun, check_advice, split_sentences
from src.llm.agent import find_directive_phrases, run_agent
from src.llm.ollama_client import ChatResult
from src.llm.retrieve import answer_from_retrieval
from src.llm.tools import SessionState
from src.services.retrieval import SearchCandidate

# Hedged advice the word patterns miss; only the model check can catch it.
HEDGED = "A slower FiO2 wean seems like a good idea for this airway."
FACT = "caseA had a PIP median of 22 cmH2O."
REWRITTEN = "caseA had a PIP median of 22 cmH2O. FiO2 was weaned over 20 minutes."
# Every figure the drafts below state, as the extract would give it.
EXTRACT = (
    "SOURCE EXTRACT PID=caseA\nPIP median 22 cmH2O. FiO2 weaned over 20 minutes to 1.0. "
    "PEEP 8 cmH2O at t=40. SpO2 89 % at t=95. Extubation at t=182."
)


def _flag_hedged(sentences: list[str]) -> str:
    labels = {str(i): ("advice" if "good idea" in s else "description") for i, s in enumerate(sentences, 1)}
    return json.dumps(labels)


class ScriptedClient:
    """Answers drafts in order; a label request (one with ``format``) goes to ``labeler``."""

    def __init__(self, *drafts: str, labeler: Callable[[list[str]], str] = _flag_hedged):
        self.drafts = drafts
        self.labeler = labeler
        self.answer_calls: list[list[dict]] = []
        self.label_calls: list[dict] = []

    def chat_turn(self, model, messages, **kwargs):
        if kwargs.get("format"):
            self.label_calls.append({"messages": messages, **kwargs})
            numbered = re.findall(r"^(\d+)\. (.*)$", messages[-1]["content"], re.M)
            return ChatResult(content=self.labeler([s for _, s in numbered]))
        self.answer_calls.append([dict(m) for m in messages])
        draft = self.drafts[min(len(self.answer_calls), len(self.drafts)) - 1]
        return ChatResult(content=draft)


def _timeout(_sentences: list[str]) -> str:
    raise TimeoutError("timed out")


@pytest.fixture(autouse=True)
def synthetic_emr_dir(tmp_path: Path, monkeypatch) -> Path:
    emr = tmp_path / "EMR"
    emr.mkdir()
    monkeypatch.setenv("MOVER_EMR_DIR", str(emr))
    return emr


def _research(client: ScriptedClient):
    candidate = SearchCandidate(
        pid="caseA",
        source_label="analyzed case",
        analyzed=True,
        procedure="Laparoscopic Cholecystectomy",
        age=55.0,
        gender="M",
        source_text="PID caseA.",
        rank_method="embedding",
        score=0.9,
    )
    return answer_from_retrieval(
        question="high PIP",
        model="fake",
        candidates=[candidate],
        extracts=[EXTRACT],
        client=client,
    )


def _ask(client: ScriptedClient):
    cases = pd.DataFrame([{"PID": "caseA", "Age": 55, "Gender": "M"}])
    session = SessionState(cases=cases, timeseries=pd.DataFrame(), flags=pd.DataFrame())
    return run_agent(model="fake", question="q", session=session, client=client, skip_prefetch=True)


PATHS = pytest.mark.parametrize("answer", [_research, _ask], ids=["research", "ask"])


def _notes(answer: str) -> list[str]:
    return answer.split("**Verification note:**")[1:]


def test_patterns_miss_the_hedged_sentence():
    assert find_directive_phrases(HEDGED) == []


@PATHS
def test_model_flagged_advice_is_rewritten_and_the_clean_draft_ships(answer):
    client = ScriptedClient(f"{FACT} {HEDGED}", REWRITTEN)
    result = answer(client)
    assert len(client.answer_calls) == 2
    assert HEDGED in client.answer_calls[1][-1]["content"]
    assert HEDGED not in result.answer
    assert "FiO2 was weaned over 20 minutes." in result.answer
    assert _notes(result.answer) == []
    assert len(client.label_calls) == 2, "one label call per checked draft"


@PATHS
def test_advice_still_present_after_the_rewrite_is_named_in_the_note(answer):
    client = ScriptedClient(f"{FACT} {HEDGED}", f"{FACT} {HEDGED}")
    result = answer(client)
    assert "rewrite:still_flagged" in result.tool_trace
    assert any(HEDGED in note for note in _notes(result.answer)), result.answer


@PATHS
@pytest.mark.parametrize(
    "labeler",
    [lambda _s: "Sure! Sentence 1 is description.", _timeout],
    ids=["garbage", "timeout"],
)
def test_a_failed_advice_check_ships_the_answer_with_a_note(answer, labeler):
    client = ScriptedClient(f"{FACT} {HEDGED}", labeler=labeler)
    result = answer(client)
    assert len(client.answer_calls) == 1
    assert HEDGED in result.answer
    notes = _notes(result.answer)
    assert len(notes) == 1 and "advice check did not run" in notes[0]


@PATHS
def test_descriptive_answer_makes_one_label_call_and_no_rewrite(answer):
    draft = " ".join(
        [
            FACT,
            "PEEP was raised to 8 cmH2O at minute 40.",
            "SpO2 fell to 89% at minute 95.",
            "FiO2 was increased to 1.0 during the desaturation.",
            "No recruitment maneuver is documented.",
            "Extubation was recorded at minute 182.",
        ]
    )
    client = ScriptedClient(draft)
    result = answer(client)
    assert len(client.answer_calls) == 1
    assert len(client.label_calls) == 1
    assert _notes(result.answer) == []


def test_label_request_is_deterministic_bounded_and_without_thinking():
    client = ScriptedClient()
    check = check_advice(f"{FACT} {HEDGED}", client, "fake")
    assert check == Labeled(((FACT, "description"), (HEDGED, "advice")))
    assert check.advice == (HEDGED,)
    (call,) = client.label_calls
    assert call["temperature"] == 0
    assert call["think"] is False
    assert 0 < call["max_tokens"] <= 200
    assert call["timeout_s"] > 0


@pytest.mark.parametrize(
    "reply",
    [
        '{"1": "description"}',
        '{"1": "description", "2": "maybe"}',
        "[]",
    ],
    ids=["missing_label", "unknown_label", "not_an_object"],
)
def test_incomplete_labels_mean_the_check_did_not_run(reply):
    check = check_advice(f"{FACT} {HEDGED}", ScriptedClient(labeler=lambda _s: reply), "fake")
    assert isinstance(check, NotRun)
    assert check.advice == ()


def test_think_block_is_stripped_before_parsing():
    reply = '<think>sentence 2 hedges</think>{"1": "description", "2": "advice"}'
    check = check_advice(f"{FACT} {HEDGED}", ScriptedClient(labeler=lambda _s: reply), "fake")
    assert check.advice == (HEDGED,)


def test_no_model_means_the_check_did_not_run():
    client = ScriptedClient()
    assert isinstance(check_advice(FACT, client, ""), NotRun)
    assert client.label_calls == []


def test_split_sentences_handles_bullets_and_lines():
    text = "## Management\n- PEEP was 5. FiO2 was 0.4.\n* Consider a recruitment maneuver.\n\n---"
    assert split_sentences(text) == [
        "Management",
        "PEEP was 5.",
        "FiO2 was 0.4.",
        "Consider a recruitment maneuver.",
    ]
