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
    assert "MOVER SIS" in win.windowTitle()
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
    assert win.combo_pid.count() > 0
    assert elapsed < 5.0, f"UI data ready took {elapsed:.2f}s (budget 5s)"
    # Charts refresh should not throw
    win._refresh_charts_if_needed()
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
        win.btn_ollama_start.click()
        first_worker = win._ollama_worker
        win._ollama_start()
        assert win._ollama_worker is first_worker
        assert win.btn_ollama_start.text() == "Starting…"
        assert not win.btn_ollama_start.isEnabled()
        while not ticks and time.perf_counter() - started < 0.1:
            qapp.processEvents()
            time.sleep(0.001)
        assert ticks and ticks[0] - started < 0.1
        assert first_worker.wait(3000)
        qapp.processEvents()
        assert len(calls) == 1
        assert win.btn_ollama_start.isEnabled()
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
        assert win.btn_ollama_start.isEnabled()
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
    monkeypatch.setattr(MainWindow, "_refresh_tables_and_selectors", lambda self: None)
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
        assert len(dialogs) == 1 and str(cases) in dialogs[0]
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
        assert str(tmp_path / "missing") in dialogs[0]
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
