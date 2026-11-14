"""Follow-up questions remember earlier answers (Slice A).

Privacy cases use invented PIDs only. Expected values come from the slice brief:
the advice check and the rewrite carry no history (the test plan's F14 said the
rewrite kept it; the brief wins).
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pandas as pd
import pytest

from src.llm import cloud
from src.llm.agent import WITHHELD_ANSWER, find_unstated_case_facts, run_agent
from src.llm.cloud import (
    CLAUDE_API,
    REAL_DATA_BLOCKED,
    CloudBlocked,
    CloudConfig,
    Trimmer,
    open_cloud_session,
)
from src.llm.grounding import REMOVED_MARK, find_ungrounded_numbers
from src.llm.history import (
    CLOUD_BACKENDS,
    BODY_CAP,
    PriorTurn,
    answer_body,
    follow_up_query,
    turns_to_send,
)
from src.llm.ollama_client import ChatResult, OllamaError
from src.llm.prompts import VERIFICATION_FOOTER, build_extract_system_prompt
from src.llm.retrieve import answer_from_articles, answer_from_minute, answer_from_retrieval
from src.llm.tools import SessionState
from src.services.articles import Article, ArticleCatalog, ArticleSearchResult
from src.services.retrieval import RetrievalResult, SearchCandidate

P1X = "P1X"
P2X = "P2X"
P3X = "P3X"
P4X = "P4X"
PID_SHAPE = "SYN0123456789"


def _labels(kwargs: dict) -> ChatResult:
    return ChatResult(content=json.dumps(dict.fromkeys(kwargs["format"]["required"], "description")))


class Recorder:
    """Keeps every message list. ``reply`` may be one string or a list consumed in order."""

    def __init__(self, reply: str | list[str] = "The extract describes a stable course."):
        self.reply = reply
        self.received: list[list[dict]] = []

    def chat_turn(self, model, messages, **kwargs):
        self.received.append([dict(message) for message in messages])
        if kwargs.get("format"):
            return _labels(kwargs)
        if isinstance(self.reply, list):
            text = self.reply.pop(0)
        else:
            text = self.reply
        return ChatResult(content=text, raw={"model": "served-model"})


def _candidate(pid: str, age: float = 58.0) -> SearchCandidate:
    return SearchCandidate(
        pid=pid, source_label="analyzed case", analyzed=True, procedure="Invented procedure",
        age=age, gender="F", source_text=f"PID {pid}.", rank_method="embedding", score=0.9,
    )


def _extract(pid: str) -> str:
    return f"SOURCE EXTRACT PID={pid}\nPIP median 22.\nCase facts: PID {pid}, age 58"


def _turn(
    question: str, answer: str, *pids: str, backend: str = CLAUDE_API, trimmed: bool = True,
    scope: str = "cases", state: str = "answered", sources: tuple[str, ...] = (),
    demo: bool = False,
) -> PriorTurn:
    return PriorTurn(
        question=question, answer=answer, sources=sources, pids=pids, backend=backend,
        trimmed=trimmed, scope=scope, state=state, demo=demo,
    )


def _answer_calls(recorder: Recorder) -> list[list[dict]]:
    """Calls that are not the structured advice check."""
    calls = []
    # Recorder stores messages only. Advice calls are the ones whose text is the advice prompt;
    # the cloud Remote records every call, and advice calls carry a format. Here we keep calls
    # whose first system message is the extract prompt, which the advice check does not use.
    system = build_extract_system_prompt(model="m")
    for messages in recorder.received:
        if messages and messages[0].get("content") == system:
            calls.append(messages)
    return calls


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MOVER_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.delenv(cloud.API_KEY_ENV, raising=False)
    emr = tmp_path / "EMR"
    emr.mkdir()
    monkeypatch.setenv("MOVER_EMR_DIR", str(emr))
    return tmp_path / "config"


def _ask(monkeypatch, *, reply, history=(), pids=(), real_data=True, question="What now?", candidates=None):
    remote = Recorder(reply)
    monkeypatch.setattr(cloud, "ClaudeApiClient", lambda **kwargs: remote)
    session = open_cloud_session(
        CloudConfig(CLAUDE_API, real_data_allowed=True) if real_data else CloudConfig(CLAUDE_API),
        real_data=real_data, pids=pids, history=history,
    )
    chosen = candidates if candidates is not None else [_candidate(pid) for pid in pids]
    result = answer_from_retrieval(
        question=question, model="m", candidates=chosen,
        extracts=[_extract(c.pid) for c in chosen], client=session.client, history=history,
    )
    return remote, session, result


def _outbound(remote: Recorder) -> str:
    return json.dumps(remote.received, ensure_ascii=False)


def _aliases(text: str) -> list[str]:
    return re.findall(r"Case-[A-Z]", text)


# ----- privacy -----


def test_p1_prior_case_numbers_are_aliased_with_one_alias_each(monkeypatch, config_dir):
    history = (
        _turn("What happened in the first?", f"{P1X} was stable.", P1X),
        _turn("And the other?", f"{P2X} was reviewed.", P2X),
    )
    remote, _session, result = _ask(
        monkeypatch, reply="Case-A and Case-B were stable.", history=history,
        pids=[P1X, P3X], question=f"What about {P1X} and {P3X} now?",
    )
    sent = _outbound(remote)
    for pid in (P1X, P2X, P3X):
        assert pid not in sent
    assert set(_aliases(sent)) >= {"Case-A", "Case-B", "Case-C"}
    first = remote.received[0]
    history_body = first[2]["content"]
    question = first[-1]["content"]
    alias = _aliases(history_body)[0]
    assert alias in question and alias in question
    assert alias in first[-1]["content"]
    # The same alias is used in the extract, which shares the user message with the question.
    assert history_body == f"{alias} was stable."
    assert P1X in result.answer and P3X in result.answer
    assert "Case-" not in result.answer


def test_p2_a_pid_only_in_a_prior_turn_is_still_aliased(monkeypatch, config_dir):
    history = (_turn("Earlier", f"See {P1X} for the first episode.", P1X),)
    remote, _session, _result = _ask(
        monkeypatch, reply="The course was stable.", history=history,
        pids=[P3X], question="What about the latest extract?",
    )
    assert P1X not in _outbound(remote)
    assert "Case-" in remote.received[0][2]["content"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("a 58-year-old", "a 55–59-year-old"),
        ("58 years old", "55–59 years old"),
        ("58 yo", "55–59 yo"),
        ("58 y/o", "55–59 y/o"),
        ("58F", "55–59F"),
        ("58 M", "55–59 M"),
        ("Age: 58", "Age: 55–59"),
        ("age 58", "age 55–59"),
        ("aged 58", "aged 55–59"),
        ("58.5-year-old", "55–59-year-old"),
        ("102 yo", "100–104 yo"),
        ("3 yo", "0–4 yo"),
        ("59 yo", "55–59 yo"),
        ("60 yo", "60–64 yo"),
        ("47yo", "45–49yo"),
        ("47 y.o.", "45–49 y.o."),
        ("47-year old", "45–49-year old"),
        ("47 year-old", "45–49 year-old"),
        ("47 yrs old", "45–49 yrs old"),
        ("47 yr old", "45–49 yr old"),
        ("age of 47", "age of 45–49"),
    ],
)
def test_p3_age_forms_are_banded(text, expected):
    assert Trimmer([]).outbound(text) == expected
    assert Trimmer([]).outbound(f"extract {text}") == f"extract {expected}"
    assert Trimmer([]).outbound(f"earlier answer {text}") == f"earlier answer {expected}"


@pytest.mark.parametrize(
    "text",
    ["58 mmHg", "58 min", "t=58", "PIP 58", "58 mg", "58 bpm", "minute -58", "1:2", "Case-A"],
)
def test_p3_a_banding_probe_leaves_non_ages_alone(text):
    assert Trimmer([]).outbound(text) == text


def test_p3_a_temperature_like_98_6f_may_be_over_banded():
    # Harmless: a wider band hides a temperature, it does not reveal a case.
    assert Trimmer([]).outbound("98.6F") == "95–99F"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("born 2021-03-04", "born [date not sent]"),
        ("on 03/04/2021", "on [date not sent]"),
        ("on 3/4/21", "on [date not sent]"),
    ],
)
def test_typed_dates_are_masked(text, expected):
    assert Trimmer([]).outbound(text) == expected
    assert Trimmer([]).outbound(f"extract {text}") == f"extract {expected}"


def test_p3_a_banded_restatement_is_not_a_wrong_age():
    session = SessionState(
        cases=pd.DataFrame([{"PID": P1X, "Age": 58, "Gender": "F", "Procedure_short": "Invented"}]),
        cited_pids=[P1X],
    )
    assert find_unstated_case_facts(f"{P1X} is a 55–59-year-old.", session) == []
    assert find_ungrounded_numbers("a 55–59-year-old", ["the 58-year-old"]) == []
    assert find_ungrounded_numbers(f"{P1X}: age 50-54.", ["Age: 58"]) == ["50", "54"]


def test_p4_a_local_answer_on_real_data_is_never_sent(monkeypatch, config_dir):
    secret = "age 58, PIP 31.4 at 2016-03-01 10:15, nurse note XYZ"
    local = _turn("What was the exact note?", secret, P1X, backend="ollama", trimmed=False)
    remote, session, _result = _ask(
        monkeypatch, reply="The course was stable.", history=(local,),
        pids=[P3X], question="And now?",
    )
    sent = _outbound(remote)
    for needle in (secret, "XYZ", "31.4", "What was the exact note?"):
        assert needle not in sent
    assert [message["role"] for message in remote.received[0]] == ["system", "user"]
    path = session.log(question="And now?", kind="question", answer="The course was stable.", trace=[])
    record = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    assert record["history"] == []
    assert "XYZ" not in path.read_text(encoding="utf-8")


def test_p4_only_trimmed_cloud_turns_go_out_of_a_mixed_thread(monkeypatch, config_dir):
    cloud1 = _turn("Q1", "A1 from cloud", P1X)
    local2 = _turn("Q2 local secret XYZ", "local body XYZ", P2X, backend="ollama", trimmed=False)
    cloud3 = _turn("Q3", "A3 from cloud", P3X)
    remote, _session, _result = _ask(
        monkeypatch, reply="The course was stable.", history=(cloud1, local2, cloud3),
        pids=[P1X], question="Q4",
    )
    first = remote.received[0]
    assert [message["role"] for message in first] == ["system", "user", "assistant", "user", "assistant", "user"]
    assert first[1]["content"] == "Earlier question: Q1"
    assert first[3]["content"] == "Earlier question: Q3"
    assert "XYZ" not in _outbound(remote)
    assert "Q2" not in _outbound(remote)


def test_p5_an_untrimmed_cloud_turn_is_not_sent_on_real_data(monkeypatch, config_dir):
    demo = _turn("demo question", "demo body with age 58", P1X, trimmed=False)
    remote, _session, _result = _ask(
        monkeypatch, reply="The course was stable.", history=(demo,),
        pids=[P3X], question="real question", real_data=True,
    )
    assert [message["role"] for message in remote.received[0]] == ["system", "user"]
    assert "demo body" not in _outbound(remote)


def test_p5_a_trimmed_prior_is_not_sent_on_fictional_data(monkeypatch, config_dir):
    """A trimmed cloud turn with no demo flag is real data, so a fictional question leaves it here.

    The old expectation sent that turn unchanged. Its stored answer has the real case
    numbers put back, and a fictional follow-up used to forward them untrimmed.
    """
    prior = _turn("trimmed question", "trimmed body age 58", P1X, trimmed=True)
    remote, _session, _result = _ask(
        monkeypatch, reply="The course was stable.", history=(prior,),
        pids=[P1X], question="fictional now", real_data=False,
    )
    sent = _outbound(remote)
    assert "trimmed question" not in sent and "trimmed body" not in sent
    assert [message["role"] for message in remote.received[0]] == ["system", "user"]


def test_p6_real_data_with_cloud_off_raises_before_a_client_and_leaks_no_history(monkeypatch, config_dir):
    built = []
    monkeypatch.setattr(cloud, "ClaudeApiClient", lambda **kwargs: built.append(kwargs))
    history = (_turn("secret question XYZAGE", "secret answer 31.4", P1X),)
    with pytest.raises(CloudBlocked) as refused:
        open_cloud_session(CloudConfig(CLAUDE_API), real_data=True, pids=[P1X], history=history)
    message = str(refused.value)
    assert message == REAL_DATA_BLOCKED
    assert "XYZAGE" not in message and "31.4" not in message and P1X not in message
    assert built == []
    assert list(config_dir.glob("ai-log/*.jsonl")) == []


def test_p7_three_turn_thread_message_list_and_advice_check_has_no_history(monkeypatch, config_dir):
    history = (
        _turn("Q1", "A1 body", P1X),
        _turn("Q2", "A2 body\n\n**Verification note:** drop this 91.\n" + VERIFICATION_FOOTER, P2X),
    )
    bare = Recorder()
    monkeypatch.setattr(cloud, "ClaudeApiClient", lambda **kwargs: bare)
    answer_from_retrieval(
        question="And in the second case?", model="m", candidates=[_candidate(P3X)],
        extracts=[_extract(P3X)],
        client=open_cloud_session(
            CloudConfig(CLAUDE_API, real_data_allowed=True), real_data=True, pids=[P3X],
        ).client,
    )
    remote = Recorder()
    monkeypatch.setattr(cloud, "ClaudeApiClient", lambda **kwargs: remote)
    answer_from_retrieval(
        question="And in the second case?", model="m", candidates=[_candidate(P3X)],
        extracts=[_extract(P3X)],
        client=open_cloud_session(
            CloudConfig(CLAUDE_API, real_data_allowed=True), real_data=True, pids=[P3X], history=history,
        ).client,
        history=history,
    )
    first = remote.received[0]
    assert [message["role"] for message in first] == [
        "system", "user", "assistant", "user", "assistant", "user",
    ]
    assert first[0]["content"] == bare.received[0][0]["content"] == build_extract_system_prompt(model="m")
    assert first[-1]["content"] == bare.received[0][-1]["content"]
    assert first[1]["content"].startswith("Earlier question: ")
    assert first[2]["content"] == "A1 body"
    assert "Verification note" not in first[4]["content"]
    assert "not clinical recommendations" not in first[4]["content"]
    assert "tool" not in {message["role"] for message in first}
    advice = [messages for messages in remote.received[1:] if len(messages) == 2]
    assert advice
    for messages in advice:
        assert "Earlier question:" not in json.dumps(messages)
        assert [message["role"] for message in messages] == ["system", "user"]


def test_p8_fictional_history_goes_out_unchanged_for_every_backend(monkeypatch, config_dir):
    # These turns were asked on fictional data. A real or unrecorded turn would not go out.
    history = (
        _turn(f"local ask {P1X}", f"local answer {P1X}", P1X, backend="ollama", trimmed=False, demo=True),
        _turn(f"cloud ask {P2X}", f"cloud answer {P2X}", P2X, backend=CLAUDE_API, trimmed=False, demo=True),
    )
    remote, _session, _result = _ask(
        monkeypatch, reply="The course was stable.", history=history, pids=[P1X],
        question="next", real_data=False,
    )
    sent = _outbound(remote)
    assert P1X in sent and P2X in sent
    assert "Case-A" not in sent
    assert [message["role"] for message in remote.received[0]] == [
        "system", "user", "assistant", "user", "assistant", "user",
    ]


def test_p9_a_local_model_receives_every_prior_turn_untrimmed():
    history = (
        _turn("cloud q", "cloud a age 58", P1X, trimmed=True),
        _turn("local q", "local a age 58", P2X, backend="ollama", trimmed=False),
    )
    recorder = Recorder()
    run_agent(
        model="m", question="next", session=SessionState(), client=recorder,
        skip_prefetch=True, allow_tools=False, extra_prefetched="PIP 22",
        sources=["PIP 22"], history=history,
    )
    first = _answer_calls(recorder)[0]
    assert [message["content"] for message in first[1:5]] == [
        "Earlier question: cloud q", "cloud a age 58",
        "Earlier question: local q", "local a age 58",
    ]


def test_p10_backend_switch_mid_thread_on_real_data(monkeypatch, config_dir):
    local = _turn("L", "local body", P1X, backend="ollama", trimmed=False)
    cloud_turn = _turn("C", "cloud body", P2X, trimmed=True)
    remote, _session, _result = _ask(
        monkeypatch, reply="The course was stable.", history=(local, local),
        pids=[P3X], question="now claude",
    )
    assert [message["role"] for message in remote.received[0]] == ["system", "user"]

    recorder = Recorder()
    run_agent(
        model="m", question="now local", session=SessionState(), client=recorder,
        skip_prefetch=True, allow_tools=False, extra_prefetched="PIP 22", sources=["PIP 22"],
        history=(cloud_turn, cloud_turn),
    )
    roles = [message["role"] for message in _answer_calls(recorder)[0]]
    assert roles == ["system", "user", "assistant", "user", "assistant", "user"]

    remote2, _session2, _result2 = _ask(
        monkeypatch, reply="The course was stable.",
        history=(cloud_turn, cloud_turn, local), pids=[P3X], question="back to claude",
    )
    contents = [message["content"] for message in remote2.received[0]]
    assert contents[1:5] == ["Earlier question: C", "cloud body", "Earlier question: C", "cloud body"]
    assert "local body" not in _outbound(remote2)


def test_p11_the_log_lists_the_trimmed_history_that_was_sent(monkeypatch, config_dir):
    monkeypatch.setenv(cloud.API_KEY_ENV, "sk-ant-TESTKEY-DO-NOT-LEAK")
    history = (
        _turn(f"Q {P1X}", f"A age 58 {P1X}", P1X),
        _turn("Q2", "A2", P2X),
    )
    remote, session, result = _ask(
        monkeypatch, reply="Case-A was stable.", history=history, pids=[P1X, P2X], question="And now?",
    )
    path = session.log(question="And now?", kind="question", answer=result.answer, trace=result.tool_trace)
    text = path.read_text(encoding="utf-8")
    record = json.loads(text.splitlines()[-1])
    assert record["calls"][0]["messages"] == remote.received[0]
    assert record["history"] == [
        {"question": remote.received[0][1]["content"].removeprefix("Earlier question: "),
         "answer": remote.received[0][2]["content"]},
        {"question": remote.received[0][3]["content"].removeprefix("Earlier question: "),
         "answer": remote.received[0][4]["content"]},
    ]
    outbound = json.dumps(record["calls"]) + json.dumps(record["history"])
    assert P1X not in outbound and P2X not in outbound and "age 58" not in outbound
    assert "sk-ant-TESTKEY" not in text


def test_p12_the_stored_answer_has_real_pids_and_no_alias(monkeypatch, config_dir):
    remote, _session, result = _ask(
        monkeypatch, reply="Case-A was stable.", history=(), pids=[P1X], question=f"What about {P1X}?",
    )
    assert P1X in result.answer
    assert "Case-" not in result.answer
    assert remote.received


def test_p13_a_history_question_with_a_pid_and_a_clock_time_is_trimmed(monkeypatch, config_dir):
    history = (_turn(f"what about {P2X} at 10:15?", "the earlier course was stable", P2X),)
    remote, _session, _result = _ask(
        monkeypatch, reply="The course was stable.", history=history, pids=[P1X],
        question="And the extract?",
    )
    sent = _outbound(remote)
    assert P2X not in sent and "10:15" not in sent
    assert "Earlier question:" in remote.received[0][1]["content"]


def test_p14_a_cancelled_or_failed_turn_is_not_history_and_a_failed_call_writes_no_log(monkeypatch, config_dir):
    kept = turns_to_send((
        _turn("ok", "body", P1X),
        _turn("nope", "failed body", P1X, state="failed"),
        _turn("stop", "cancelled body", P1X, state="cancelled"),
    ))
    assert [turn.question for turn in kept] == ["ok"]

    class Boom:
        def chat_turn(self, model, messages, **kwargs):
            raise cloud.CloudError("stopped")

    monkeypatch.setattr(cloud, "ClaudeApiClient", lambda **kwargs: Boom())
    session = open_cloud_session(
        CloudConfig(CLAUDE_API, real_data_allowed=True), real_data=True, pids=[P1X],
        history=(_turn("secret XYZAGE", "secret", P1X),),
    )
    with pytest.raises(OllamaError):
        answer_from_retrieval(
            question="next", model="m", candidates=[_candidate(P1X)], extracts=[_extract(P1X)],
            client=session.client, history=(_turn("secret XYZAGE", "secret", P1X),),
        )
    assert list(config_dir.glob("ai-log/*.jsonl")) == []


def test_cloud_backend_names_match_the_history_filter():
    assert CLOUD_BACKENDS == frozenset(cloud.CLOUD_BACKENDS)


# ----- follow-up correctness -----


def _run(recorder: Recorder, question: str, history=(), sources=("PIP 22",), scope="cases"):
    return run_agent(
        model="m", question=question, session=SessionState(), client=recorder,
        skip_prefetch=True, allow_tools=False, extra_prefetched="PIP 22",
        sources=list(sources), source_scope=scope, history=history,
    )


def test_f1_empty_history_leaves_messages_byte_identical():
    bare = Recorder()
    explicit = Recorder()
    _run(bare, "What happened?")
    _run(explicit, "What happened?", history=())
    assert bare.received[0] == explicit.received[0]
    assert [message["role"] for message in bare.received[0]] == ["system", "user"]


def test_f2_order_and_cap_keep_the_last_three_oldest_first():
    turns = tuple(_turn(f"Q{n}", f"A{n}", backend="ollama", trimmed=False) for n in range(1, 6))
    recorder = Recorder()
    _run(recorder, "Q6", history=turns)
    contents = [message["content"] for message in _answer_calls(recorder)[0]]
    assert contents[1:7] == [
        "Earlier question: Q3", "A3", "Earlier question: Q4", "A4", "Earlier question: Q5", "A5",
    ]
    both = Recorder()
    _run(both, "Q3", history=turns[:2])
    assert [message["content"] for message in _answer_calls(both)[0]][1:5] == [
        "Earlier question: Q1", "A1", "Earlier question: Q2", "A2",
    ]
    none = Recorder()
    _run(none, "Q1", history=())
    assert [message["role"] for message in _answer_calls(none)[0]] == ["system", "user"]


def test_f2_non_answers_are_dropped_before_the_cap():
    turns = (
        _turn("F1", "no", state="failed"),
        _turn("F2", "no", state="cancelled"),
        _turn("A1", "body1", backend="ollama", trimmed=False),
        _turn("A2", "body2", backend="ollama", trimmed=False),
        _turn("A3", "body3", backend="ollama", trimmed=False),
        _turn("A4", "body4", backend="ollama", trimmed=False),
    )
    sent = turns_to_send(turns)
    assert [turn.question for turn in sent] == ["A2", "A3", "A4"]


def test_f3_notes_and_the_footer_are_stripped_and_a_prose_note_stays():
    body = "The record says note 22 and keeps the mark [number not in sources]."
    answer = (
        body
        + "\n\n**Verification note:** first problem 11."
        + "\n\n**Verification note:** second problem 13."
        + VERIFICATION_FOOTER
    )
    sent = answer_body(answer)
    assert sent == body
    assert "Verification note" not in sent
    assert "not clinical recommendations" not in sent
    assert "note 22" in sent
    assert REMOVED_MARK in sent
    recorder = Recorder()
    _run(recorder, "next", history=(_turn("Q", answer, backend="ollama", trimmed=False),))
    assert _answer_calls(recorder)[0][2]["content"] == body


def test_f4_the_body_is_capped_at_2500_after_stripping():
    exact = "x" * BODY_CAP
    assert answer_body(exact) == exact
    longer = "y" * 2501
    assert answer_body(longer) == "y" * BODY_CAP
    assert answer_body("z" * 6000) == "z" * BODY_CAP
    short = "s" * 100
    noted = short + "\n\n**Verification note:** a long note " + ("9" * 3000)
    assert answer_body(noted) == short


def test_f5_earlier_bodies_ground_numbers_and_notes_do_not():
    earlier = _turn("Q", "the pressure was 42", backend="ollama", trimmed=False)
    kept = Recorder("the pressure was 42")
    result = _run(kept, "Again?", history=(earlier,), sources=("PIP 22",))
    assert "42" in result.answer and REMOVED_MARK not in result.answer

    dropped = Recorder("the pressure was 77")
    result = _run(dropped, "Again?", history=(earlier,), sources=("PIP 22",))
    assert "77" not in result.answer and REMOVED_MARK in result.answer
    assert not re.search(r"\d", REMOVED_MARK)

    huge = "b" * BODY_CAP + " 88"
    beyond = Recorder("see 88")
    result = _run(beyond, "Again?", history=(_turn("Q", huge, backend="ollama", trimmed=False),), sources=("PIP 22",))
    assert "88" not in result.answer

    noted = _turn(
        "Q", "the body is plain\n\n**Verification note:** the value 91 was checked.",
        backend="ollama", trimmed=False,
    )
    from_note = Recorder("see 91")
    result = _run(from_note, "Again?", history=(noted,), sources=("PIP 22",))
    assert "91" not in result.answer

    marked = _turn("Q", f"PIP was {REMOVED_MARK}.", backend="ollama", trimmed=False)
    back = Recorder("PIP was 63.")
    result = _run(back, "Again?", history=(marked,), sources=("PIP 22",))
    assert "63" not in result.answer


def test_f6_and_f7_retrieval_query_and_cited_cases(monkeypatch):
    from src.desktop.research_workers import ResearchWorker
    from src.llm.agent import AgentResult

    seen: dict = {}

    def capture(**kwargs):
        seen["question"] = kwargs["question"]
        seen["kind"] = kwargs["kind"]
        seen["pids"] = [candidate.pid for candidate in kwargs["candidates"]]
        seen["history"] = kwargs["history"]
        return AgentResult(answer="ok")

    monkeypatch.setattr("src.desktop.research_workers.answer_from_retrieval", capture)
    monkeypatch.setattr("src.llm.retrieve.load_medications", lambda pids: pd.DataFrame())

    class Engine:
        extract_limit = 3
        preset = "default"
        missing: set[str] = set()

        def __init__(self, ranked: list[str]):
            self.ranked = ranked
            self.queries: list[str] = []
            self.fetched: list[list[str]] = []

        def search(self, query, on_progress=None, **kwargs):
            self.queries.append(query)
            return RetrievalResult(candidates=tuple(_candidate(pid) for pid in self.ranked))

        def fetch_extracts(self, pids, on_progress=None, session_frames=None):
            self.fetched.append(list(pids))
            loaded, extracts = [], []
            for pid in pids:
                if pid in self.missing:
                    continue
                if len(loaded) >= self.extract_limit:
                    break
                loaded.append(pid)
                extracts.append(f"EXTRACT {pid} PIP 22")
            return extracts, loaded

        def candidates_for(self, pids):
            return [_candidate(pid) for pid in pids]

    session = SimpleNamespace(cases=None, timeseries=None, flags=None, episodes=None, events=None)
    alone = Engine([P2X])
    ResearchWorker(alone, "And the second?", "fake", session).run()
    assert alone.queries == ["And the second?"]

    prior = _turn("What happened in the first case?", "the first was stable", P1X, backend="ollama", trimmed=False)
    follow = Engine([P2X, P3X, P4X])
    boards = []
    worker = ResearchWorker(follow, "And the second?", "fake", session, history=(prior,))
    worker.shortlist.connect(boards.append)
    worker.run()
    # The board comes from the new question alone; the extracts are ranked again with the earlier question.
    assert follow.queries[0] == "And the second?" and len(boards) == 1
    assert follow.queries[1].index("What happened in the first case?") < follow.queries[1].index("And the second?")
    assert seen["pids"] == [P1X, P2X, P3X]
    assert seen["kind"] == "question"

    full = Engine([P4X])
    cited = _turn("Q", "A", P1X, P2X, P3X, backend="ollama", trimmed=False)
    ResearchWorker(full, "next", "fake", session, history=(cited,)).run()
    assert seen["pids"] == [P1X, P2X, P3X]

    duplicate = Engine([P1X, P2X, P3X])
    ResearchWorker(duplicate, "next", "fake", session, history=(prior,)).run()
    assert seen["pids"] == [P1X, P2X, P3X]
    assert seen["pids"].count(P1X) == 1

    dropped = Engine([P2X, P3X, P4X])
    dropped.missing = {P1X}
    ResearchWorker(dropped, "next", "fake", session, history=(prior,)).run()
    assert seen["pids"] == [P2X, P3X, P4X]

    older = _turn("older question", "older answer", P1X, backend="ollama", trimmed=False)
    newer = _turn("newer question", "newer answer", P2X, backend="ollama", trimmed=False)
    ordered = Engine([P3X, P4X, "P5X"])
    ResearchWorker(ordered, "And the second?", "fake", session, history=(older, newer)).run()
    assert seen["pids"] == [P2X, P1X, P3X]
    assert seen["history"][0].question == "older question"


@pytest.fixture
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_f8_reset_clears_history_and_a_late_answer_is_not_added(qapp, monkeypatch, synthetic_emr):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QMessageBox

    from src.desktop.app import MainWindow

    monkeypatch.setenv("MOVER_EMR_DIR", str(synthetic_emr))
    monkeypatch.setattr(MainWindow, "_startup", lambda self: None)
    monkeypatch.setattr("src.desktop.app.ResearchWorker.start", lambda self: None)
    monkeypatch.setattr(MainWindow, "_load_research_case", lambda *args: None)
    window = MainWindow()
    window.research.set_models(["test-model"])
    window._research_engine = SimpleNamespace(emr=synthetic_emr.resolve())
    try:
        window._start_research_search("first question")
        first = window._active_research_request
        window._on_research_answer(SimpleNamespace(answer="first answer 42"), first)
        window._on_research_done(first)
        assert window.research.conversation.history_before()[0].answer == "first answer 42"

        def confirm(self):
            next(button for button in self.buttons() if button.text() == "Clear").click()
            return 0

        monkeypatch.setattr(QMessageBox, "exec", confirm)
        window.research.conversation.reset_button.click()
        qapp.processEvents()
        assert window.research.conversation.turns == []
        window._on_research_answer(SimpleNamespace(answer="late answer"), first)
        assert window.research.conversation.turns == []

        window._start_research_search("second question")
        assert window._research_worker.history == ()
        assert window._research_worker.question == "second question"
    finally:
        window.close()
        qapp.processEvents()


def test_f9_ask_again_sends_the_turns_before_that_turn(qapp, monkeypatch, synthetic_emr):
    pytest.importorskip("PySide6")
    from src.desktop.app import MainWindow

    monkeypatch.setenv("MOVER_EMR_DIR", str(synthetic_emr))
    monkeypatch.setattr(MainWindow, "_startup", lambda self: None)
    monkeypatch.setattr("src.desktop.app.ResearchWorker.start", lambda self: None)
    monkeypatch.setattr(MainWindow, "_load_research_case", lambda *args: None)
    window = MainWindow()
    window.research.set_models(["test-model"])
    window._research_engine = SimpleNamespace(emr=synthetic_emr.resolve())
    try:
        for question, answer in (("Q1", "A1"), ("Q2", "A2"), ("Q3", "A3")):
            window._start_research_search(question)
            request = window._active_research_request
            window._on_research_answer(SimpleNamespace(answer=answer), request)
            window._on_research_done(request)
        second = window.research.conversation.turns[1]
        window.research.conversation.turn_widgets[second.request_id].ask_button.click()
        qapp.processEvents()
        history = window._research_worker.history
        assert [turn.question for turn in history] == ["Q1"]
        assert all(turn.question != "Q2" for turn in history)
        recorder = Recorder()
        _run(recorder, "Q2", history=history)
        earlier = [message["content"] for message in _answer_calls(recorder)[0] if message["content"].startswith("Earlier question:")]
        assert earlier == ["Earlier question: Q1"]
    finally:
        window.close()
        qapp.processEvents()


def test_f10_only_answered_turns_are_sent():
    turns = (
        _turn("run", "", state="running"),
        _turn("fail", "failed", state="failed"),
        _turn("cancel", "cancelled", state="cancelled"),
        _turn("none", "No AI answer.", state="no_model"),
        _turn("held", WITHHELD_ANSWER.format(problems="- names a case."), state="answered"),
        _turn("ok", "the real body", backend="ollama", trimmed=False),
    )
    recorder = Recorder()
    _run(recorder, "next", history=turns)
    contents = [message["content"] for message in _answer_calls(recorder)[0]]
    assert contents[1:3] == ["Earlier question: ok", "the real body"]
    assert "withheld" not in " ".join(contents)


def test_f11_article_history_stays_in_scope_and_a_pid_is_still_removed():
    articles = _turn("Which papers?", "Abstracts mention pressure.", scope="articles", backend="ollama", trimmed=False)
    cases = _turn("Which case?", f"See {P1X}.", P1X, backend="ollama", trimmed=False)
    recorder = Recorder(f"One paper cites {PID_SHAPE}.")
    result = _run(recorder, "And the next paper?", history=(articles, cases), sources=("abstract 22",), scope="articles")
    first = recorder.received[0]
    assert "Earlier question: Which papers?" in first[1]["content"]
    assert "Which case?" not in json.dumps(first)
    assert PID_SHAPE not in result.answer
    assert "case ID removed" in result.answer or "case-ID-like" in result.answer


def test_f12_a_minute_question_gets_history_and_only_its_own_extract():
    prior = _turn("What happened earlier?", "the earlier answer mentioned 42", backend="ollama", trimmed=False)
    recorder = Recorder("The minute was stable.")
    answer_from_minute(
        question="What about this minute?", model="m", candidate=_candidate(P1X),
        extract="MINUTE WINDOW ONLY token-minute PIP 22", client=recorder, history=(prior,),
    )
    first = _answer_calls(recorder)[0]
    assert first[1]["content"] == "Earlier question: What happened earlier?"
    assert "42" in first[2]["content"]
    user = first[-1]["content"]
    assert "token-minute" in user
    assert "the earlier answer" not in user


def test_f13_kind_comes_from_the_new_question_alone(monkeypatch):
    from src.desktop.research_workers import ResearchWorker
    from src.llm.agent import AgentResult
    from src.llm.intent import classify

    seen = {}

    def capture(**kwargs):
        seen["kind"] = kwargs["kind"]
        return AgentResult(answer="ok")

    monkeypatch.setattr("src.desktop.research_workers.answer_from_retrieval", capture)

    class Engine:
        extract_limit = 3
        preset = "default"
        queries: list[str] = []

        def search(self, query, on_progress=None, **kwargs):
            self.queries.append(query)
            return RetrievalResult(candidates=(_candidate(P1X),))

        def fetch_extracts(self, pids, on_progress=None, session_frames=None):
            return [f"EXTRACT {pids[0]} PIP 22"], list(pids[:1])

    earlier = _turn("58-year-old woman with high airway pressure", "matched one case", P1X, backend="ollama", trimmed=False)
    engine = Engine()
    session = SimpleNamespace(cases=None, timeseries=None, flags=None, episodes=None, events=None)
    ResearchWorker(engine, "And the second?", "fake", session, history=(earlier,)).run()
    combined = follow_up_query("And the second?", (earlier,))
    assert engine.queries == ["And the second?", combined], "the board first, then the ranking with the earlier question"
    assert seen["kind"] == classify("And the second?").kind == "question"
    assert classify(combined).kind == "scenario"


def test_f14_the_rewrite_carries_no_history():
    """The sealed brief: the rewrite repairs this draft and is not shown the thread."""
    history = (_turn("EARLIER-Q", "the earlier body", backend="ollama", trimmed=False),)
    recorder = Recorder(["PIP was 999.", "PIP was 22."])
    _run(recorder, "What was the pressure?", history=history, sources=("PIP 22",))
    prose = _answer_calls(recorder)
    assert len(prose) >= 2
    assert "Earlier question: EARLIER-Q" in prose[0][1]["content"]
    assert "Earlier question:" not in json.dumps(prose[1])
    assert prose[1][1]["role"] == "user"
    assert "PIP 22" in prose[1][1]["content"] or "Question" in prose[1][1]["content"]


def test_w3_editing_a_turn_does_not_change_the_history_that_was_recorded(qapp):
    pytest.importorskip("PySide6")
    from src.desktop.research_workspace import ResearchWorkspace

    view = ResearchWorkspace()
    view.set_models(["test-model"])
    view.data_is_demo = True
    view.begin_question("first", 1)
    view.set_answer("body 42", 1, backend=CLAUDE_API, trimmed=True, sources=("extract 42",))
    turn = view.conversation.turns[0]
    frozen = turn.sent
    turn.answer = "edited 99"
    turn.backend = "ollama"
    assert view.conversation.history_before() == (frozen,)
    assert frozen.answer == "body 42" and frozen.backend == CLAUDE_API and frozen.trimmed is True
    assert frozen.demo is True
    view.close()
    view.deleteLater()
    qapp.processEvents()


def test_worker_gives_the_trimmer_prior_pids(monkeypatch, config_dir):
    from src.desktop.research_workers import MinuteQuestionWorker, ResearchWorker

    remote = Recorder("The course was stable.")
    monkeypatch.setattr(cloud, "ClaudeApiClient", lambda **kwargs: remote)

    class Engine:
        extract_limit = 3
        preset = "default"

        def search(self, query, on_progress=None, **kwargs):
            return RetrievalResult(candidates=(_candidate(P3X),))

        def fetch_extracts(self, pids, on_progress=None, session_frames=None):
            return [_extract(P3X)], [P3X]

        def candidates_for(self, pids):
            return [_candidate(pid) for pid in pids]

    prior = _turn("earlier", f"see {P1X}", P1X)
    session = SimpleNamespace(cases=None, timeseries=None, flags=None, episodes=None, events=None)
    worker = ResearchWorker(
        Engine(), "now", "local-model", session,
        cloud=CloudConfig(CLAUDE_API, real_data_allowed=True), real_data=True, history=(prior,),
    )
    worker.run()
    assert P1X not in _outbound(remote)
    assert "Case-" in _outbound(remote)

    remote.received.clear()
    minute = MinuteQuestionWorker(
        _candidate(P3X), _extract(P3X), "this minute", "local-model",
        cloud=CloudConfig(CLAUDE_API, real_data_allowed=True), real_data=True, history=(prior,),
    )
    minute.run()
    assert P1X not in _outbound(remote)
    assert any(message["content"].startswith("Earlier question:") for messages in remote.received for message in messages)



def test_a_follow_up_board_and_article_results_come_from_the_new_question_alone(monkeypatch):
    """A question that matches nothing still shows an empty board, even in a thread."""
    from src.desktop.research_workers import ResearchWorker

    monkeypatch.setattr("src.desktop.research_workers.answer_from_retrieval",
                        lambda **kwargs: __import__("src.llm.agent", fromlist=["AgentResult"]).AgentResult(answer="ok"))

    class Engine:
        extract_limit = 3
        preset = "default"

        def __init__(self):
            self.queries: list[str] = []

        def search(self, query, on_progress=None, **kwargs):
            self.queries.append(query)
            hits = () if query == "xylophone tuning" else (_candidate(P2X),)
            return RetrievalResult(candidates=hits)

        def fetch_extracts(self, pids, on_progress=None, session_frames=None):
            return [f"EXTRACT {pid}" for pid in pids[:3]], list(pids[:3])

        def candidates_for(self, pids):
            return [_candidate(pid) for pid in pids]

    session = SimpleNamespace(cases=None, timeseries=None, flags=None, episodes=None, events=None)
    prior = _turn("What happened in the first case?", "the first was stable", P1X, backend="ollama", trimmed=False)
    boards = []
    worker = ResearchWorker(Engine(), "xylophone tuning", "fake", session, history=(prior,))
    worker.shortlist.connect(boards.append)
    worker.run()
    assert len(boards) == 1 and boards[0].candidates == ()



def test_a_case_number_nobody_listed_still_goes_out_aliased_and_comes_back(monkeypatch, config_dir):
    """The safety net: a prior answer naming a case its turn did not record is still aliased."""
    from src.llm.cloud import Trimmer

    unlisted, listed = "SYN0123456789", "0123456789abcdef"
    trimmer = Trimmer([listed])
    out = trimmer.outbound(f"{listed} and {unlisted} both rose; {unlisted} again.")
    assert unlisted not in out and listed not in out
    assert out == "Case-A and Case-B both rose; Case-B again."
    assert trimmer.inbound("Case-B was higher than Case-A.") == f"{unlisted} was higher than {listed}."
    crowded = Trimmer([f"SYN{index:010x}" for index in range(26)])
    assert crowded.outbound("SYN00000000ff") == "[case number not sent]"

    prior = PriorTurn(question="What happened?", answer=f"{unlisted} had a PIP episode.", pids=(),
                      backend="claude_api", trimmed=True)
    remote = Recorder()
    monkeypatch.setattr(cloud, "ClaudeApiClient", lambda **kwargs: remote)
    session = cloud.open_cloud_session(CloudConfig("claude_api", real_data_allowed=True), real_data=True,
                                       pids=[listed], history=(prior,))
    run_agent(model="m", question="And then?", session=SessionState(), client=session.client,
              allow_tools=False, sources=[f"PID {listed}."], history=(prior,))
    assert remote.received and all(unlisted not in str(messages) for messages in remote.received)


def test_a_real_local_turn_does_not_reach_claude_on_a_demo_question(monkeypatch, config_dir):
    secret = "REAL-LOCAL-NOTE-77"
    local = _turn(
        "what happened in the real case", f"incision note {secret}", P1X,
        backend="ollama", trimmed=False, demo=False,
    )
    remote, _session, _result = _ask(
        monkeypatch, reply="The course was stable.", history=(local,),
        pids=[P1X], question="demo follow-up", real_data=False,
    )
    sent = _outbound(remote)
    assert secret not in sent and "what happened in the real case" not in sent
    assert [message["role"] for message in remote.received[0]] == ["system", "user"]


def test_a_demo_turn_is_sent_on_a_demo_question_to_claude(monkeypatch, config_dir):
    prior = _turn("demo earlier", "demo body stays", P1X, backend="ollama", trimmed=False, demo=True)
    remote, _session, _result = _ask(
        monkeypatch, reply="The course was stable.", history=(prior,),
        pids=[P1X], question="demo follow-up", real_data=False,
    )
    assert remote.received[0][1]["content"] == "Earlier question: demo earlier"
    assert "demo body stays" in remote.received[0][2]["content"]


def test_a_demo_turn_is_sent_to_a_local_question():
    prior = _turn("demo earlier", "demo body stays", P1X, backend="ollama", trimmed=False, demo=True)
    real = _turn("real earlier", "real body stays", P1X, backend="ollama", trimmed=False, demo=False)
    recorder = Recorder()
    run_agent(
        model="m", question="next", session=SessionState(), client=recorder,
        skip_prefetch=True, allow_tools=False, extra_prefetched="PIP 22",
        sources=["PIP 22"], history=(prior, real),
    )
    contents = [message["content"] for message in _answer_calls(recorder)[0]]
    assert "Earlier question: demo earlier" in contents and "demo body stays" in contents
    assert "Earlier question: real earlier" in contents and "real body stays" in contents


def test_an_articles_question_to_a_cloud_service_sends_no_case_number(monkeypatch, config_dir):
    pid = "ab0123456789cdef"
    remote = Recorder("The papers describe pressure.")
    monkeypatch.setattr(cloud, "ClaudeApiClient", lambda **kwargs: remote)
    session = open_cloud_session(CloudConfig(CLAUDE_API), real_data=False)
    article = Article(
        id="ventilation", title="Fictional ventilation", year=2019, authors="Smith et al.",
        abstract="PEEP of 5 cmH2O was used.", doi_or_url="https://example.org/ventilation",
        source="synthetic", rights_note="fictional test article", imported_at="2026-10-17",
    )
    history = (
        _turn(f"earlier about {pid}", f"see {pid} in the note", scope="articles",
              backend="ollama", trimmed=False, demo=True),
    )
    answer_from_articles(
        question=f"what about {pid}?", model="m", articles=[article],
        client=session.client, history=history,
    )
    sent = _outbound(remote)
    assert pid not in sent and sent.count("[case number not sent]") >= 3
    local = Recorder("The papers describe pressure.")
    answer_from_articles(
        question=f"what about {pid}?", model="m", articles=[article], client=local, history=history,
    )
    assert pid in _outbound(local)


def test_a_stop_after_the_first_call_makes_no_second():
    class StopAfterFirst:
        def __init__(self):
            self.calls = 0
            self.stop = False

        def chat_turn(self, model, messages, **kwargs):
            self.calls += 1
            self.stop = True
            return ChatResult(content="The course was stable.")

    client = StopAfterFirst()
    with pytest.raises(InterruptedError):
        answer_from_retrieval(
            question="What now?", model="m", candidates=[_candidate(P1X)],
            extracts=[_extract(P1X)], client=client, should_stop=lambda: client.stop,
        )
    assert client.calls == 1


def test_workers_filter_demo_cloud_history_and_pass_cancellation(monkeypatch, config_dir):
    # Qt stays imported here, as in the other worker tests, so collecting this file does not load it.
    import src.desktop.research_workers as workers
    from src.desktop.research_workers import MinuteQuestionWorker, ResearchWorker

    real = _turn("real q", "REAL-SECRET body", P1X, backend="ollama", trimmed=False, demo=False)
    demo = _turn("demo q", "demo body stays", P1X, backend="ollama", trimmed=False, demo=True)
    recorded: list[dict] = []
    original = workers.turns_to_send

    def spy(history, **kwargs):
        recorded.append(kwargs)
        return original(history, **kwargs)

    monkeypatch.setattr(workers, "turns_to_send", spy)
    chosen = CloudConfig(CLAUDE_API)
    research = ResearchWorker(
        SimpleNamespace(), "q", None, None, cloud=chosen, real_data=False, history=(real, demo),
    )
    assert [turn.question for turn in research._sent_history()] == ["demo q"]
    assert recorded[-1]["to_cloud"] is True and recorded[-1]["real_data"] is False

    remote = Recorder("The minute was stable.")
    monkeypatch.setattr(cloud, "ClaudeApiClient", lambda **kwargs: remote)
    minute = MinuteQuestionWorker(
        _candidate(P1X), _extract(P1X), "demo minute?", "m",
        cloud=chosen, real_data=False, history=(real, demo),
    )
    minute.run()
    assert recorded[-1]["to_cloud"] is True and recorded[-1]["real_data"] is False
    sent = _outbound(remote)
    assert "REAL-SECRET" not in sent and "real q" not in sent and "demo body stays" in sent

    seen: dict = {}

    def retrieval(**kwargs):
        seen["retrieval"] = kwargs["should_stop"]
        return SimpleNamespace(answer="ok", tool_trace=[], sources=())

    def minute_answer(**kwargs):
        seen["minute"] = kwargs["should_stop"]
        return SimpleNamespace(answer="ok", tool_trace=[])

    def articles_answer(**kwargs):
        seen["articles"] = kwargs["should_stop"]
        return SimpleNamespace(answer="ok", tool_trace=[])

    monkeypatch.setattr(workers, "answer_from_retrieval", retrieval)
    monkeypatch.setattr(workers, "answer_from_minute", minute_answer)
    monkeypatch.setattr(workers, "answer_from_articles", articles_answer)

    class Engine:
        extract_limit = 3
        preset = "default"

        def search(self, query, on_progress=None, **kwargs):
            return RetrievalResult(candidates=(_candidate(P1X),))

        def fetch_extracts(self, pids, on_progress=None, session_frames=None):
            return [_extract(P1X)], list(pids[:1])

        def candidates_for(self, pids):
            return [_candidate(pid) for pid in pids]

    session = SimpleNamespace(cases=None, timeseries=None, flags=None, episodes=None, events=None)
    worker = ResearchWorker(Engine(), "now", "m", session, cloud=chosen, real_data=False, history=())
    worker.run()
    assert seen["retrieval"] == worker.isInterruptionRequested

    minute2 = MinuteQuestionWorker(
        _candidate(P1X), _extract(P1X), "q", "m", cloud=chosen, real_data=False,
    )
    minute2.run()
    assert seen["minute"] == minute2.isInterruptionRequested

    article = Article(
        id="ventilation", title="Fictional ventilation", year=2019, authors="Smith et al.",
        abstract="PEEP of 5 cmH2O was used.", doi_or_url="https://example.org/ventilation",
        source="synthetic", rights_note="fictional test article", imported_at="2026-10-17",
    )

    class Catalog(ArticleCatalog):
        def __init__(self):
            pass

        def search(self, query, **kwargs):
            return ArticleSearchResult(articles=(article,), catalog_count=1)

    papers = ResearchWorker(Catalog(), "papers?", "m", session, cloud=chosen, real_data=False)
    papers.run()
    assert seen["articles"] == papers.isInterruptionRequested
