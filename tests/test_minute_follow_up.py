"""The F follow-up answers one minute of one case from that case's rows alone."""

from __future__ import annotations

import json
import os
import threading
import time
from types import SimpleNamespace

import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from src.desktop.research_workers import ResearchWorker
from src.desktop.research_workspace import FOLLOW_UP_NO_MODEL, ResearchWorkspace
from src.llm.grounding import REMOVED_MARK
from src.llm.minute_context import build_minute_extract
from src.llm.ollama_client import ChatResult
from src.rules import load_rule_catalog
from src.services.case_fetch import FetchResult
from src.services.retrieval import RetrievalResult, SearchCandidate


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def empty_emr(tmp_path, monkeypatch):
    emr = tmp_path / "EMR"
    emr.mkdir()
    monkeypatch.setenv("MOVER_EMR_DIR", str(emr))


def candidate(pid: str = "caseA") -> SearchCandidate:
    return SearchCandidate(
        pid=pid, source_label="analyzed case", analyzed=True, procedure="Laparoscopic procedure",
        age=55, gender="M", source_text=f"PID {pid}. Procedure: Laparoscopic procedure.",
        rank_method="keyword", score=0.5,
    )


def minute_rows(count: int, first: int = 0) -> pd.DataFrame:
    return pd.DataFrame(
        {"PIP": [20.0 + i for i in range(count)], "ETCO2": [30.0 + i for i in range(count)], "HR": [70.0] * count},
        index=pd.Index([float(first + i) for i in range(count)], name="t_min"),
    )


def table_minutes(extract: str) -> list[float]:
    body = extract.split("t_min | ", 1)[1].splitlines()[1:]
    return [float(line.split(" | ")[0]) for line in body if " | " in line]


def build(rows, minute, **kwargs):
    return build_minute_extract(
        candidate=candidate(), rows=rows, minute=minute, signals=["PIP", "ETCO2", "HR"],
        episode=kwargs.pop("episode", None), **kwargs,
    )


@pytest.mark.parametrize(("minute", "first", "last"), [(15, 5, 25), (3, 0, 13), (28, 18, 29), (0, 0, 10)])
def test_window_is_ten_minutes_either_side_and_stops_at_the_case_edges(minute, first, last):
    extract = build(minute_rows(30), minute)
    minutes = table_minutes(extract)
    assert (minutes[0], minutes[-1]) == (first, last)
    assert f"SOURCE ROWS from {first} to {last} min" in extract


def test_missing_values_and_absent_minutes_are_written_as_missing():
    rows = minute_rows(12)
    rows.loc[5.0, "ETCO2"] = float("nan")
    rows.loc[6.0] = float("nan")
    extract = build(rows, 5)
    assert "At the selected minute: PIP 25; ETCO2 missing; HR 70" in extract
    assert "5 | 25 | missing | 70" in extract
    assert "6 | missing | missing | missing" in extract


def test_a_signal_the_case_lacks_is_missing_in_every_row():
    extract = build_minute_extract(
        candidate=candidate(), rows=minute_rows(4), minute=1, signals=["PIP", "SPO2", "PIP"], episode=None)
    assert "t_min | PIP (cmH₂O) | SPO2" in extract
    assert "1 | 21 | missing" in extract


def test_the_extract_has_the_search_record_and_no_other_case():
    extract = build(minute_rows(5), 2)
    assert extract.startswith("SOURCE EXTRACT (analyzed case) PID=caseA\nPID caseA. Procedure: Laparoscopic procedure.")
    assert extract.count("PID=") == 1


def test_no_episode_is_said_plainly():
    assert build(minute_rows(5), 2).endswith("No flagged episode covers the selected minute.")


def test_covering_episode_shows_label_severity_bounds_measurements_and_thresholds():
    episode = {"rule_id": "spo2_low", "severity": "critical", "t_start_min": 3.0, "t_end_min": 6.0, "value_mean": 90.5}
    rows = minute_rows(10).assign(SPO2=[98.0, 97.0, 96.0, 91.0, 89.0, float("nan"), 92.0, 97.0, 98.0, 98.0])
    extract = build(
        rows, 4, episode=episode, episode_label="Low SpO2", episode_signals=["SPO2"],
        threshold_lines=["Thresholds when flagged (default, abc123abc123)", "SpO2 below 92 %"],
    )
    assert "EPISODE COVERING THE SELECTED MINUTE: Low SpO2 (spo2_low), critical, 3 to 6 min" in extract
    assert "Mean flagged value: 90.5" in extract
    assert "SPO2: min 89, max 92, mean 90.6667; 1 missing minutes" in extract
    assert extract.endswith("Thresholds when flagged (default, abc123abc123)\nSpO2 below 92 %")


@pytest.fixture
def workspace(qapp):
    view = ResearchWorkspace()
    view.resize(900, 860)
    catalog = load_rule_catalog("default")
    view.set_rule_catalog(catalog)
    view.set_results(RetrievalResult(candidates=(candidate("caseA"), candidate("caseB"))))
    ts = pd.DataFrame({
        "PID": ["caseA"] * 6, "t_min": range(6), "PIP": [20.0, 21.0, 22.0, 23.0, 24.0, 25.0],
        "ETCO2": [30.0, 31.0, 32.0, 33.0, 34.0, 35.0], "HR": [70.0] * 6, "SPO2": [98.0, 91.0, 89.0, 90.0, 92.0, 98.0],
    })
    flags = pd.DataFrame([
        {"PID": "caseA", "t_min": minute, "rule_id": "spo2_low", "severity": "critical",
         "Obs_time": pd.Timestamp("2020-01-01") + pd.Timedelta(minutes=minute),
         "value": 90.0, "message": "Synthetic flag", "phase": "maintenance"}
        for minute in (1, 2, 3, 4)
    ])
    fetched = FetchResult(cases=pd.DataFrame({"PID": ["caseA"], "Age": [55]}), timeseries=ts, flags=flags,
                          events=pd.DataFrame({"PID": ["caseA"], "event": ["synthetic"]}))
    view.set_case_data("caseA", ts, flags, fetched=fetched, thresholds_sha256=catalog.thresholds_sha256)
    view.set_case_data("caseB", ts.assign(PID="caseB"), pd.DataFrame(), thresholds_sha256=catalog.thresholds_sha256,
                       comparison=True)
    view.show()
    view.open_detail("caseA")
    qapp.processEvents()
    yield view
    view.close()


def pump(qapp, done, what="the follow-up", timeout=5.0):
    deadline = time.monotonic() + timeout
    while not done() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.001)
    assert done(), f"timed out waiting for {what}"


class Describing:
    """Answers with a fixed draft, passes the advice check, and records every request."""

    def __init__(self, draft="The extract lists the selected minute.", gate=None):
        self.draft, self.sent, self.gate = draft, [], gate

    def chat_turn(self, model, messages, **kwargs):
        if kwargs.get("format"):
            return ChatResult(content=json.dumps(dict.fromkeys(kwargs["format"]["required"], "description")))
        assert not kwargs.get("tools")
        self.sent.append(str(messages))
        if self.gate is not None:
            assert self.gate.wait(5), "the test never released the model"
        return ChatResult(content=self.draft, tool_calls=[])


def use_client(monkeypatch, client):
    monkeypatch.setattr("src.desktop.research_workers.OllamaClient", lambda **_kwargs: client)


def ask(view, qapp, question="What happened here?", model="fake"):
    view.set_models([model] if model else [])
    view.follow_up_edit.setText(question)
    view.follow_up_button.click()
    if model:
        pump(qapp, lambda: view._follow_up_active is None)


def test_the_extract_carries_the_covering_episode_and_its_thresholds(workspace):
    workspace.minute_slider.setValue(2)
    extract = workspace._minute_extract("caseA", 2.0)
    assert "EPISODE COVERING THE SELECTED MINUTE:" in extract and "(spo2_low), critical, 1 to 4 min" in extract
    assert "SPO2: min 89, max 92, mean 90.5; 0 missing minutes" in extract
    assert "critical at most 88 %" in extract
    assert "Thresholds when flagged (default," in extract
    assert "caseB" not in extract
    assert "No flagged episode" in workspace._minute_extract("caseA", 5.0)


def test_extra_signals_the_user_chose_are_in_the_rows(workspace):
    workspace._user_extras = ["SPO2"]
    workspace._apply_extra_tracks()
    assert "t_min | PIP (cmH₂O) | ETCO2 (mmHg) | HR (bpm) | SPO2" in workspace._minute_extract("caseA", 2.0)


def test_the_answer_shows_under_the_field_labeled_with_minute_and_pid(workspace, qapp, monkeypatch):
    client = Describing("The extract lists **PIP 22** at the selected minute.")
    use_client(monkeypatch, client)
    workspace.minute_slider.setValue(2)
    ask(workspace, qapp)
    assert workspace.follow_up_label.text() == "Minute 2 · caseA"
    assert "PIP 22" in workspace.follow_up_answer.toPlainText()
    assert workspace.follow_up_edit.placeholderText() == "Ask about this minute"
    assert "PID=caseA" in client.sent[0] and "caseB" not in client.sent[0]
    assert workspace.conversation.turns == []


def test_enter_asks_the_question(workspace, qapp, monkeypatch):
    use_client(monkeypatch, Describing())
    workspace.set_models(["fake"])
    workspace.follow_up_edit.setText("What happened here?")
    workspace.follow_up_edit.returnPressed.emit()
    pump(qapp, lambda: workspace.follow_up_answer.isVisible() and "Asking" not in workspace.follow_up_answer.toPlainText())


def test_without_a_chat_model_it_says_so(workspace, qapp, monkeypatch):
    use_client(monkeypatch, Describing())
    ask(workspace, qapp, model=None)
    assert workspace.follow_up_answer.toPlainText() == FOLLOW_UP_NO_MODEL
    assert workspace.follow_up_label.text() == f"Minute {workspace.minute_slider.value()} · caseA"
    assert not workspace._follow_up_workers


def test_links_in_the_answer_are_inert(workspace, qapp, monkeypatch):
    use_client(monkeypatch, Describing("See [the page](https://example.com/page) for PIP 22."))
    workspace.minute_slider.setValue(2)
    ask(workspace, qapp)
    assert workspace.follow_up_answer.openLinks() is False and workspace.follow_up_answer.openExternalLinks() is False
    assert "href" not in workspace.follow_up_answer.toHtml()


def test_a_fabricated_number_in_the_answer_is_removed(workspace, qapp, monkeypatch):
    use_client(monkeypatch, Describing("PIP was 87 cmH2O at this minute, and PIP 22 earlier."))
    workspace.minute_slider.setValue(2)
    ask(workspace, qapp)
    shown = workspace.follow_up_answer.toPlainText()
    assert "87" not in shown and REMOVED_MARK in shown and "22" in shown


@pytest.mark.parametrize("change", ["minute", "case", "leave"])
def test_changing_minute_case_or_leaving_clears_the_answer(workspace, qapp, monkeypatch, change):
    use_client(monkeypatch, Describing())
    workspace.minute_slider.setValue(2)
    ask(workspace, qapp)
    assert workspace.follow_up_answer.isVisible()
    if change == "minute":
        workspace.minute_slider.setValue(3)
    elif change == "case":
        workspace.open_detail("caseB")
    else:
        workspace.return_to_canvas()
    assert not workspace.follow_up_answer.isVisible() and not workspace.follow_up_label.isVisible()
    assert workspace.follow_up_answer.toPlainText() == "" and workspace._follow_up_key is None


def test_rerendering_the_same_minute_keeps_the_answer(workspace, qapp, monkeypatch):
    use_client(monkeypatch, Describing())
    workspace.minute_slider.setValue(2)
    ask(workspace, qapp)
    workspace._render_minute(2)
    assert workspace.follow_up_answer.isVisible()


@pytest.mark.parametrize("change", ["minute", "case", "cancel"])
def test_a_late_answer_for_an_old_minute_or_case_is_dropped(workspace, qapp, monkeypatch, change):
    gate = threading.Event()
    client = Describing("Late answer.", gate=gate)
    use_client(monkeypatch, client)
    workspace.set_models(["fake"])
    workspace.minute_slider.setValue(2)
    workspace.follow_up_edit.setText("What happened here?")
    workspace.follow_up_button.click()
    pump(qapp, lambda: bool(client.sent), "the request to reach the model")
    assert workspace.follow_up_cancel.isVisible() and not workspace.follow_up_button.isEnabled()
    worker = next(iter(workspace._follow_up_workers.values()))
    if change == "minute":
        workspace.minute_slider.setValue(3)
    elif change == "case":
        workspace.open_detail("caseB")
    else:
        workspace.follow_up_cancel.click()
    gate.set()
    pump(qapp, lambda: worker.isFinished() and not workspace._follow_up_workers, "the late worker")
    qapp.processEvents()
    assert workspace.follow_up_answer.toPlainText() == "" and not workspace.follow_up_answer.isVisible()
    assert workspace.follow_up_button.isEnabled() and not workspace.follow_up_cancel.isVisible()


def test_an_answer_that_arrives_after_the_minute_changed_is_ignored_by_request_id(workspace, qapp, monkeypatch):
    gate = threading.Event()
    client = Describing("Late answer.", gate=gate)
    use_client(monkeypatch, client)
    workspace.set_models(["fake"])
    workspace.follow_up_edit.setText("What happened here?")
    workspace.follow_up_button.click()
    pump(qapp, lambda: bool(client.sent), "the request to reach the model")
    old_request = workspace._follow_up_active
    workspace.minute_slider.setValue(workspace.minute_slider.value() - 1)
    workspace._on_follow_up(old_request, "Late answer.")
    assert workspace.follow_up_answer.toPlainText() == "" and not workspace.follow_up_answer.isVisible()
    gate.set()
    pump(qapp, lambda: not workspace._follow_up_workers, "the worker to finish")


def test_a_new_question_after_cancel_gets_its_own_answer(workspace, qapp, monkeypatch):
    gate = threading.Event()
    first = Describing("Old answer.", gate=gate)
    use_client(monkeypatch, first)
    workspace.set_models(["fake"])
    workspace.follow_up_edit.setText("First?")
    workspace.follow_up_button.click()
    pump(qapp, lambda: bool(first.sent))
    workspace.follow_up_cancel.click()
    use_client(monkeypatch, Describing("New answer."))
    workspace.follow_up_button.click()
    gate.set()
    pump(qapp, lambda: "New answer." in workspace.follow_up_answer.toPlainText())
    qapp.processEvents()
    assert "Old answer." not in workspace.follow_up_answer.toPlainText()


def test_a_failed_request_is_reported_under_the_field(workspace, qapp, monkeypatch):
    class Broken:
        def chat_turn(self, *args, **kwargs):
            raise RuntimeError("the model is not running")

    use_client(monkeypatch, Broken())
    ask(workspace, qapp)
    assert workspace.follow_up_answer.toPlainText() == "The question could not be answered: the model is not running"
    assert workspace.follow_up_button.isEnabled()


def test_follow_up_and_conversation_questions_never_reach_each_other(workspace, qapp, monkeypatch):
    follow_up_client = Describing("FOLLOW_UP_ANSWER_SENTINEL at the selected minute.")
    use_client(monkeypatch, follow_up_client)
    workspace.minute_slider.setValue(2)
    ask(workspace, qapp, question="FOLLOW_UP_QUESTION_SENTINEL?")
    assert "FOLLOW_UP_ANSWER_SENTINEL" in workspace.follow_up_answer.toPlainText()

    conversation_client = Describing("CONVERSATION_ANSWER_SENTINEL for caseA.")
    use_client(monkeypatch, conversation_client)
    cases = (candidate("caseA"),)
    engine = SimpleNamespace(
        extract_limit=3, preset="default",
        search=lambda *args, **kwargs: RetrievalResult(candidates=cases, indexed_count=1, filtered_count=1),
        fetch_extracts=lambda pids, **kwargs: ([f"SOURCE EXTRACT PID={pid}\nPIP median 22" for pid in pids], tuple(pids)),
    )
    session = SimpleNamespace(cases=None, timeseries=None, flags=None, episodes=None, events=None)
    answers = []
    worker = ResearchWorker(engine, "CONVERSATION_QUESTION_SENTINEL?", "fake", session)
    worker.answer.connect(answers.append)
    worker.run()
    assert answers
    sent = " ".join(conversation_client.sent)
    assert "FOLLOW_UP" not in sent and "selected minute" not in sent.lower()

    use_client(monkeypatch, follow_up_client)
    ask(workspace, qapp, question="SECOND_FOLLOW_UP?")
    later = follow_up_client.sent[-1]
    assert "CONVERSATION_" not in later and "FOLLOW_UP_QUESTION_SENTINEL" not in later
    assert "FOLLOW_UP_ANSWER_SENTINEL" not in later
    assert workspace.conversation.turns == []
