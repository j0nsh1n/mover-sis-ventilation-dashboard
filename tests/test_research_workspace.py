"""Question, case, and source navigation in the research desktop view."""

from __future__ import annotations

import os
import time

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
