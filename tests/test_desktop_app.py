"""Desktop app functionality and startup budget (offscreen Qt)."""

from __future__ import annotations

import os
import time

import pytest

# Force offscreen before Qt import
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")


def _qt_available() -> bool:
    try:
        from PySide6.QtWidgets import QApplication  # noqa: F401

        return True
    except ImportError:
        return False


requires_qt = pytest.mark.skipif(
    not _qt_available(),
    reason="PySide6/Qt system libs unavailable (e.g. missing libEGL)",
)


@pytest.fixture(scope="module")
def qapp():
    if not _qt_available():
        pytest.skip("PySide6/Qt system libs unavailable (e.g. missing libEGL)")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        try:
            QApplication.setAttribute(
                Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True
            )
        except Exception:
            pass
        app = QApplication([])
    return app


@requires_qt
def test_charts_pure_matplotlib_no_plotly():
    """Desktop chart module must not import plotly."""
    import src.desktop.charts as charts
    import sys

    # Ensure charts module did not pull plotly in
    mods_before = {m for m in sys.modules if m.startswith("plotly")}
    import importlib

    importlib.reload(charts)
    mods_after = {m for m in sys.modules if m.startswith("plotly")}
    assert mods_after == mods_before
    import pandas as pd

    cases = pd.DataFrame(
        {
            "PID": ["a", "b", "c"],
            "anomaly_score": [10, 5, 1],
            "Procedure_short": ["x", "y", "z"],
        }
    )
    fig = charts.top_cases_figure(cases, n=3)
    assert fig is not None
    flags = pd.DataFrame(
        {
            "rule_id": ["pip_high", "pip_high", "etco2_low"],
            "severity": ["warn", "critical", "warn"],
        }
    )
    fig2 = charts.flag_rules_figure(flags)
    assert fig2 is not None
    ts = pd.DataFrame(
        {
            "t_min": list(range(10)),
            "TV": [500] * 10,
            "PIP": list(range(15, 25)),
            "PEEP": [5] * 10,
            "ETCO2": [38] * 10,
            "Agent_Et": [1.5] * 10,
            "HR": [80] * 10,
            "SPO2": [99] * 10,
        }
    )
    fig3 = charts.case_timeline_figure(ts, show_vitals=True)
    assert fig3 is not None


@requires_qt
def test_chart_view_tall_figure_is_scrollable(qapp):
    """Case timelines are multi-panel; the PNG host must enable vertical scroll."""
    import pandas as pd

    from src.desktop.charts import ChartView, case_timeline_figure

    ts = pd.DataFrame(
        {
            "t_min": list(range(80)),
            "TV": [500] * 80,
            "PIP": list(range(15, 95)),
            "PEEP": [5] * 80,
            "ETCO2": [38] * 80,
            "Agent_Et": [1.5] * 80,
            "HR": [80] * 80,
            "SPO2": [99] * 80,
            "nMAP": [70] * 80,
        }
    )
    view = ChartView()
    view.resize(640, 280)  # short viewport → tall figure must scroll
    view.show()
    qapp.processEvents()
    view.set_figure(case_timeline_figure(ts, show_vitals=True))
    qapp.processEvents()

    assert not view._label.pixmap().isNull()
    # Content taller than the constrained viewport → vertical scrollbar range
    assert view._label.height() > view._scroll.viewport().height()
    assert view._scroll.verticalScrollBar().maximum() > 0
    view.close()


@requires_qt
def test_mainwindow_constructs_fast(qapp, monkeypatch, tmp_path):
    """Window shell should appear quickly even with no data."""
    monkeypatch.setenv("MOVER_PROCESSED_DIR", str(tmp_path / "empty_processed"))
    (tmp_path / "empty_processed").mkdir()

    from src.desktop.app import MainWindow

    t0 = time.perf_counter()
    win = MainWindow()
    construct_s = time.perf_counter() - t0
    win.show()
    qapp.processEvents()
    assert construct_s < 2.0, f"MainWindow construct took {construct_s:.2f}s"
    assert "MOVER Suite" in win.windowTitle()
    win.close()


@requires_qt
def test_mainwindow_loads_processed_data(qapp, pipeline_result, monkeypatch, tmp_path):
    """End-to-end: load processed outputs into the UI and populate selectors."""
    # Write pipeline outputs to a temp processed dir
    out = tmp_path / "ui_processed"
    out.mkdir(exist_ok=True)
    pipeline_result["cases"].to_parquet(out / "cases.parquet", index=False)
    pipeline_result["timeseries"].to_parquet(out / "timeseries.parquet", index=False)
    pipeline_result["flags"].to_parquet(out / "flags.parquet", index=False)
    if pipeline_result.get("episodes") is not None and not pipeline_result["episodes"].empty:
        pipeline_result["episodes"].to_parquet(out / "episodes.parquet", index=False)

    monkeypatch.setenv("MOVER_PROCESSED_DIR", str(out))

    from src.desktop.app import MainWindow

    win = MainWindow()
    win.show()
    # Process deferred autoload worker
    t0 = time.perf_counter()
    deadline = t0 + 10.0
    while time.perf_counter() < deadline:
        qapp.processEvents()
        if win.cases is not None and len(win.cases) > 0:
            break
        time.sleep(0.05)
    elapsed = time.perf_counter() - t0
    assert win.cases is not None and len(win.cases) > 0, "data never loaded"
    win.research.show_cases()
    assert win.research._cases_overview._proxy.rowCount() == len(win.cases)
    assert elapsed < 5.0, f"UI data ready took {elapsed:.2f}s (budget 5s)"
    # Charts refresh should not throw
    win.research.refresh_charts()
    qapp.processEvents()
    win.close()


@requires_qt
def test_startup_budget_with_data(qapp, pipeline_result, monkeypatch, tmp_path):
    """Total time from MainWindow() to interactive data < 3s offscreen."""
    out = tmp_path / "processed2"
    out.mkdir()
    pipeline_result["cases"].to_parquet(out / "cases.parquet", index=False)
    pipeline_result["timeseries"].to_parquet(out / "timeseries.parquet", index=False)
    pipeline_result["flags"].to_parquet(out / "flags.parquet", index=False)

    monkeypatch.setenv("MOVER_PROCESSED_DIR", str(out))
    from src.desktop.app import MainWindow

    t0 = time.perf_counter()
    win = MainWindow()
    win.show()
    while time.perf_counter() - t0 < 8.0:
        qapp.processEvents()
        if win.cases is not None:
            break
        time.sleep(0.02)
    # one more tick for deferred charts
    for _ in range(20):
        qapp.processEvents()
        time.sleep(0.02)
    total = time.perf_counter() - t0
    assert win.cases is not None
    assert total < 3.0, f"startup to data+charts {total:.2f}s exceeds 3s budget"
    win.close()


@requires_qt
def test_ollama_start_keeps_timer_responsive(qapp, monkeypatch):
    from PySide6.QtCore import QTimer
    from src.desktop.app import MainWindow
    from src.llm.service import OllamaStatus

    monkeypatch.setattr(MainWindow, "_startup", lambda self: None)
    calls = []

    def slow_start(**kwargs):
        calls.append(kwargs)
        time.sleep(2)
        return OllamaStatus(True, "Ready", ["synthetic-model"], "http://127.0.0.1:11434")

    monkeypatch.setattr("src.llm.service.start_ollama", slow_start)
    win = MainWindow()
    ticks = []
    timer = QTimer(win)
    timer.setSingleShot(True)
    timer.timeout.connect(lambda: ticks.append(time.perf_counter()))
    started = time.perf_counter()
    timer.start(10)
    try:
        win.research.start_action.trigger()
        first_worker = win._ollama_worker
        win._ollama_start()
        assert win._ollama_worker is first_worker
        assert win.research.start_action.text() == "Starting…"
        assert not win.research.start_action.isEnabled()
        while not ticks and time.perf_counter() - started < 0.1:
            qapp.processEvents()
            time.sleep(0.001)
        assert ticks and ticks[0] - started < 0.1
        assert first_worker.wait(3000)
        qapp.processEvents()
        assert len(calls) == 1
        assert win.research.start_action.isEnabled()
        assert "ready" in win.research.status_label.text().lower()
    finally:
        win.close()


@requires_qt
def test_ollama_failure_names_reason(qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from src.desktop.app import MainWindow

    monkeypatch.setattr(MainWindow, "_startup", lambda self: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: None)
    monkeypatch.setattr("src.llm.service.start_ollama", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("port busy")))
    win = MainWindow()
    try:
        win._ollama_start()
        assert win._ollama_worker.wait(1000)
        qapp.processEvents()
        assert "port busy" in win.research.status_label.text()
        assert win.research.start_action.isEnabled()
    finally:
        win.close()


@requires_qt
def test_research_failure_reason_survives_status_updates(qapp, monkeypatch):
    from src.desktop.app import MainWindow

    monkeypatch.setattr(MainWindow, "_startup", lambda self: None)
    win = MainWindow()
    try:
        win._on_research_fail("connection timed out after 30 seconds")
        win.statusBar().showMessage("Ready")
        win.research.set_status("Another status update")
        assert "connection timed out after 30 seconds" in win.research.answer_text.toPlainText()
        win.research.begin_question("Next question")
        assert win.research.answer_text.toPlainText() == ""
    finally:
        win.close()


@requires_qt
def test_scan_partial_failures_report_case_counts(qapp, monkeypatch):
    from types import SimpleNamespace
    from src.desktop.app import MainWindow

    monkeypatch.setattr(MainWindow, "_startup", lambda self: None)
    monkeypatch.setattr(MainWindow, "_push_cases_overview", lambda self: None)
    win = MainWindow()
    try:
        win._on_corpus_scan_ok(SimpleNamespace(meta={
            "n_scanned_cases": 12, "n_ventilated_surgeries": 20,
            "failed_shards": [{"n_pids": 5}, {"n_pids": 3}],
        }))
        assert "12 of 20 surgeries scored; 8 could not be scored (see details)" in win.statusBar().currentMessage()
    finally:
        win.close()


@requires_qt
def test_scenario_agents_come_from_the_scan_and_the_sample_wins(qapp, tmp_path, monkeypatch):
    from types import SimpleNamespace

    import pandas as pd
    from src.desktop.app import MainWindow

    monkeypatch.setattr(MainWindow, "_startup", lambda self: None)
    monkeypatch.setattr(MainWindow, "_push_cases_overview", lambda self: None)
    monkeypatch.setenv("MOVER_CONFIG_DIR", str(tmp_path / "config"))
    win = MainWindow()
    try:
        assert win._case_agents() == {}
        win.cases = pd.DataFrame({"PID": ["A"], "primary_agent_name": ["desflurane"]})
        win._research_engine = object()
        win._on_corpus_scan_ok(SimpleNamespace(
            cases=pd.DataFrame({"PID": ["A", "B", "C"], "primary_agent_name": ["sevoflurane", "isoflurane", None]}),
            meta={"n_scanned_cases": 3, "n_ventilated_surgeries": 3, "failed_shards": []},
        ))
        assert win._research_engine is None
        assert win._case_agents() == {"A": "desflurane", "B": "isoflurane"}
        engine = win._new_research_engine(tmp_path)
        assert engine.case_agents == {"A": "desflurane", "B": "isoflurane"} and engine.agents_from_scan
        assert engine.analyzed_pids == {"A"}
    finally:
        win.close()


@requires_qt
def test_a_scan_that_finishes_after_the_emr_changed_is_not_loaded(qapp, tmp_path, monkeypatch):
    from types import SimpleNamespace

    import pandas as pd
    from src.desktop.app import MainWindow
    from src.pipeline.corpus_scan import emr_fingerprint

    old, new = tmp_path / "old", tmp_path / "new"
    for emr in (old, new):
        emr.mkdir()
        (emr / "patient_information.csv").write_text(f"PID\n{emr.name}\n")
    monkeypatch.setattr(MainWindow, "_startup", lambda self: None)
    monkeypatch.setattr(MainWindow, "_push_cases_overview", lambda self: None)
    monkeypatch.setenv("MOVER_EMR_DIR", str(new))
    win = MainWindow()
    try:
        meta = {"emr_fingerprint": emr_fingerprint(old), "n_scanned_cases": 1,
                "n_ventilated_surgeries": 1, "failed_shards": []}
        win._on_corpus_scan_ok(SimpleNamespace(cases=pd.DataFrame({"PID": ["X"]}), meta=meta))
        assert win.corpus_scan is None
        assert "previous data folder" in win.statusBar().currentMessage()
        meta["emr_fingerprint"] = emr_fingerprint(new)
        win._on_corpus_scan_ok(SimpleNamespace(cases=pd.DataFrame({"PID": ["X"]}), meta=meta))
        assert win.corpus_scan is not None
    finally:
        win.close()


@requires_qt
def test_corrupt_scan_dialog_names_file(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from src.desktop.app import MainWindow

    monkeypatch.setattr(MainWindow, "_startup", lambda self: None)
    monkeypatch.setenv("MOVER_PROCESSED_DIR", str(tmp_path))
    cases = tmp_path / "corpus_cases.parquet"
    cases.write_bytes(b"not parquet")
    (tmp_path / "corpus_meta.json").write_text("{}")
    dialogs = []
    monkeypatch.setattr(QMessageBox, "open", lambda box: dialogs.append(box.text()))
    win = MainWindow()
    try:
        win._load_corpus_scan()
        assert win.corpus_scan is None
        assert len(dialogs) == 1 and "scan-load:ArrowInvalid" in dialogs[0]
        assert str(cases) not in dialogs[0]
    finally:
        win.close()


@requires_qt
def test_missing_processed_dialog_names_path(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from src.desktop.app import MainWindow

    monkeypatch.setattr(MainWindow, "_startup", lambda self: None)
    monkeypatch.setenv("MOVER_PROCESSED_DIR", str(tmp_path / "missing"))
    dialogs = []
    monkeypatch.setattr(QMessageBox, "open", lambda box: dialogs.append(box.text()))
    win = MainWindow()
    try:
        monkeypatch.setattr("src.desktop.app.needs_first_run_setup", lambda: False)
        win._autoload_processed()
        assert "Missing processed files: cases.parquet" in dialogs[0]
        assert "choose Run pipeline" in dialogs[0]
        assert str(tmp_path / "missing") not in dialogs[0]
    finally:
        win.close()


@requires_qt
def test_scan_cancel_requests_interruption_and_keeps_previous(qapp, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox
    from src.desktop.app import MainWindow
    from src.pipeline.corpus_scan import ScanCancelled

    monkeypatch.setattr(MainWindow, "_startup", lambda self: None)
    monkeypatch.setenv("MOVER_PROCESSED_DIR", str(tmp_path))
    dialogs = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: dialogs.append(args[2]))

    def scan(*args, should_stop, **kwargs):
        while not should_stop():
            time.sleep(0.005)
        raise ScanCancelled("Full-EMR scan cancelled; the previous scan was kept.")

    monkeypatch.setattr("src.pipeline.corpus_scan.scan_corpus", scan)
    win = MainWindow()
    previous = object()
    win.corpus_scan = previous
    try:
        win._run_corpus_scan()
        assert not win._scan_cancel_button.isHidden()
        win._scan_cancel_button.click()
        assert win._worker.wait(1000)
        qapp.processEvents()
        assert win.corpus_scan is previous
        assert not dialogs
        assert "Full-EMR scan cancelled; the previous scan was kept." in win.statusBar().currentMessage()
        assert win._scan_cancel_button.isHidden()
    finally:
        win.close()


_PROBE_FAILURE = (
    "Ollama API not reachable (Cannot reach Ollama at http://127.0.0.1:11434. "
    "Start it with: ollama serve. (<urlopen error [Errno 111] Connection refused>))"
)


@requires_qt
def test_load_warning_dismiss_focuses_the_research_question(qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from src.desktop.app import MainWindow

    monkeypatch.setattr(MainWindow, "_startup", lambda self: None)
    win = MainWindow()
    win.show()
    qapp.processEvents()
    try:
        win._warn("Load error", "Missing processed files: cases.parquet")
        qapp.processEvents()
        box = win.findChild(QMessageBox)
        assert box is not None
        box.button(QMessageBox.StandardButton.Ok).click()
        for _ in range(4):
            qapp.processEvents()
        edit = win.research.question_edit
        assert edit.isVisible()
        assert edit.accessibleName() == "Research question"
        assert qapp.focusWidget() is edit
        assert edit.hasFocus()
    finally:
        win.close()


@requires_qt
def test_pipeline_error_dialog_never_shows_folder_paths_in_the_status_bar(qapp, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox
    from src.desktop.app import MainWindow

    emr = tmp_path / "emr-a-bad"
    processed = tmp_path / "processed"
    monkeypatch.setenv("MOVER_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("MOVER_EMR_DIR", str(emr))
    monkeypatch.setenv("MOVER_PROCESSED_DIR", str(processed))
    monkeypatch.setattr(MainWindow, "_startup", lambda self: None)
    shown = []

    def critical(parent, title, text):
        shown.append((title, text, parent.statusBar().currentMessage()))

    monkeypatch.setattr(QMessageBox, "critical", critical)
    win = MainWindow()
    try:
        assert str(emr) not in win.statusBar().currentMessage()
        win._on_pipeline_fail(
            "The operation could not finish (pipeline:DataValidationError). "
            "Details are in app-errors.log in the settings folder."
        )
        assert len(shown) == 1
        title, text, during = shown[0]
        assert title == "Pipeline / guardrail error"
        assert "pipeline:DataValidationError" in text
        assert str(emr) not in text and str(processed) not in text
        assert during == "Pipeline failed"
        assert str(emr) not in during and str(processed) not in during
        after = win.statusBar().currentMessage()
        assert after == "Pipeline failed"
        assert str(emr) not in after and str(processed) not in after
        # the folders are in the source field's tooltip and in Settings, nowhere else in the footer
        tip = win._footer.source.toolTip()
        assert str(emr) in tip and str(processed) in tip
    finally:
        win.close()


@requires_qt
def test_pipeline_dialog_keeps_paths_out_of_a_nested_reload_and_keeps_the_latest_status(
    qapp, monkeypatch, tmp_path
):
    from PySide6.QtCore import QObject, QTimer, Signal
    from PySide6.QtWidgets import QMessageBox
    from src.desktop.app import MainWindow

    emr = tmp_path / "emr-a-bad"
    processed = tmp_path / "processed"
    monkeypatch.setenv("MOVER_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("MOVER_EMR_DIR", str(emr))
    monkeypatch.setenv("MOVER_PROCESSED_DIR", str(processed))
    monkeypatch.setattr(MainWindow, "_startup", lambda self: None)

    class _Emitter(QObject):
        finished = Signal()

    win = MainWindow()
    win.show()
    qapp.processEvents()
    emitter = _Emitter(win)
    samples: list[str] = []
    bailed: list[bool] = []
    latest = "Loaded 4 cases"

    def pipeline_box():
        boxes = [
            box
            for box in win.findChildren(QMessageBox)
            if box.windowTitle() == "Pipeline / guardrail error"
        ]
        return boxes[-1] if boxes else None

    def queued_status() -> None:
        win._set_status(latest)
        samples.append(win.statusBar().currentMessage())
        box = pipeline_box()
        assert box is not None
        box.button(QMessageBox.StandardButton.Ok).click()

    def during_dialog() -> None:
        assert pipeline_box() is not None
        samples.append(win.statusBar().currentMessage())
        win._worker = emitter
        win._reload_after_scan = True
        emitter.finished.connect(win._after_data_job)
        emitter.finished.emit()
        samples.append(win.statusBar().currentMessage())
        QTimer.singleShot(0, queued_status)

    def bail() -> None:
        bailed.append(True)
        box = pipeline_box()
        if box is not None:
            box.accept()

    try:
        assert str(emr) not in win.statusBar().currentMessage()
        QTimer.singleShot(0, during_dialog)
        QTimer.singleShot(3000, bail)
        win._on_pipeline_fail(
            "The operation could not finish (pipeline:DataValidationError). "
            "Details are in app-errors.log in the settings folder."
        )
        assert bailed == []
        assert len(samples) == 3
        for message in samples:
            assert str(emr) not in message
            assert str(processed) not in message
            assert " ·  EMR:" not in message
        assert samples[0] == "Pipeline failed"
        assert samples[1].startswith("No processed cache")
        assert samples[2] == latest
        after = win.statusBar().currentMessage()
        assert after == latest
        assert str(emr) not in after and str(processed) not in after
        tip = win._footer.source.toolTip()
        assert str(emr) in tip and str(processed) in tip
    finally:
        win._worker = None
        for box in win.findChildren(QMessageBox):
            box.accept()
        win.close()


@requires_qt
def test_pipeline_dialog_preserves_a_newer_direct_status(qapp, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox
    from src.desktop.app import MainWindow

    monkeypatch.setenv("MOVER_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(MainWindow, "_startup", lambda self: None)
    latest = "Version 9.9.9 is ready. Restart when convenient."

    def critical(parent, _title, _text):
        parent.statusBar().showMessage(latest)
        assert str(tmp_path) not in parent.statusBar().currentMessage()

    monkeypatch.setattr(QMessageBox, "critical", critical)
    win = MainWindow()
    try:
        win._on_pipeline_fail("pipeline:DataValidationError")
        assert win.statusBar().currentMessage() == latest
    finally:
        win.close()


@requires_qt
def test_status_and_scope_lines_fit_default_window_with_probe_error(qapp, monkeypatch, tmp_path):
    from src.desktop.app import MainWindow

    monkeypatch.setenv("MOVER_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(MainWindow, "_startup", lambda self: None)
    win = MainWindow()
    win.resize(1380, 860)
    win.show()
    qapp.processEvents()
    try:
        research = win.research
        research.set_model_status("Ollama: stopped — click Start to activate")
        research.set_status(_PROBE_FAILURE)
        research.scope_label.setText("Indexed surgeries: — · analyzed cases: —")
        qapp.processEvents()
        _assert_lines_readable(win, _PROBE_FAILURE)
        longer = _PROBE_FAILURE + " " + ("extra detail " * 40)
        research.set_status(longer)
        qapp.processEvents()
        status = research.status_label
        assert longer in status.text()
        assert status.height() + 1 >= status.heightForWidth(max(status.width(), 1))
        _assert_lines_readable(win, longer)
    finally:
        win.close()


def _assert_lines_readable(win, expected: str) -> None:
    """The Ollama status, the reason and the scope line are whole and sit on the screen, one under the other."""
    research = win.research
    model = research.model_status
    status = research.status_label
    scope = research.scope_label
    viewport = research.board_scroll.viewport()
    assert model.isVisible() and "Ollama" in model.text()
    assert expected in status.text()
    for label in (model, status, scope):
        assert label.height() + 1 >= label.heightForWidth(max(label.width(), 1)), label.text()
    # Page coordinates: the reason sits under the Ollama line, and both sit above the evidence trail.
    page = research._board_page
    model_bottom = model.mapTo(page, model.rect().bottomLeft()).y()
    status_top = status.mapTo(page, status.rect().topLeft()).y()
    status_bottom = status.mapTo(page, status.rect().bottomLeft()).y()
    scope_top = scope.mapTo(page, scope.rect().topLeft()).y()
    assert model_bottom <= status_top and status_bottom <= scope_top
    # And the scope line is in view without scrolling at the default window size.
    scope_bottom = scope.mapTo(win, scope.rect().bottomLeft()).y()
    view_bottom = viewport.mapTo(win, viewport.rect().bottomLeft()).y()
    assert scope_bottom <= view_bottom
    assert scope_bottom <= win.statusBar().y()


@requires_qt
@pytest.mark.parametrize("available", [True, False])
def test_ollama_state_is_a_word_not_a_color(qapp, monkeypatch, tmp_path, available):
    from types import SimpleNamespace

    from src.desktop.app import MainWindow

    monkeypatch.setattr(MainWindow, "_startup", lambda self: None)
    monkeypatch.setenv("MOVER_PROCESSED_DIR", str(tmp_path))
    win = MainWindow()
    try:
        win._set_ollama_status_ui(SimpleNamespace(available=available, models=["demo"], started_by_app=False))
        text = win.research.model_status.text()
        assert ("running" if available else "stopped") in text
        assert "color" not in text, "one accent, no red or green state colors"
        assert win._scan_cancel_button.styleSheet() == "", "the focus ring comes from the app stylesheet's accent"
    finally:
        win.close()


def test_the_main_window_source_has_no_color_literals_outside_the_tokens():
    import re
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "src" / "desktop" / "app.py").read_text(encoding="utf-8")
    assert re.findall(r"#[0-9a-fA-F]{6}\b", source) == []
