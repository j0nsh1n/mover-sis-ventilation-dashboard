"""Question, case, and source navigation in the research desktop view."""

from __future__ import annotations

import os
import time
from dataclasses import replace

import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QDialog, QTableWidget

from src.desktop.research_workspace import ResearchWorkspace
from src.services.retrieval import RetrievalResult, SearchCandidate


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _candidate(pid: str) -> SearchCandidate:
    return SearchCandidate(
        pid=pid,
        source_label="analyzed case",
        analyzed=True,
        procedure="Laparoscopic procedure",
        age=55,
        gender="M",
        source_text=f"PID {pid}. Procedure: Laparoscopic procedure.",
        rank_method="keyword",
        score=0.5,
    )


def test_question_case_source_and_return_preserve_context(qapp, monkeypatch):
    view = ResearchWorkspace()
    view.show()
    view.question_edit.setText("Where is peak pressure?")
    asked = []
    view.question_submitted.connect(asked.append)
    view.ask_button.click()
    assert asked == ["Where is peak pressure?"]

    view.set_results(RetrievalResult(candidates=(_candidate("caseA"), _candidate("caseB"))))
    requests = []
    view.case_requested.connect(lambda *args: requests.append(args))
    view.candidate_table.cellClicked.emit(1, 0)
    view.compare_button.click()
    assert requests == [("caseB", True, False)]

    timeseries = pd.DataFrame(
        {"t_min": [0, 1, 2], "PIP": [20.0, 33.0, 24.0], "ETCO2": [35.0, None, 37.0], "HR": [70, 72, 75]}
    )
    view.set_case_data("caseA", timeseries, pd.DataFrame())
    view.set_case_data("caseB", timeseries, pd.DataFrame(), comparison=True)
    assert view.comparison_card.isVisible()
    assert "caseA" in view.source_label.text()
    assert "33" in view.finding_title.text()

    view.open_detail("caseA")
    assert view.stack.currentIndex() == 1
    assert view.minute_slider.value() == 1
    assert "33" in view.sample_label.text()

    def inspect_dialog(self):
        table = self.findChild(QTableWidget)
        assert table is not None
        assert table.item(1, 1).text() == "33"
        assert table.item(1, 2).text() == "—"
        return 0

    monkeypatch.setattr(QDialog, "exec", inspect_dialog)
    view._show_current_source()
    view.return_to_canvas()
    assert view.stack.currentIndex() == 0
    assert view.question_edit.text() == "Where is peak pressure?"
    assert "caseB" in view.comparison_label.text()
    view.reset_button.click()
    assert view.question_edit.text() == ""
    assert view.candidate_table.rowCount() == 0
    assert not view.comparison_card.isVisible()
    assert view.answer_text.toPlainText() == ""
    view.close()


def test_mainwindow_retrieval_keyword_fallback(qapp, synthetic_emr, tmp_path, monkeypatch):
    monkeypatch.setenv("MOVER_EMR_DIR", str(synthetic_emr))
    monkeypatch.setenv("MOVER_PROCESSED_DIR", str(tmp_path / "processed"))
    monkeypatch.setenv("MOVER_SKIP_SETUP", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr("src.llm.ollama_client.OllamaEmbedder.is_available", lambda self: False)
    monkeypatch.setattr("src.desktop.app.MainWindow._refresh_ollama_models", lambda self, **kwargs: self.research.set_models([]))

    from src.desktop.app import MainWindow

    win = MainWindow()
    win.show()
    qapp.processEvents()
    win.research.question_edit.setText("laparoscopic pressure")
    win.research.ask_button.click()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        qapp.processEvents()
        if win.research.result is not None and "caseA" in win.research.source_label.text():
            break
        time.sleep(0.01)
    assert win.research.result is not None
    assert win.research.result.rank_method == "keyword"
    assert win.research.result.candidates[0].pid == "caseA"
    assert "caseA" in win.research.source_label.text()
    win.research.inspect_button.click()
    qapp.processEvents()
    assert win.research.stack.currentIndex() == 1
    win.research.return_to_canvas()
    win.research.question_edit.setText("nephrectomy")
    win.research.ask_button.click()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        qapp.processEvents()
        if win.research.ask_button.isEnabled() and not win.research.result.candidates:
            break
        time.sleep(0.01)
    assert win.research.result.candidates == ()
    assert win.research.candidate_table.rowCount() == 0
    assert win.research.finding_title.text() == "No matching cases"
    assert win.research.source_label.text() == "No source record matched this question."
    assert "No matching indexed surgery records" in win.research.answer_text.toPlainText()
    assert not win.research.inspect_button.isEnabled()
    win.close()


def test_startup_opens_setup_before_loading_when_emr_invalid(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("MOVER_EMR_DIR", str(tmp_path / "missing"))
    monkeypatch.delenv("MOVER_SKIP_SETUP", raising=False)
    from src.desktop.app import MainWindow

    calls = []
    monkeypatch.setattr(MainWindow, "_setup_bypassed", lambda self: False)
    monkeypatch.setattr(MainWindow, "_open_setup_wizard", lambda self, first_run=False: calls.append(("setup", first_run)))
    monkeypatch.setattr(MainWindow, "_autoload_processed", lambda self: calls.append(("autoload",)))
    monkeypatch.setattr(MainWindow, "_refresh_ollama_models", lambda self, **kwargs: calls.append(("models",)))
    win = MainWindow()
    win.show()
    qapp.processEvents()
    assert calls == [("setup", True)]
    win.close()


def test_results_select_first_case_with_ventilator_data(qapp):
    view = ResearchWorkspace()
    no_vent = replace(_candidate("caseN"), has_vent=False)
    view.set_results(RetrievalResult(candidates=(no_vent, _candidate("caseA"))))
    assert view.preferred_pid() == "caseA"
    assert view.candidate_table.currentRow() == 1
    assert view.source_title.text() == "caseA"
    assert "No case selected" not in view.source_label.text()

    view.show_case_unavailable("caseN", "no ventilator rows.")
    assert view.source_title.text() == "caseN"
    assert view.source_label.text() == "no ventilator rows."
    assert "caseN" in view.status_label.text()
    view.close()


def test_mainwindow_does_not_fetch_case_without_ventilator_data(qapp, synthetic_emr, tmp_path, monkeypatch):
    monkeypatch.setenv("MOVER_EMR_DIR", str(synthetic_emr))
    monkeypatch.setenv("MOVER_PROCESSED_DIR", str(tmp_path / "processed"))
    monkeypatch.setenv("MOVER_SKIP_SETUP", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr("src.desktop.app.MainWindow._refresh_ollama_models", lambda self, **kwargs: self.research.set_models([]))

    from src.desktop import app as desktop_app

    started = []
    monkeypatch.setattr(desktop_app.CaseFetchWorker, "start", lambda self: started.append(self.pid))
    win = desktop_app.MainWindow()
    no_vent = replace(_candidate("caseN"), has_vent=False)
    win.research.set_results(RetrievalResult(candidates=(no_vent,)))
    win._load_research_case("caseN", False, False)
    assert started == []
    assert "no ventilator rows" in win.research.source_label.text()
    win.close()


def test_cancel_search_restores_board_and_ignores_late_results(qapp, monkeypatch, synthetic_emr):
    from types import SimpleNamespace
    from src.desktop.app import MainWindow

    monkeypatch.setattr(MainWindow, "_startup", lambda self: None)
    monkeypatch.setenv("MOVER_EMR_DIR", str(synthetic_emr))
    win = MainWindow()
    original = RetrievalResult(candidates=(_candidate("caseA"), _candidate("caseB")))
    stopped = []

    class SlowEngine:
        emr = synthetic_emr.resolve()

        def search(self, question, on_progress):
            try:
                while True:
                    on_progress(SimpleNamespace(message="Searching slowly"))
                    time.sleep(0.005)
            finally:
                stopped.append(True)

    win._research_engine = SlowEngine()
    win.research._question = "Previous question"
    win.research.set_results(original)
    win.research.set_answer("Previous answer")
    try:
        win._start_research_search("New question")
        assert win.research.reset_button.isEnabled()
        assert not win.research.cancel_button.isHidden()
        win.research.cancel_button.click()
        assert win._research_worker.wait(1000)
        qapp.processEvents()
        win._research_worker.shortlist.emit(RetrievalResult(candidates=()))
        win._research_worker.answer.emit(SimpleNamespace(answer="Late answer"))
        qapp.processEvents()
        assert stopped
        assert win.research.result is original
        assert win.research.question_edit.text() == "Previous question"
        assert win.research.answer_text.toPlainText() == "Previous answer"
        assert win.research.ask_button.isEnabled()
    finally:
        win.close()


def test_reset_during_search_cancels_and_clears_board(qapp):
    view = ResearchWorkspace()
    cancelled = []
    view.cancel_requested.connect(lambda: cancelled.append(True))
    view.begin_question("A running question")
    view.reset_button.click()
    assert cancelled == [True]
    assert view.question_edit.text() == ""
    assert view.answer_text.toPlainText() == ""
    view.close()


def test_cancel_restores_comparison_and_is_keyboard_reachable_at_900px(qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    view = ResearchWorkspace()
    view.resize(900, 860)
    view.show()
    original = RetrievalResult(candidates=(_candidate("caseA"), _candidate("caseB")))
    view.set_results(original)
    ts = pd.DataFrame({"t_min": [0], "PIP": [25]})
    view.set_case_data("caseA", ts, pd.DataFrame())
    view.set_case_data("caseB", ts, pd.DataFrame(), comparison=True)
    view.begin_question("New question")
    view.activateWindow()
    qapp.processEvents()
    view.cancel_button.setFocus()
    QTest.keyClick(view.cancel_button, Qt.Key.Key_Tab)
    QTest.keyClick(view.focusWidget(), Qt.Key.Key_Tab, Qt.KeyboardModifier.ShiftModifier)
    assert view.cancel_button.hasFocus()
    qapp.processEvents()
    assert view.cancel_button.width() <= 900
    view.cancel_question()
    assert view.result is original
    assert view.comparison_card.isVisible()
    assert "caseB" in view.comparison_label.text()
    view.close()


def test_cancel_handles_selected_pid_missing_from_restored_candidates(qapp):
    view = ResearchWorkspace()
    original = RetrievalResult(candidates=(_candidate("caseA"),))
    view.set_results(original)
    view._selected_pid = "removed_pid"
    view.begin_question("New question")
    view.cancel_question()
    assert view.result is original
    assert view.ask_button.isEnabled()
    view.close()


@pytest.mark.parametrize("method,embedded,expected", [
    ("embedding", 18, "embedding, 18 of 30 records indexed; the rest ranked by keywords"),
    ("embedding", 30, "embedding, 30 of 30 records indexed"),
    ("keyword", 18, "ranking: keywords"),
])
def test_scope_label_states_embedding_coverage(qapp, method, embedded, expected):
    view = ResearchWorkspace()
    view.set_results(RetrievalResult(indexed_count=30, embedded_count=embedded, rank_method=method))
    assert expected in view.scope_label.text()
    if method == "keyword" or embedded == 30:
        assert "the rest" not in view.scope_label.text()
    view.close()
