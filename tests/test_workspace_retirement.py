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
    assert window.centralWidget() is window.research
    assert not hasattr(window, "tabs") and not hasattr(window, "chat_history")
    window._apply_ollama_status(OllamaStatus(True, "ready", ["synthetic:a", "synthetic:b"], "http://127.0.0.1:11434"))
    research = window.research
    research.model_combo.setCurrentText("synthetic:b")
    assert research.selected_model() == "synthetic:b"
    research.begin_question("Keep this completed question", request_id=1)
    research.set_answer("Recorded answer", 1)
    research.set_searching(False)
    calls = []
    monkeypatch.setattr(window, "_start_ollama_worker", calls.append)
    research.stop_button.click()
    assert calls == ["stop"]
    assert research.question_edit.text() == "Keep this completed question"
    assert research._board_request == 1
    unloaded = []
    monkeypatch.setattr("src.llm.service.unload_ollama_model", lambda model: (unloaded.append(model) or True, "unloaded"))
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    research.unload_button.click()
    assert unloaded == ["synthetic:b"]


def test_charts_are_lazy_refresh_with_scope_and_keep_summary_metrics(qapp, monkeypatch):
    import src.desktop.cases_overview as module

    top = Mock(wraps=module.top_cases_figure)
    figures = []
    draw_rules = module.flag_rules_figure

    def record_rules(*args, **kwargs):
        figure = draw_rules(*args, **kwargs)
        figures.append(figure)
        return figure

    rules = Mock(side_effect=record_rules)
    monkeypatch.setattr(module, "top_cases_figure", top)
    monkeypatch.setattr(module, "flag_rules_figure", rules)
    cases = pd.DataFrame({"PID": ["SYN1", "SYN2"], "anomaly_score": [9, 42], "n_warn": [0, 2],
                          "n_critical": [0, 1], "primary_agent_name": ["sevoflurane", "isoflurane"],
                          "median_TV": [400, 500], "median_PIP": [20, 25], "median_ETCO2": [35, 40]})
    flags = pd.DataFrame({"PID": ["SYN2"], "rule_id": ["pip_high"], "severity": ["warn"]})
    scan_cases = cases.assign(anomaly_score=[80, 90])
    episodes = flags.assign(duration_min=4)
    scan = ScanResult(scan_cases, episodes, {"preset": "default"})
    view = CasesOverview()
    view.set_data(cases, scan, load_rule_catalog(), flags)
    view.show()
    qapp.processEvents()
    assert not view.charts_button.isChecked()
    top.assert_not_called()
    rules.assert_not_called()
    assert "with flags: 1 · max score: 42" in view.scope_label.text()
    assert view.row_for("SYN1")["median_TV"] == 400
    view.charts_button.click()
    assert top.call_count == rules.call_count == 1
    pd.testing.assert_frame_equal(rules.call_args.args[0], flags)
    view.charts_button.click()
    view.scope_combo.setCurrentIndex(1)
    assert top.call_count == rules.call_count == 1
    assert "max score: 90" in view.scope_label.text()
    view.charts_button.click()
    assert rules.call_args.kwargs["count_column"] == "duration_min"
    assert sum(patch.get_height() for patch in figures[-1].axes[0].patches) == 4
    pd.testing.assert_frame_equal(rules.call_args.args[0], episodes)
    view.charts_button.click()
    view.set_data(None, None, None)
    assert top.call_count == 2
    view.charts_button.click()
    assert "No scored cases" in view.top_chart._label.text()
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


def test_the_loaded_sample_flags_reach_the_cases_rule_chart(window, monkeypatch):
    import src.desktop.cases_overview as module

    shown = []
    draw = module.flag_rules_figure
    monkeypatch.setattr(module, "flag_rules_figure", lambda *args, **kwargs: shown.append(draw(*args, **kwargs)) or shown[-1])
    window.cases, window.flags = _sample_with_flags()
    window._push_cases_overview()
    window.research.show_cases()
    window.research._cases_overview.charts_button.click()
    pd.testing.assert_frame_equal(window.research._cases_overview._flags, window.flags)
    assert shown[-1].axes[0].patches
    assert not any(text.get_text() == "No flags" for text in shown[-1].axes[0].texts)


def test_choosing_a_model_is_remembered_and_restored(window):
    from src.user_settings import load_settings

    url = "http://127.0.0.1:11434"
    window._apply_ollama_status(OllamaStatus(True, "ready", ["synthetic:a", "synthetic:b"], url))
    assert window.research.selected_model() == "synthetic:a"
    window.research.model_combo.setCurrentText("synthetic:b")
    assert load_settings()["ollama_model"] == "synthetic:b"
    window._apply_ollama_status(OllamaStatus(False, "stopped", [], url))
    assert window.research.selected_model() is None
    assert load_settings()["ollama_model"] == "synthetic:b"
    window._apply_ollama_status(OllamaStatus(True, "ready", ["synthetic:a", "synthetic:b"], url))
    assert window.research.selected_model() == "synthetic:b"
    for placeholder in ("(Ollama stopped — click Start)", "  ", ""):
        window._on_ollama_model_changed(placeholder)
    assert load_settings()["ollama_model"] == "synthetic:b"


@pytest.mark.parametrize("route", ["quick", "settings"])
def test_changing_the_theme_redraws_the_open_cases_charts(window, qapp, monkeypatch, route):
    import src.desktop.cases_overview as module
    from src.desktop.theme import apply_theme

    top = Mock(wraps=module.top_cases_figure)
    monkeypatch.setattr(module, "top_cases_figure", top)
    try:
        window.cases, window.flags = _sample_with_flags()
        window._push_cases_overview()
        window.research.show_cases()
        window.research._cases_overview.charts_button.click()
        assert top.call_count == 1
        if route == "quick":
            window._set_theme_quick("dark")
        else:
            window._on_settings_applied({"theme": "dark", "preset": "default"})
        assert top.call_count == 2
    finally:
        apply_theme(qapp, "light")
