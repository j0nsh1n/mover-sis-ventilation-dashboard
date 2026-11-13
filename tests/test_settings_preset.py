"""Saved rule choices apply to new computations without changing loaded rules."""

from types import SimpleNamespace

import pandas as pd
import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from src.desktop.app import MainWindow, PipelineWorker, CorpusScanWorker
from src.desktop.settings_page import SettingsPage
from src.runtime_paths import clear_session_path_overrides, set_session_paths
from src.user_settings import load_settings, update_settings


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def settings(monkeypatch, tmp_path, synthetic_emr):
    monkeypatch.setenv("MOVER_CONFIG_DIR", str(tmp_path / "config"))
    models = tmp_path / "models"
    models.mkdir()
    monkeypatch.setenv("OLLAMA_MODELS", str(models))
    monkeypatch.setattr("src.llm.service.ensure_ollama", lambda **kw: SimpleNamespace(models=[]))
    clear_session_path_overrides()
    set_session_paths(emr_dir=synthetic_emr, processed_dir=tmp_path / "processed")
    update_settings(ollama_models_dir=str(models))
    yield
    clear_session_path_overrides()


@pytest.mark.parametrize("loaded", [False, True])
def test_saving_strict_sets_next_runs_and_keeps_the_loaded_datas_preset(qapp, monkeypatch, loaded):
    monkeypatch.setattr(MainWindow, "_startup", lambda self: None)
    win = MainWindow()
    win._set_loaded_preset("lenient", recorded=True)
    if loaded:
        win.cases = pd.DataFrame()
    applied = []
    page = SettingsPage()
    page.settings_applied.connect(lambda vals, _changed: applied.append(vals))
    page.settings_applied.connect(lambda vals, _changed: win._on_settings_applied(vals))
    page.combo_preset.setCurrentIndex(page.combo_preset.findData("strict"))
    assert page.save() is True
    assert applied[0]["preset"] == load_settings()["preset"] == "strict"
    assert win._next_run_preset() == "strict"
    # Loaded data keeps its recorded preset; with no data, research fetches follow the saved one.
    active = "lenient" if loaded else "strict"
    assert win._active_preset == active
    assert win._make_session_snapshot().preset == active
    assert f"({active}, " in win.research.rules_drawer.header_label.text()

    monkeypatch.setattr(PipelineWorker, "start", lambda self: None)
    win._run_pipeline_menu()
    assert isinstance(win._worker, PipelineWorker)
    assert win._worker.preset == "strict"
    assert win._active_preset == active

    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(CorpusScanWorker, "start", lambda self: None)
    win._run_corpus_scan()
    assert isinstance(win._worker, CorpusScanWorker)
    assert win._worker.preset == "strict"
    assert win._active_preset == active
    win.close()
    qapp.processEvents()
