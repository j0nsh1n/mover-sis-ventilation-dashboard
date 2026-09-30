"""Path helpers for dev vs frozen builds."""

from __future__ import annotations


import pytest

from src.runtime_paths import (
    app_dir,
    bundle_dir,
    data_dir,
    emr_dir,
    is_frozen,
    processed_dir,
    thresholds_path,
)


@pytest.fixture(autouse=True)
def _clear_paths():
    from src.runtime_paths import clear_session_path_overrides

    clear_session_path_overrides()
    yield
    clear_session_path_overrides()


def test_not_frozen_in_pytest():
    assert is_frozen() is False


def test_dev_paths_point_at_repo():
    root = app_dir()
    assert (root / "src").is_dir()
    assert bundle_dir() == root
    assert data_dir() == root / "data"
    assert emr_dir() == root / "data" / "raw" / "EMR"
    assert processed_dir() == root / "data" / "processed"
    assert thresholds_path().is_file()
    assert thresholds_path().name == "thresholds.yaml"


def test_env_overrides(monkeypatch, tmp_path):
    from src.runtime_paths import clear_session_path_overrides

    clear_session_path_overrides()
    monkeypatch.setenv("MOVER_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("MOVER_EMR_DIR", str(tmp_path / "e"))
    monkeypatch.setenv("MOVER_PROCESSED_DIR", str(tmp_path / "p"))
    from src.runtime_paths import data_dir as dd
    from src.runtime_paths import emr_dir as ed
    from src.runtime_paths import processed_dir as pd

    assert dd() == (tmp_path / "d").resolve()
    assert ed() == (tmp_path / "e").resolve()
    assert pd() == (tmp_path / "p").resolve()
