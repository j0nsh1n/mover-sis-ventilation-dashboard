"""User data-directory selection and settings persistence."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.runtime_paths import (
    clear_session_path_overrides,
    configure_from_user_directory,
    emr_dir,
    processed_dir,
    resolve_user_data_choice,
    set_session_paths,
)
from src.user_settings import load_settings, save_settings, settings_path, update_settings


@pytest.fixture(autouse=True)
def _clean_session_paths():
    clear_session_path_overrides()
    yield
    clear_session_path_overrides()


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("MOVER_CONFIG_DIR", str(tmp_path / "cfg"))
    return tmp_path / "cfg"


def test_settings_roundtrip(config_home):
    data = update_settings(emr_dir="/tmp/emr", n_cases=25)
    assert settings_path().is_file()
    assert data["emr_dir"] == "/tmp/emr"
    assert data["n_cases"] == 25
    assert load_settings()["emr_dir"] == "/tmp/emr"


def test_resolve_emr_folder_directly(tmp_path):
    emr = tmp_path / "EMR"
    emr.mkdir()
    (emr / "patient_information.csv").write_text("PID\nx\n")
    (emr / "patient_ventilator.csv").write_text("PID\n")
    (emr / "patient_vitals.csv").write_text("PID\n")
    resolved = resolve_user_data_choice(emr)
    assert resolved["emr_dir"] == emr.resolve()
    assert resolved["processed_dir"].name == "processed"


def test_resolve_data_root_with_raw_emr(tmp_path):
    emr = tmp_path / "data" / "raw" / "EMR"
    emr.mkdir(parents=True)
    (emr / "patient_information.csv").write_text("PID\na\n")
    proc = tmp_path / "data" / "processed"
    proc.mkdir()
    (proc / "cases.parquet").write_bytes(b"PAR1")  # not real parquet; only path check
    resolved = resolve_user_data_choice(tmp_path / "data")
    assert resolved["emr_dir"] == emr.resolve()
    # prefers existing processed with cases.parquet
    assert resolved["processed_dir"] == proc.resolve()


def test_resolve_rejects_empty_folder(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError, match="No SIS EMR"):
        resolve_user_data_choice(empty)


def test_configure_persists(config_home, tmp_path):
    emr = tmp_path / "raw" / "EMR"
    emr.mkdir(parents=True)
    (emr / "patient_information.csv").write_text("PID\n1\n")
    resolved = configure_from_user_directory(tmp_path / "raw", persist=True)
    assert emr_dir() == resolved["emr_dir"]
    assert processed_dir() == resolved["processed_dir"]
    cfg = load_settings()
    assert Path(cfg["emr_dir"]) == resolved["emr_dir"]


def test_session_override_beats_default(tmp_path):
    custom = tmp_path / "custom_emr"
    custom.mkdir()
    set_session_paths(emr_dir=custom, processed_dir=tmp_path / "p")
    assert emr_dir() == custom.resolve()
    assert processed_dir() == (tmp_path / "p").resolve()


def test_env_beats_session(tmp_path, monkeypatch):
    set_session_paths(emr_dir=tmp_path / "session_emr")
    env_emr = tmp_path / "env_emr"
    env_emr.mkdir()
    monkeypatch.setenv("MOVER_EMR_DIR", str(env_emr))
    assert emr_dir() == env_emr.resolve()
