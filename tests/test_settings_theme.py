"""User settings (LLM path, theme) and theme resolution."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.user_settings import (
    THEME_DARK,
    THEME_LIGHT,
    THEME_SYSTEM,
    apply_ollama_env_from_settings,
    detect_default_ollama_models_dir,
    get_theme,
    load_settings,
    mark_setup_complete,
    needs_first_run_setup,
    update_settings,
)


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("MOVER_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.delenv("OLLAMA_MODELS", raising=False)
    return tmp_path / "cfg"


def test_theme_roundtrip(config_home):
    update_settings(theme=THEME_DARK)
    assert get_theme() == THEME_DARK
    update_settings(theme="nope")
    assert get_theme() == THEME_SYSTEM


def test_needs_setup_when_empty(config_home):
    assert needs_first_run_setup() is True
    mark_setup_complete()
    assert needs_first_run_setup() is False


def test_needs_setup_false_when_emr_saved(config_home):
    update_settings(emr_dir="/tmp/some_emr")
    assert needs_first_run_setup() is False
    assert load_settings().get("setup_complete") is True


def test_apply_ollama_models_env(config_home, tmp_path, monkeypatch):
    models = tmp_path / "LLM_Models"
    models.mkdir()
    update_settings(ollama_models_dir=str(models))
    applied = apply_ollama_env_from_settings()
    assert applied == str(models.resolve())
    assert os.environ["OLLAMA_MODELS"] == str(models.resolve())


def test_detect_default_from_env(config_home, tmp_path, monkeypatch):
    models = tmp_path / "models"
    models.mkdir()
    monkeypatch.setenv("OLLAMA_MODELS", str(models))
    assert Path(detect_default_ollama_models_dir()) == models.resolve()


def test_ollama_serve_env_includes_models(config_home, tmp_path, monkeypatch):
    from unittest.mock import MagicMock, patch

    from src.llm.ollama_client import OllamaError
    from src.llm.service import ensure_ollama

    models = tmp_path / "LLM_Models"
    models.mkdir()
    update_settings(ollama_models_dir=str(models))

    with patch("src.llm.service.OllamaClient") as Cls, patch(
        "src.llm.service.find_ollama_binary", return_value="/usr/bin/ollama"
    ), patch("src.llm.service.subprocess.Popen") as Popen, patch(
        "src.llm.service.time.sleep"
    ):
        call_count = {"n": 0}

        def client_factory(*a, **k):
            m = MagicMock()

            def list_models():
                call_count["n"] += 1
                if Popen.call_count == 0:
                    raise OllamaError("down")
                return ["gemma4:latest"]

            m.list_models.side_effect = list_models
            return m

        Cls.side_effect = client_factory
        proc = MagicMock()
        proc.poll.return_value = None
        Popen.return_value = proc

        st = ensure_ollama(start_if_needed=True, wait_s=2.0)
        assert st.available
        assert Popen.called
        env = Popen.call_args.kwargs.get("env") or Popen.call_args[1].get("env")
        assert env is not None
        assert env.get("OLLAMA_MODELS") == str(models.resolve())


@pytest.mark.skipif(
    os.environ.get("QT_QPA_PLATFORM") == "skip",
    reason="placeholder",
)
def test_theme_apply_light_dark(config_home, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        pytest.skip("PySide6 unavailable")

    from src.desktop.theme import apply_theme, resolve_theme_mode

    app = QApplication.instance()
    if app is None:
        app = QApplication([])

    assert resolve_theme_mode(THEME_LIGHT) == THEME_LIGHT
    assert resolve_theme_mode(THEME_DARK) == THEME_DARK
    concrete = apply_theme(app, THEME_DARK)
    assert concrete == THEME_DARK
    sheet = app.styleSheet()
    assert sheet and ("0b1220" in sheet or "0f172a" in sheet or "151e2e" in sheet)
    concrete = apply_theme(app, THEME_LIGHT)
    assert concrete == THEME_LIGHT
    assert "f1f5f9" in app.styleSheet() or "ffffff" in app.styleSheet()


def test_settings_dialog_constructs(config_home, monkeypatch, tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        pytest.skip("PySide6 unavailable")

    from src.desktop.settings_ui import PathsForm, SettingsDialog, SetupWizard
    from src.runtime_paths import clear_session_path_overrides, set_session_paths

    app = QApplication.instance()
    if app is None:
        app = QApplication([])

    clear_session_path_overrides()
    empty = tmp_path / "p"
    empty.mkdir()
    set_session_paths(processed_dir=empty)

    form = PathsForm()
    assert form.combo_theme.count() == 3
    form.edit_ollama_models.setText(str(tmp_path / "models"))
    (tmp_path / "models").mkdir()
    form.edit_emr.setText("")  # no require

    dlg = SettingsDialog()
    assert dlg.windowTitle() == "Settings"
    dlg.close()

    wiz = SetupWizard()
    assert len(wiz.pageIds()) == 3
    wiz.close()
    clear_session_path_overrides()
