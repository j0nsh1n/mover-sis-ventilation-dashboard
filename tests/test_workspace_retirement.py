"""Legacy outputs remain reachable in the sole research workspace."""

from unittest.mock import Mock

import pandas as pd
import pytest
from PySide6.QtWidgets import QApplication, QCheckBox, QDialog, QMessageBox

from src.config import load_thresholds
from src.desktop.app import MainWindow
from src.desktop.cases_overview import CasesOverview
from src.desktop.research_workspace import ResearchWorkspace
from src.llm.service import OllamaStatus
from src.pipeline.corpus_scan import ScanResult
from src.rules import load_rule_catalog
from src.services.case_fetch import FetchResult
from src.services.retrieval import RetrievalResult, SearchCandidate


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, monkeypatch, tmp_path):
    monkeypatch.setenv("MOVER_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("MOVER_EMR_DIR", str(tmp_path / "EMR"))
    monkeypatch.setenv("MOVER_PROCESSED_DIR", str(tmp_path / "processed"))
    monkeypatch.setattr(MainWindow, "_startup", lambda self: None)
    win = MainWindow()
    win.show()
    qapp.processEvents()
    yield win
    win.close()
    win.deleteLater()
    qapp.processEvents()


@pytest.fixture
def detail(qapp):
    view = ResearchWorkspace()
    candidate = SearchCandidate("SYN0000000001", "analyzed case", True, "Synthetic procedure", 55,
                                "F", "Synthetic source", "keyword", 1.0)
    view.set_results(RetrievalResult(candidates=(candidate,)))
    ts = pd.DataFrame({"t_min": [0., 1., 2.], "PIP": [20., 22., 21.],
                       "ETCO2": [35., 36., 37.], "HR": [70., 71., 72.],
                       "TV": [400., 410., 420.], "PEEP": [5., 6., 5.],
                       "Agent_Et": [1., 1.2, 1.1], "Agent_Fi": [1.4, 1.5, 1.4],
                       "MAC_Et": [.7, .8, .9], "SPO2": [98., None, 99.], "nMAP": [65., 66., 67.]})
    fetched = FetchResult(pd.DataFrame({"PID": [candidate.pid], "Age": [55]}), ts,
                          pd.DataFrame(), pd.DataFrame(), pd.DataFrame())
    view.set_case_data(candidate.pid, ts, pd.DataFrame(), fetched=fetched, open_detail=True)
    view.show()
    qapp.processEvents()
    yield view
    view.close()
    view.deleteLater()
    qapp.processEvents()


def test_research_is_central_and_lifecycle_uses_its_controls(window, monkeypatch):
    assert window.centralWidget().isAncestorOf(window.module_stack)
    assert window.module_stack.currentWidget() is window.research
    assert not hasattr(window, "tabs") and not hasattr(window, "chat_history")
    window._apply_ollama_status(OllamaStatus(True, "ready", ["synthetic:a", "synthetic:b"], "http://127.0.0.1:11434"))
    research = window.research
    research.model_menu.choose("synthetic:b")
    assert research.selected_model() == "synthetic:b"
    research.begin_question("Keep this completed question", request_id=1)
    research.set_answer("Recorded answer", 1)
    research.set_searching(False)
    calls = []
    monkeypatch.setattr(window, "_start_ollama_worker", calls.append)
    research.stop_action.trigger()
    assert calls == ["stop"]
    assert research.question_edit.text() == "Keep this completed question"
    assert research._board_request == 1
    unloaded = []
    monkeypatch.setattr("src.llm.service.unload_ollama_model", lambda model: (unloaded.append(model) or True, "unloaded"))
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    research.unload_action.trigger()
    assert unloaded == ["synthetic:b"]


def test_charts_are_shown_at_once_follow_the_scope_and_the_summary_keeps_its_metrics(qapp, monkeypatch):
    import src.desktop.cases_overview as module

    scores = Mock(wraps=module.score_spec)
    rules = Mock(wraps=module.rules_spec)
    durations = Mock(wraps=module.duration_spec)
    monkeypatch.setattr(module, "score_spec", scores)
    monkeypatch.setattr(module, "rules_spec", rules)
    monkeypatch.setattr(module, "duration_spec", durations)
    cases = pd.DataFrame({"PID": ["SYN1", "SYN2"], "anomaly_score": [9, 42], "n_warn": [0, 2],
                          "n_critical": [0, 1], "primary_agent_name": ["sevoflurane", "isoflurane"],
                          "median_TV": [400, 500], "median_PIP": [20, 25], "median_ETCO2": [35, 40],
                          "case_duration_min": [60., 90.]})
    flags = pd.DataFrame({"PID": ["SYN2"], "rule_id": ["pip_high"], "severity": ["warn"]})
    scan_cases = cases.assign(anomaly_score=[80, 90])
    episodes = flags.assign(duration_min=4)
    scan = ScanResult(scan_cases, episodes, {"preset": "default"})
    view = CasesOverview()
    for spy in (scores, rules, durations):
        spy.reset_mock()
    view.set_data(cases, scan, load_rule_catalog(), flags)
    view.show()
    qapp.processEvents()
    # There is no Charts toggle: the three charts are on the page as soon as it has cases
    assert not hasattr(view, "charts_button")
    assert view.charts_panel.isVisibleTo(view)
    assert scores.call_count == rules.call_count == durations.call_count == 1
    pd.testing.assert_frame_equal(rules.call_args.args[0], flags)
    assert rules.call_args.kwargs["minutes_column"] is None
    assert view.summary_label.text().startswith("1 of 2 analyzed cases has rule episodes")
    assert view.row_for("SYN1")["median_TV"] == 400
    view.set_scope("scan")
    assert scores.call_count == rules.call_count == 2
    assert rules.call_args.kwargs["minutes_column"] == "duration_min"
    pd.testing.assert_frame_equal(rules.call_args.args[0], episodes)
    pd.testing.assert_frame_equal(scores.call_args.args[0], scan_cases)
    assert view.summary_label.text().startswith("1 of 2 scored cases has rule episodes")
    # the scan's episode minutes are summed: one 4-minute episode
    assert view.rules_block.chart.accessibleDescription().endswith(", 4.")
    view.set_data(None, None, None)
    assert not view.charts_panel.isVisibleTo(view)
    assert view.summary_label.text() == "No analyzed cases yet."
    view.close()


@pytest.mark.parametrize("signal", ["TV", "PEEP", "Agent_Et", "Agent_Fi", "MAC_Et", "SPO2", "nMAP"])
def test_more_signals_reaches_every_legacy_track_and_source_rows(detail, monkeypatch, signal):
    def choose(dialog):
        boxes = {box.text(): box for box in dialog.findChildren(QCheckBox)}
        boxes[signal].setChecked(True)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", choose)
    detail.more_signals_button.click()
    assert detail.chart_track_names == ["PIP", "ETCO2", "HR", signal]
    detail.minute_slider.setValue(0)
    assert signal in {name for name, _, _ in detail.detail_chart.cursor_points()}
    if signal == "SPO2":
        detail.minute_slider.setValue(1)
        assert signal not in {name for name, _, _ in detail.detail_chart.cursor_points()}

    def inspect(dialog):
        from PySide6.QtWidgets import QTableWidget

        table = dialog.findChild(QTableWidget)
        assert table.horizontalHeaderItem(4).text() == signal
        assert table.item(0, 4).text() != "—"
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(QDialog, "exec", inspect)
    detail._show_current_source()


def test_more_signals_limits_to_three_and_cancel_keeps_tracks(detail, monkeypatch):
    def choose(dialog):
        boxes = {box.text(): box for box in dialog.findChildren(QCheckBox)}
        for signal in ["TV", "PEEP", "SPO2"]:
            boxes[signal].setChecked(True)
        assert not boxes["nMAP"].isEnabled()
        boxes["PEEP"].setChecked(False)
        assert boxes["nMAP"].isEnabled()
        boxes["PEEP"].setChecked(True)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", choose)
    detail.more_signals_button.click()
    assert detail.chart_track_names[3:] == ["TV", "PEEP", "SPO2"]
    monkeypatch.setattr(QDialog, "exec", lambda dialog: QDialog.DialogCode.Rejected)
    detail.more_signals_button.click()
    assert detail.chart_track_names[3:] == ["TV", "PEEP", "SPO2"]
    detail.clear_episode_selection()
    assert detail.chart_track_names[3:] == ["TV", "PEEP", "SPO2"]


def test_grounded_context_uses_the_open_case(detail, monkeypatch):
    calls = []
    monkeypatch.setattr("src.llm.case_context.build_case_context", lambda pid, **kw: calls.append((pid, kw)) or "Synthetic briefing")

    def inspect(box):
        assert box.detailedText() == "Synthetic briefing"
        return 0

    monkeypatch.setattr(QMessageBox, "exec", inspect)
    detail.context_button.click()
    pid, data = calls[0]
    assert pid == "SYN0000000001" and data["include_emr_extras"]
    assert data["cases"].iloc[0]["Age"] == 55
    assert data["timeseries"]["TV"].tolist() == [400., 410., 420.]


def test_drawer_explanations_follow_loaded_preset_below_rules(window):
    from src.desktop.app import PipelineWorker

    window._worker = PipelineWorker(1, "strict", True, parent=window)
    window._on_pipeline_ok(tuple(pd.DataFrame() for _ in range(5)))
    window.research.open_rules()
    drawer = window.research.rules_drawer
    cfg = load_thresholds("strict")
    assert drawer.scoring_text.isHidden() and drawer.mac_text.isHidden()
    drawer.scoring_button.click()
    drawer.mac_button.click()
    assert drawer.scoring_text.isVisible() and drawer.mac_text.isVisible()
    for key, value in cfg["scoring"].items():
        assert f"{key}: {value}" in drawer.scoring_text.text()
    for key, value in cfg["mac_age40"].items():
        assert f"{key}: {value}" in drawer.mac_text.text()
    layout = drawer._list_layout
    assert all(layout.indexOf(row) < layout.indexOf(drawer.scoring_button) for row in drawer._rows.values())
    assert layout.indexOf(drawer.scoring_text) < layout.indexOf(drawer.mac_button)


def _sample_with_flags() -> tuple[pd.DataFrame, pd.DataFrame]:
    cases = pd.DataFrame({"PID": ["SYN1", "SYN2"], "anomaly_score": [9, 42], "n_warn": [0, 2], "n_critical": [0, 1]})
    flags = pd.DataFrame({"PID": ["SYN2", "SYN2"], "t_min": [1., 2.], "rule_id": ["pip_high", "pip_high"],
                          "severity": ["warn", "critical"]})
    return cases, flags


def test_the_loaded_sample_flags_reach_the_cases_rule_chart(window):
    window.cases, window.flags = _sample_with_flags()
    window._push_cases_overview()
    window.research.show_cases()
    overview = window.research._cases_overview
    pd.testing.assert_frame_equal(overview._flags, window.flags)
    chart = overview.rules_block.chart
    assert chart.has_chart()
    # two flagged minutes of one rule, named by the preset's label for it
    label = load_rule_catalog().get("pip_high").label
    assert chart.accessibleDescription() == f"Most frequent rules by flagged minutes: {label}, 2."


def test_the_loaded_samples_minutes_and_episodes_reach_the_cases_preview(window):
    window.cases, window.flags = _sample_with_flags()
    window.ts = pd.DataFrame({"PID": ["SYN2"] * 3, "t_min": [0, 1, 2], "PIP": [20., 21., 22.],
                              "ETCO2": [35.] * 3, "HR": [70.] * 3})
    window.episodes = pd.DataFrame({"PID": ["SYN2", "SYN2"], "rule_id": ["pip_high", "pip_high"],
                                    "severity": ["warn", "critical"], "duration_min": [1., 2.]})
    window._push_cases_overview()
    window.research.show_cases()
    overview = window.research._cases_overview
    # sorted by anomaly score, the case with the 42 comes first
    assert overview.preview.kicker.text() == "Preview · SYN2"
    assert overview.preview.traces["PIP"].state()[1] == (20., 21., 22.)
    label = load_rule_catalog().get("pip_high").label
    rows = overview.preview.episode_list
    assert [rows.item(i).text() for i in range(rows.count())] == [
        f"◆ {label} · critical · 2 min", f"△ {label} · warn · 1 min"]
    # the other case has no minute data in memory
    overview.table.selectRow(1)
    assert overview.preview.kicker.text() == "Preview · SYN1"
    assert overview.preview.trace_note.isVisibleTo(overview)


def test_choosing_a_model_is_remembered_and_restored(window):
    from src.user_settings import load_settings

    url = "http://127.0.0.1:11434"
    window._apply_ollama_status(OllamaStatus(True, "ready", ["synthetic:a", "synthetic:b"], url))
    assert window.research.selected_model() == "synthetic:a"
    window.research.model_menu.choose("synthetic:b")
    assert load_settings()["ollama_model"] == "synthetic:b"
    window._apply_ollama_status(OllamaStatus(False, "stopped", [], url))
    assert window.research.selected_model() is None
    assert load_settings()["ollama_model"] == "synthetic:b"
    window._apply_ollama_status(OllamaStatus(True, "ready", ["synthetic:a", "synthetic:b"], url))
    assert window.research.selected_model() == "synthetic:b"
    for placeholder in ("(Ollama stopped — choose Start Ollama)", "  ", ""):
        window._on_ollama_model_changed(placeholder)
    assert load_settings()["ollama_model"] == "synthetic:b"


@pytest.mark.parametrize("route", ["quick", "settings"])
def test_changing_the_theme_redraws_the_open_cases_charts(window, qapp, route):
    from src.desktop.theme import apply_theme, tokens

    def corner(chart):
        # the figure's own ground, taken from what the widget paints
        return chart.grab().toImage().pixelColor(0, 0).name().upper()

    try:
        # An earlier test in the same run may have left the app dark; this one starts from light.
        apply_theme(qapp, "light")
        window.cases, window.flags = _sample_with_flags()
        window._push_cases_overview()
        window.research.show_cases()
        qapp.processEvents()
        charts = [block.chart for block in (window.research._cases_overview.score_block,
                                            window.research._cases_overview.rules_block,
                                            window.research._cases_overview.duration_block)]
        light = tokens("light")["paper"].upper()
        dark = tokens("dark")["paper"].upper()
        assert [corner(chart) for chart in charts[:2]] == [light, light]
        if route == "quick":
            window._set_theme_quick("dark")
        else:
            window._on_settings_applied({"theme": "dark", "preset": "default"})
        qapp.processEvents()
        assert [corner(chart) for chart in charts[:2]] == [dark, dark]
    finally:
        apply_theme(qapp, "light")


def test_source_samples_hatch_missing_cells_and_say_missing(detail, monkeypatch):
    from PySide6.QtWidgets import QTableWidget

    from src.desktop.marks import MISSING_ROLE, MissingCellDelegate

    def choose(dialog):
        {box.text(): box for box in dialog.findChildren(QCheckBox)}["SPO2"].setChecked(True)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", choose)
    detail.more_signals_button.click()
    seen = {}

    def inspect(dialog):
        table = dialog.findChild(QTableWidget)
        assert isinstance(table.itemDelegate(), MissingCellDelegate)
        seen.update({
            (row, column): table.item(row, column).text()
            for row in range(table.rowCount()) for column in range(table.columnCount())
            if table.item(row, column).data(MISSING_ROLE)
        })
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(QDialog, "exec", inspect)
    detail._show_current_source()
    # SPO2 is the fifth column and has no sample at minute 1 (row 1); no other cell is missing
    assert seen == {(1, 4): "missing"}
