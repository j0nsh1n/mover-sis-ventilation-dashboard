"""Question, case, and source navigation in the research desktop view."""

from __future__ import annotations

import os
import sys
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


def test_reset_during_search_cancels_and_clears_board(qapp, monkeypatch):
    monkeypatch.setattr(ResearchWorkspace, "_confirm_reset", lambda self: True)
    view = ResearchWorkspace()
    cancelled = []
    view.cancel_requested.connect(lambda: cancelled.append(True))
    view.begin_question("A running question")
    view.reset_button.click()
    assert cancelled == [True]
    assert view.question_edit.text() == ""
    assert view.answer_text.toPlainText() == ""
    view.close()


@pytest.mark.skipif(sys.platform == "win32", reason="900 px layout is tuned for Linux font and style metrics; the Windows native style needs about 1,060 px (follow-up)")
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


@pytest.fixture
def episode_view(qapp):
    from src.rules import load_rule_catalog
    from src.services.case_fetch import FetchResult

    view = ResearchWorkspace()
    view.resize(900, 860)
    view.set_rule_catalog(load_rule_catalog("default"))
    view.set_results(RetrievalResult(candidates=(_candidate("caseA"),)))
    ts = pd.DataFrame({"PID": ["caseA"] * 6, "t_min": range(6),
                       "PIP": [20., 21., 22., 23., 24., 25.], "ETCO2": [30., 31., 32., 33., 34., 35.],
                       "HR": [70.] * 6, "SPO2": [98., 91., 89., 90., 92., 98.]})
    flags = pd.DataFrame([
        {"PID": "caseA", "t_min": minute, "rule_id": rule, "severity": severity,
         "Obs_time": pd.Timestamp("2020-01-01") + pd.Timedelta(minutes=minute),
         "value": 90., "message": "Synthetic flag", "phase": "maintenance"}
        for rule, severity, minutes in [("spo2_low", "critical", [1, 2, 3, 4]), ("etco2_high", "warn", [1, 2])]
        for minute in minutes
    ])
    fetched = FetchResult(cases=pd.DataFrame({"PID": ["caseA"], "Age": [55]}), timeseries=ts,
                          flags=flags, events=pd.DataFrame({"PID": ["caseA"], "event": ["synthetic"]}))
    view.set_case_data("caseA", ts, flags, fetched=fetched,
                       thresholds_sha256=load_rule_catalog().thresholds_sha256)
    view.show()
    view.open_detail("caseA")
    qapp.processEvents()
    yield view, fetched
    view.close()


def test_episode_order_lane_and_keyboard(episode_view, qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    view, fetched = episode_view
    assert list(view._episodes.rule_id) == ["etco2_high", "spo2_low"]
    assert view.episode_list.count() == 2
    assert view.detail_chart.lane_marks == {1.: "critical", 2.: "critical", 3.: "critical", 4.: "critical"}
    view.episode_list.setFocus()
    QTest.keyClick(view.episode_list, Qt.Key.Key_Home)
    assert view.selected_rule_id == "etco2_high"
    assert view.minute_slider.value() == 1
    QTest.keyClick(view.episode_list, Qt.Key.Key_Down)
    assert view.selected_rule_id == "spo2_low"
    assert view.episode_list.hasFocus()
    assert "SPO2" in view.chart_track_names
    view.episode_list.clearSelection()
    assert view.selected_rule_id is None
    assert view.chart_track_names == ["PIP", "ETCO2", "HR"]
    assert not view.rule_link.isEnabled()
    view._select_flag_minute(2.)
    assert view.selected_rule_id == "spo2_low"
    assert view.minute_slider.value() == 2
    view.return_to_canvas()
    assert "SPO2" not in view.chart_track_names
    pd.testing.assert_frame_equal(view._case_views["caseA"].cases, fetched.cases)
    pd.testing.assert_frame_equal(view._case_views["caseA"].events, fetched.events)


@pytest.mark.parametrize("gap", ["none", "nan", "deleted"])
def test_rule_detail_statistics_and_missing_minutes(episode_view, gap):
    view, _ = episode_view
    case = view._case_views["caseA"]
    if gap == "nan":
        case.timeseries.loc[case.timeseries.t_min.eq(2), "SPO2"] = float("nan")
    elif gap == "deleted":
        case.timeseries = case.timeseries[case.timeseries.t_min.ne(2)].reset_index(drop=True)
    view.episode_list.setCurrentRow(1)
    values = case.timeseries.loc[case.timeseries.t_min.between(1, 4), "SPO2"]
    assert view.signal_statistics["SPO2"] == {
        "min": values.min(), "max": values.max(), "mean": values.mean(),
        "missing": 0 if gap == "none" else 1,
    }
    assert f"{0 if gap == 'none' else 1} missing minutes" in view.rule_detail.text()


def test_current_thresholds_change_and_rule_link(episode_view, monkeypatch):
    from src.rules import load_rule_catalog

    view, _ = episode_view
    view.episode_list.setCurrentRow(1)
    default = load_rule_catalog("default")
    assert f"Thresholds when flagged (default, {default.thresholds_sha256[:12]})" in view.rule_detail.text()
    other = load_rule_catalog("strict")
    view.set_rule_catalog(other)
    assert f"Thresholds when flagged (strict, {other.thresholds_sha256[:12]})" in view.rule_detail.text()
    calls = []
    original = view.show_rule

    def show(rule):
        calls.append(rule)
        original(rule)

    monkeypatch.setattr(view, "show_rule", show)
    view.rule_link.click()
    assert calls == ["spo2_low"]
    assert view.rules_drawer.is_open
    assert view.rules_drawer._rows["spo2_low"].property("highlight") is True


@pytest.mark.parametrize("recorded", [True, False])
def test_rule_detail_threshold_provenance_after_file_change(episode_view, monkeypatch, tmp_path, recorded):
    import src.config
    import src.rules
    from src.runtime_paths import thresholds_path

    view, fetched = episode_view
    original = src.rules.load_rule_catalog()
    path = tmp_path / "thresholds.yaml"
    path.write_bytes(thresholds_path().read_bytes() + b"\n# changed for this test\n")
    monkeypatch.setattr(src.config, "_thresholds_file", lambda: path)
    monkeypatch.setattr(src.rules, "thresholds_path", lambda: path)
    current = src.rules.load_rule_catalog()
    stamp = original.thresholds_sha256 if recorded else None
    view.set_rule_catalog(current, thresholds_sha256=stamp)
    view.set_case_data("caseA", fetched.timeseries, fetched.flags, fetched=fetched,
                       thresholds_sha256=stamp, open_detail=True)
    view.episode_list.setCurrentRow(1)
    header = (f"Thresholds when flagged (default, {stamp[:12]})" if stamp else
              "Thresholds when flagged: not recorded with this data")
    for text in (view.rule_detail.text(), view.rules_drawer.header_label.text()):
        assert header in text
        assert f"Current thresholds (default, {current.thresholds_sha256[:12]})" in text
        assert ("The thresholds changed since these flags were computed. "
                "Run the pipeline again to refresh them." in text) is recorded
    lines = view.rule_detail.text().splitlines()
    current_line = lines.index(f"Current thresholds (default, {current.thresholds_sha256[:12]})")
    # The listed values come from the current file, so they must follow the current line directly.
    assert lines[current_line + 1].startswith(("warn", "critical", "info"))


def test_rule_detail_matching_thresholds_omit_change_warning(episode_view):
    from src.rules import load_rule_catalog

    view, _ = episode_view
    view.episode_list.setCurrentRow(1)
    expected = f"Thresholds when flagged (default, {load_rule_catalog().thresholds_sha256[:12]})"
    for text in (view.rule_detail.text(), view.rules_drawer.header_label.text()):
        assert expected in text
        assert "Current thresholds" not in text
        assert "The thresholds changed" not in text


def test_rule_render_reloads_changed_file_values(episode_view, monkeypatch, tmp_path):
    import src.config
    import src.rules

    view, _ = episode_view
    path = tmp_path / "thresholds.yaml"
    content = src.rules.thresholds_path().read_text()
    assert "warn: 92" in content
    path.write_text(content.replace("warn: 92", "warn: 91"))
    monkeypatch.setattr(src.config, "_thresholds_file", lambda: path)
    monkeypatch.setattr(src.rules, "thresholds_path", lambda: path)
    current = src.rules.load_rule_catalog()
    view.episode_list.setCurrentRow(1)
    assert f"Current thresholds (default, {current.thresholds_sha256[:12]})" in view.rule_detail.text()
    assert "warn at most 91 %" in view.rule_detail.text()
    view.open_rules()
    assert f"Current thresholds (default, {current.thresholds_sha256[:12]})" in view.rules_drawer.header_label.text()
    from PySide6.QtWidgets import QLabel
    assert any("91 %" in label.text() for label in view.rules_drawer._rows["spo2_low"].findChildren(QLabel))
    monkeypatch.setattr(src.rules, "load_rule_catalog", lambda *args: pytest.fail("unchanged file reloaded"))
    view._select_episode(1)
    view.open_rules()


def test_show_on_board_restores_case_threshold_hashes(episode_view):
    view, fetched = episode_view
    view.return_to_canvas()
    view.begin_question("First", request_id=10)
    view.set_results(RetrievalResult(candidates=(_candidate("caseA"),)))
    view.set_case_data("caseA", fetched.timeseries, fetched.flags, thresholds_sha256="a" * 64)
    view.set_answer("First answer")
    view.begin_question("Second", request_id=11)
    view.set_results(RetrievalResult(candidates=(_candidate("caseA"),)))
    view.set_case_data("caseA", fetched.timeseries, fetched.flags, thresholds_sha256="b" * 64)
    view.set_answer("Second answer")
    view.show_turn(10)
    view.open_detail("caseA")
    view.episode_list.setCurrentRow(1)
    assert view._case_views["caseA"].thresholds_sha256 == "a" * 64
    for text in (view.rule_detail.text(), view.rules_drawer.header_label.text()):
        assert "Thresholds when flagged (default, aaaaaaaaaaaa)" in text
        assert "The thresholds changed" in text


def test_no_flags_and_new_case_clear_selection(episode_view):
    view, fetched = episode_view
    view.episode_list.setCurrentRow(1)
    view.set_case_data("caseA", fetched.timeseries, pd.DataFrame(), open_detail=True)
    assert view.selected_rule_id is None
    assert view.episode_list.item(0).text() == "No flags in this case"
    assert not view.detail_chart.lane_marks
    assert view.chart_track_names == ["PIP", "ETCO2", "HR"]
    from src.desktop.charts import focused_signals_figure
    import matplotlib.pyplot as plt

    figure = focused_signals_figure(fetched.timeseries, 0)
    assert any(text.get_text() == "No flags in this case" for text in figure.axes[-1].texts)
    plt.close(figure)


def test_chart_preserves_nan_and_deleted_row_gaps(episode_view):
    import matplotlib.pyplot as plt
    import numpy as np
    from src.desktop.charts import focused_signals_figure

    _, fetched = episode_view
    ts = fetched.timeseries.drop(index=2).copy()
    ts.loc[ts.t_min.eq(3), "SPO2"] = float("nan")
    figure = focused_signals_figure(ts, 0, flags=fetched.flags, extra_signals=("SPO2",))
    line = figure.axes[3].lines[0]
    assert list(line.get_xdata()) == [0, 1, 2, 3, 4, 5]
    assert np.isnan(line.get_ydata()[2:4]).all()
    assert len(figure.axes[-1].collections[0].get_offsets()) == 4
    plt.close(figure)


@pytest.mark.parametrize("first", [-1.5, -4 / 3])
def test_fractional_minutes_preserve_traces_slider_and_missing_count(episode_view, first):
    import matplotlib.pyplot as plt
    import numpy as np
    from src.desktop.charts import focused_signals_figure

    view, fetched = episode_view
    ts = fetched.timeseries.drop(index=4).copy()
    ts["t_min"] = [first + minute for minute in (0, 1, 2, 3, 5)]
    fetched.timeseries = ts
    fetched.episodes = pd.DataFrame([{
        "rule_id": "spo2_low", "severity": "critical", "t_start_min": first,
        "t_end_min": first + 5, "duration_min": 6, "phase": "maintenance",
    }])
    view.set_case_data("caseA", ts, fetched.flags, fetched=fetched, open_detail=True)
    assert view.minute_slider.maximum() + 1 == 6
    assert list(view._minute_rows.t_min) == pytest.approx([first + k for k in range(6)])
    view.episode_list.setCurrentRow(0)
    assert view.signal_statistics["SPO2"]["missing"] == 1
    figure = focused_signals_figure(ts, first)
    line = figure.axes[0].lines[0]
    assert list(line.get_xdata()) == pytest.approx([first + k for k in range(6)])
    assert np.isfinite(line.get_ydata()[:4]).all()
    assert np.isnan(line.get_ydata()[4])
    assert np.isfinite(line.get_ydata()[5])
    plt.close(figure)


def test_duplicate_minutes_open_detail_and_keep_last_measurement(episode_view):
    import matplotlib.pyplot as plt
    from src.desktop.charts import focused_signals_figure

    view, fetched = episode_view
    ts = fetched.timeseries.iloc[[0, 0, 2]].copy()
    ts.iloc[1, ts.columns.get_loc("SPO2")] = 80.
    fetched.timeseries = ts
    fetched.episodes = pd.DataFrame([{
        "rule_id": "spo2_low", "severity": "critical", "t_start_min": 0.,
        "t_end_min": 2., "duration_min": 3, "phase": "maintenance",
    }])
    view.set_case_data("caseA", ts, fetched.flags, fetched=fetched, open_detail=True)
    view.episode_list.setCurrentRow(0)
    assert list(view._minute_rows.t_min) == [0, 1, 2]
    assert view.signal_statistics["SPO2"] == {"min": 80., "max": 89., "mean": 84.5, "missing": 1}
    figure = focused_signals_figure(ts, None, extra_signals=("SPO2",))
    assert figure.axes[3].lines[0].get_ydata()[0] == 80.
    plt.close(figure)


def test_lane_empty_space_and_outside_axis_do_not_select(qapp):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from src.desktop.charts import FocusedSignalChart

    chart = FocusedSignalChart()
    chart.resize(900, 320)
    chart.set_case(pd.DataFrame({"t_min": [0, 100], "PIP": [20., 21.]}),
                   pd.DataFrame({"t_min": [0.], "severity": ["warn"]}))
    chart.show()
    QTest.qWait(180)
    clicked = []
    chart.minute_clicked.connect(clicked.append)
    y = int(chart._axes_rects[-1].center().y())
    QTest.mouseClick(chart, Qt.MouseButton.LeftButton, pos=QPoint(chart.width() // 2, y))
    lane = chart._axes_rects[-1]
    QTest.mouseClick(chart, Qt.MouseButton.LeftButton, pos=QPoint(int(lane.left()) - 1, y))
    low, high = chart._xlim
    mark_x = lane.left() + lane.width() * (0 - low) / (high - low)
    QTest.mouseClick(chart, Qt.MouseButton.LeftButton, pos=QPoint(int(mark_x) + 8, y))
    assert clicked == []
    QTest.mouseClick(chart, Qt.MouseButton.LeftButton, pos=QPoint(int(mark_x) + 5, y))
    assert clicked == [0.]
    chart.close()


def test_cursor_marks_each_track_value_and_skips_missing(qapp):
    from PySide6.QtTest import QTest
    from src.desktop.charts import FocusedSignalChart

    chart = FocusedSignalChart()
    chart.resize(900, 320)
    chart.set_case(pd.DataFrame({"t_min": [0., 1., 2.], "PIP": [10., 30., 20.],
                                 "ETCO2": [35., float("nan"), 40.], "HR": [60., 70., 80.]}),
                   pd.DataFrame())
    chart.show()
    QTest.qWait(180)
    chart.set_minute(1.)
    points = {column: point for column, point, _color in chart.cursor_points()}
    assert set(points) == {"PIP", "HR"}
    pip_axis, *_ = chart._axes_rects
    (_column, (y_low, y_high), _color), *_ = chart._tracks
    assert points["PIP"].y() == pytest.approx(pip_axis.bottom() - pip_axis.height() * (30. - y_low) / (y_high - y_low))
    assert points["PIP"].y() < pip_axis.center().y()
    chart.set_minute(1.5)
    assert chart.cursor_points() == []
    chart.close()


@pytest.mark.parametrize("dpr", [1., 2.])
def test_chart_renders_at_widget_size_after_debounced_resize(qapp, monkeypatch, dpr):
    from PySide6.QtTest import QTest
    from src.desktop.charts import FocusedSignalChart

    chart = FocusedSignalChart()
    monkeypatch.setattr(chart, "devicePixelRatioF", lambda: dpr)
    chart.resize(650, 320)
    chart.set_case(pd.DataFrame({"t_min": [0., 1.], "PIP": [20., 21.]}), pd.DataFrame())
    chart.show()
    QTest.qWait(180)
    assert chart._pixmap.width() == int(chart.width() * dpr)
    assert chart._pixmap.height() == int(chart.height() * dpr)
    assert chart._pixmap.devicePixelRatioF() == dpr
    old_image = chart._pixmap.cacheKey()
    chart.set_minute(1.)
    chart.repaint()
    assert chart._pixmap.cacheKey() == old_image
    chart.resize(1000, 380)
    qapp.processEvents()
    assert chart._pixmap.cacheKey() == old_image
    QTest.qWait(180)
    assert chart._pixmap.width() == int(1000 * dpr)
    assert chart._pixmap.height() == int(380 * dpr)
    chart.resize(650, 320)
    QTest.qWait(180)
    assert chart._pixmap.cacheKey() == old_image
    chart.close()


@pytest.mark.parametrize("width,extras", [(650, ()), (650, ("SPO2",)), (1150, ())])
def test_sized_chart_keeps_axis_titles_inside_image(width, extras):
    import matplotlib.pyplot as plt
    from src.desktop.charts import focused_signals_figure

    height = 250 + 65 * len(extras)
    figure = focused_signals_figure(pd.DataFrame({"t_min": [0., 1.], "PIP": [20., 21.]}),
                                    None, extra_signals=extras, figsize=(width / 110, height / 110))
    figure.canvas.draw()
    renderer = figure.canvas.get_renderer()
    for axis in figure.axes:
        for title in (axis.xaxis.label, axis.yaxis.label):
            if title.get_text():
                bounds = title.get_window_extent(renderer)
                assert bounds.x0 >= 0 and bounds.y0 >= 0
                assert bounds.x1 <= width and bounds.y1 <= height
    plt.close(figure)


@pytest.mark.parametrize("route", ["processed", "live"])
def test_mainwindow_retains_full_case_fetch(qapp, episode_view, monkeypatch, route):
    from types import SimpleNamespace
    from src.desktop.app import MainWindow
    from src.services.case_fetch import FetchResult

    view, fetched = episode_view
    fetched.episodes = view._case_views["caseA"].episodes
    fake = SimpleNamespace(research=view, _pending_case=("caseA", False, False, False),
                           _thresholds_sha256="a" * 64,
                           _case_worker=None, cases=fetched.cases, ts=fetched.timeseries,
                           flags=fetched.flags, episodes=fetched.episodes, events=fetched.events)
    fake._on_research_case_data = lambda value, **kwargs: MainWindow._on_research_case_data(fake, value, **kwargs)
    if route == "processed":
        for name in ("cases", "ts", "flags", "episodes", "events"):
            frame = getattr(fake, name)
            setattr(fake, name, pd.concat([frame, frame.assign(PID="caseB")], ignore_index=True))
        MainWindow._load_research_case(fake, "caseA", False, False)
    else:
        MainWindow._on_research_case_loaded(fake, fetched)
    case = view._case_views["caseA"]
    assert isinstance(fetched, FetchResult)
    for name in ("cases", "timeseries", "flags", "episodes", "events"):
        pd.testing.assert_frame_equal(getattr(case, name).reset_index(drop=True), getattr(fetched, name).reset_index(drop=True))


def test_missing_episode_start_has_exact_cursor_and_missing_sample(episode_view):
    view, fetched = episode_view
    fetched.timeseries = fetched.timeseries[fetched.timeseries.t_min.ne(1)]
    view.set_case_data("caseA", fetched.timeseries, fetched.flags, fetched=fetched, open_detail=True)
    view.episode_list.setCurrentRow(1)
    assert view._minute_rows.iloc[view.minute_slider.value()].t_min == 1
    assert "PIP: missing" in view.sample_label.text()
    assert view.signal_statistics["SPO2"]["missing"] == 1


@pytest.mark.parametrize("width", [900, 1400])
def test_lane_mouse_click_selects_worst_episode_and_flagged_minute(episode_view, width):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    view, _ = episode_view
    view.resize(width, 860)
    QTest.qWait(180)
    chart = view.detail_chart
    low, high = chart._xlim
    lane = chart._axes_rects[-1]
    x = int(lane.left() + lane.width() * (2 - low) / (high - low))
    y = int(lane.center().y())
    QTest.mouseClick(chart, Qt.MouseButton.LeftButton, pos=QPoint(x, y))
    assert view.selected_rule_id == "spo2_low"
    assert view._minute_rows.iloc[view.minute_slider.value()].t_min == 2


def test_tab_reaches_episode_list_and_rule_link(episode_view, qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    view, _ = episode_view
    view.minute_slider.setFocus()
    QTest.keyClick(view.minute_slider, Qt.Key.Key_Tab)
    assert view.follow_up_edit.hasFocus()
    QTest.keyClick(view.follow_up_edit, Qt.Key.Key_Tab)
    assert view.follow_up_button.hasFocus()
    QTest.keyClick(view.follow_up_button, Qt.Key.Key_Tab)
    assert view.episode_list.hasFocus()
    QTest.keyClick(view.episode_list, Qt.Key.Key_Home)
    QTest.keyClick(view.episode_list, Qt.Key.Key_Tab)
    assert view.clear_episode_button.hasFocus()
    QTest.keyClick(view.clear_episode_button, Qt.Key.Key_Tab)
    assert view.rule_link.hasFocus()


def test_unknown_rule_phase_and_extra_track_limit(episode_view):
    view, fetched = episode_view
    case = view._case_views["caseA"]
    episodes = case.episodes.copy()
    episodes.loc[0, "rule_id"] = "desat_with_vent_issue"
    episodes.loc[0, "phase_end"] = "emergence"
    episodes.loc[1, "rule_id"] = "unknown_synthetic_rule"
    fetched.episodes = episodes
    view.set_case_data("caseA", fetched.timeseries, fetched.flags, fetched=fetched, open_detail=True)
    assert "maintenance → emergence" in view.episode_list.item(0).text()
    view.episode_list.setCurrentRow(0)
    assert view.chart_track_names == ["PIP", "ETCO2", "HR", "SPO2", "TV", "RR"]
    assert "TV_mlkg: min missing, max missing, mean missing; 2 missing minutes; not charted" in view.rule_detail.text()
    assert "likely_mech_vent" not in view.signal_statistics
    assert "together with" in view.rule_detail.text()
    assert "unknown_synthetic_rule" in view.episode_list.item(1).text()
    view.episode_list.setCurrentRow(1)
    assert "Current thresholds unavailable" in view.rule_detail.text()


def test_escape_on_signal_detail_closes_open_drawer_before_leaving(episode_view, qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    view, _fetched = episode_view
    view.show()
    view.open_detail("caseA")
    view.open_rules()
    view.back_button.setFocus()
    QTest.keyClick(view.back_button, Qt.Key.Key_Escape)
    assert not view.rules_drawer.isVisible()
    assert view.stack.currentIndex() == 1
    view.back_button.setFocus()
    QTest.keyClick(view.back_button, Qt.Key.Key_Escape)
    assert view.stack.currentIndex() == 0
    view.close()


def _choose_extras(view, monkeypatch, *signals, accept=True):
    from PySide6.QtWidgets import QCheckBox

    def choose(dialog):
        boxes = {box.text(): box for box in dialog.findChildren(QCheckBox)}
        for box in boxes.values():
            box.setChecked(False)
        for signal in signals:
            boxes[signal].setChecked(True)
        return QDialog.DialogCode.Accepted if accept else QDialog.DialogCode.Rejected

    monkeypatch.setattr(QDialog, "exec", choose)
    view.more_signals_button.click()


def _with_extra_columns(view, fetched, pid="caseA", **columns):
    ts = fetched.timeseries.copy()
    for name, value in columns.items():
        ts[name] = value
    fetched.timeseries = ts
    view.set_case_data(pid, ts, fetched.flags, fetched=fetched, open_detail=True)


def test_more_signals_choices_persist_across_episodes_clear_and_cases(episode_view, monkeypatch):
    view, fetched = episode_view
    _with_extra_columns(view, fetched, TV=[400.] * 6, PEEP=[5.] * 6)
    _choose_extras(view, monkeypatch, "TV")
    assert view.chart_track_names == ["PIP", "ETCO2", "HR", "TV"]
    view.episode_list.setCurrentRow(1)
    assert view.selected_rule_id == "spo2_low"
    assert view.chart_track_names == ["PIP", "ETCO2", "HR", "TV", "SPO2"]
    assert "not charted" not in view.rule_detail.text()
    view.clear_episode_selection()
    assert view.chart_track_names == ["PIP", "ETCO2", "HR", "TV"]
    view.episode_list.setCurrentRow(0)
    assert view.selected_rule_id == "etco2_high"
    assert view.chart_track_names == ["PIP", "ETCO2", "HR", "TV"]
    view.detail_chart.minute_clicked.emit(3.)
    assert view.chart_track_names == ["PIP", "ETCO2", "HR", "TV", "SPO2"]
    view.set_results(RetrievalResult(candidates=(_candidate("caseA"), _candidate("caseB"))))
    other = replace(fetched, timeseries=fetched.timeseries.assign(PID="caseB"))
    view.set_case_data("caseB", other.timeseries, fetched.flags, fetched=other, open_detail=True)
    assert view._current_pid == "caseB"
    assert view.chart_track_names == ["PIP", "ETCO2", "HR", "TV"]
    assert view.more_signals_button.text() == "More signals (1/3)"


def test_chosen_extras_take_the_slots_before_a_rules_signals(episode_view, monkeypatch):
    view, fetched = episode_view
    _with_extra_columns(view, fetched, TV=[400.] * 6, PEEP=[5.] * 6, nMAP=[65.] * 6)
    _choose_extras(view, monkeypatch, "TV", "PEEP", "nMAP")
    view.episode_list.setCurrentRow(1)
    assert view.chart_track_names == ["PIP", "ETCO2", "HR", "TV", "PEEP", "nMAP"]
    assert next(line for line in view.rule_detail.text().splitlines()
                if line.startswith("SPO2:")).endswith("0 missing minutes; not charted")
    _choose_extras(view, monkeypatch, "TV")
    assert view.chart_track_names == ["PIP", "ETCO2", "HR", "TV", "SPO2"]
    assert "not charted" not in view.rule_detail.text()


def test_more_signals_cancel_keeps_the_choice_and_lists_only_the_users_signals(episode_view, monkeypatch):
    from PySide6.QtWidgets import QCheckBox

    view, fetched = episode_view
    _with_extra_columns(view, fetched, TV=[400.] * 6, PEEP=[5.] * 6)
    _choose_extras(view, monkeypatch, "TV")
    view.episode_list.setCurrentRow(1)
    _choose_extras(view, monkeypatch, "PEEP", accept=False)
    assert view.chart_track_names == ["PIP", "ETCO2", "HR", "TV", "SPO2"]
    assert view.more_signals_button.text() == "More signals (1/3)"
    seen = {}

    def inspect(dialog):
        seen.update({box.text(): box.isChecked() for box in dialog.findChildren(QCheckBox)})
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(QDialog, "exec", inspect)
    view.more_signals_button.click()
    assert seen["TV"] and not seen["SPO2"] and not seen["PEEP"]


def test_a_chosen_signal_without_samples_gets_a_note_not_an_empty_track(episode_view, monkeypatch):
    view, fetched = episode_view
    _choose_extras(view, monkeypatch, "TV")
    assert view.chart_track_names == ["PIP", "ETCO2", "HR"]
    assert view.extras_note.isVisibleTo(view)
    assert view.extras_note.text() == "No TV samples in this case"
    assert view.more_signals_button.text() == "More signals (1/3)"
    _with_extra_columns(view, fetched, TV=[400.] * 6)
    assert view.chart_track_names == ["PIP", "ETCO2", "HR", "TV"]
    assert not view.extras_note.isVisibleTo(view)
    _with_extra_columns(view, fetched, TV=[float("nan")] * 6)
    assert view.chart_track_names == ["PIP", "ETCO2", "HR"]
    assert view.extras_note.text() == "No TV samples in this case"


@pytest.mark.skipif(sys.platform == "win32", reason="900 px layout is tuned for Linux font and style metrics; the Windows native style needs about 1,060 px (follow-up)")
def test_signal_detail_does_not_scroll_sideways_at_900_px_with_the_drawer_open(episode_view, qapp):
    view, _fetched = episode_view
    view.resize(900, 900)
    view.open_rules()
    qapp.processEvents()
    scroll = view.signal_scroll
    assert view.stack.currentIndex() == 1
    assert scroll.horizontalScrollBar().maximum() == 0
    assert not scroll.horizontalScrollBar().isVisible()
    assert view.context_button.isVisible()
    right = view.context_button.mapTo(scroll.viewport(), view.context_button.rect().topRight()).x()
    assert right <= scroll.viewport().width()
