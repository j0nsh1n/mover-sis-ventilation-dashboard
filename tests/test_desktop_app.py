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
