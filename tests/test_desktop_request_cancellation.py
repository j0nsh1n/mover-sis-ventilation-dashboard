from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QApplication, QMessageBox

from src.desktop.app import MainWindow
from src.services.retrieval import RetrievalResult


@pytest.fixture
def window(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("MOVER_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("MOVER_PROCESSED_DIR", str(tmp_path / "processed"))
    monkeypatch.setattr(MainWindow, "_startup", lambda self: None)
    dialogs = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: dialogs.append(args[2]))
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: dialogs.append(args[2]))
    monkeypatch.setattr(QMessageBox, "open", lambda box: dialogs.append(box.text()))
    win = MainWindow()
    yield win, app, dialogs
    workers = list(getattr(win, "_research_workers", {}).values())
    for worker in [*workers, win._research_worker, win._case_worker, win._worker]:
        if worker is not None:
            worker.requestInterruption()
            assert worker.wait(5000)
    win.close()
    app.processEvents()


def pump(app, predicate):
    deadline = time.monotonic() + 2
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()
    assert predicate()


class ControlledSearch(QThread):
    progress = Signal(object)
    shortlist = Signal(object)
    answer = Signal(object)
    failed = Signal(str)
    gates: dict[str, threading.Event] = {}

    def __init__(self, engine, question, model, session, parent=None):
        super().__init__(parent)
        self.question = question

    def run(self):
        self.gates[self.question].wait(1)
        self.progress.emit(SimpleNamespace(message=f"progress {self.question}"))
        self.shortlist.emit(RetrievalResult(candidates=()))
        self.answer.emit(SimpleNamespace(answer=f"answer {self.question}"))
        self.failed.emit(f"late failure {self.question}")


def prepare_search(win, monkeypatch, synthetic_emr):
    monkeypatch.setenv("MOVER_EMR_DIR", str(synthetic_emr))
    monkeypatch.setattr("src.desktop.app.ResearchWorker", ControlledSearch)
    win._research_engine = SimpleNamespace(emr=synthetic_emr)


def test_cancel_then_new_question_runs_and_ignores_old_results(window, monkeypatch, synthetic_emr):
    win, app, _ = window
    prepare_search(win, monkeypatch, synthetic_emr)
    ControlledSearch.gates = {name: threading.Event() for name in ("old", "new")}
    win.research.set_answer("previous board")
    win._start_research_search("old")
    old = win._research_worker
    win._cancel_research()
    win._start_research_search("new")
    new = win._research_worker
    try:
        assert new is not old
        assert win.research.question_edit.text() == "new"
        ControlledSearch.gates["new"].set()
        pump(app, lambda: not new.isRunning())
        answer = win.research.answer_text.toPlainText()
        status = win.research.status_label.text()
        ControlledSearch.gates["old"].set()
        pump(app, lambda: not old.isRunning())
        assert win.research.answer_text.toPlainText() == answer
        assert win.research.status_label.text() == status
        assert "new" in answer
        assert win.research.ask_button.isEnabled()
    finally:
        for gate in ControlledSearch.gates.values():
            gate.set()
        old.wait(2000)
        new.wait(2000)


def test_case_failure_after_cancel_is_visible(window, monkeypatch, synthetic_emr):
    win, app, _ = window
    prepare_search(win, monkeypatch, synthetic_emr)
    ControlledSearch.gates = {"cancelled": threading.Event()}
    win._start_research_search("cancelled")
    win._cancel_research()
    ControlledSearch.gates["cancelled"].set()
    pump(app, lambda: not win._research_worker.isRunning())
    monkeypatch.setattr("src.desktop.research_workers.fetch_cases", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("synthetic case error")))
    win._load_research_case("SYN_missing", False, False)
    pump(app, lambda: not win._case_worker.isRunning())
    assert "synthetic case error" in win.research.status_label.text()
    assert "Loading this case" not in win.research.status_label.text()


def test_scan_cancel_has_status_and_no_dialog(window, monkeypatch):
    from src.pipeline.corpus_scan import ScanCancelled

    win, app, dialogs = window
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)

    def scan(*args, should_stop, **kwargs):
        while not should_stop():
            time.sleep(0.005)
        raise ScanCancelled("Full-EMR scan cancelled; the previous scan was kept.")

    monkeypatch.setattr("src.pipeline.corpus_scan.scan_corpus", scan)
    previous = object()
    win.corpus_scan = previous
    win._run_corpus_scan()
    win._cancel_corpus_scan()
    pump(app, lambda: not win._worker.isRunning())
    assert not dialogs
    assert win.corpus_scan is previous
    assert "Full-EMR scan cancelled; the previous scan was kept." in win.statusBar().currentMessage()


@pytest.mark.parametrize("failed", [[], [{"n_pids": 2, "error": "synthetic reason " * 40}, {"n_pids": 3, "error": "second"}]])
def test_scan_status_counts_surgeries_and_exposes_bounded_reason(window, monkeypatch, failed):
    win, _, _ = window
    monkeypatch.setattr(win, "_push_cases_overview", lambda: None)
    win._on_corpus_scan_ok(SimpleNamespace(meta={"n_scanned_cases": 12, "n_ventilated_surgeries": 20, "failed_shards": failed}))
    message = win.statusBar().currentMessage()
    assert "12 of 20 surgeries scored" in message
    if failed:
        assert "; 5 could not be scored (see details)" in message
        assert win.statusBar().toolTip() == failed[0]["error"][:300]
    else:
        assert "failed" not in message and "could not" not in message
        assert not win.statusBar().toolTip()


def test_absent_processed_autoload_names_path_after_setup(window, monkeypatch, tmp_path):
    win, _, dialogs = window
    root = tmp_path / "absent"
    monkeypatch.setenv("MOVER_PROCESSED_DIR", str(root))
    monkeypatch.setattr("src.desktop.app.needs_first_run_setup", lambda: False)
    win._autoload_processed()
    assert len(dialogs) == 1
    assert str(root) in dialogs[0]


def test_absent_processed_autoload_is_quiet_before_setup(window, monkeypatch):
    win, _, dialogs = window
    monkeypatch.setattr("src.desktop.app.needs_first_run_setup", lambda: True)
    win._autoload_processed()
    assert not dialogs
