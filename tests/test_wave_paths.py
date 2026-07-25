"""Wave directory resolution (separate from EMR)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.runtime_paths import (
    clear_session_path_overrides,
    configure_wave_directory,
    looks_like_wave_dir,
    resolve_wave_directory,
    wave_dir,
    waveform_case_dir,
)


@pytest.fixture(autouse=True)
def _clean():
    clear_session_path_overrides()
    yield
    clear_session_path_overrides()


def test_looks_like_wave_waveforms_dir(tmp_path):
    (tmp_path / "Waveforms" / "03" / "03abc").mkdir(parents=True)
    assert looks_like_wave_dir(tmp_path)


def test_looks_like_wave_archive(tmp_path):
    (tmp_path / "sis_wave_v2.tar.gz").write_bytes(b"x")
    assert looks_like_wave_dir(tmp_path)


def test_resolve_wave_nested(tmp_path):
    root = tmp_path / "sis_wave_v2" / "UCI_part"
    (root / "Waveforms" / "ab" / "abcdef").mkdir(parents=True)
    resolved = resolve_wave_directory(tmp_path / "sis_wave_v2")
    assert resolved == (tmp_path / "sis_wave_v2").resolve()


def test_configure_wave_persists(tmp_path, monkeypatch):
    monkeypatch.setenv("MOVER_CONFIG_DIR", str(tmp_path / "cfg"))
    (tmp_path / "wave" / "Waveforms").mkdir(parents=True)
    w = configure_wave_directory(tmp_path / "wave", persist=True)
    assert wave_dir() == w
    from src.user_settings import load_settings

    assert Path(load_settings()["wave_dir"]) == w


def test_waveform_case_dir(tmp_path):
    pid = "03e4d41adce6e85f"
    case = tmp_path / "Waveforms" / "03" / pid
    case.mkdir(parents=True)
    configure_wave_directory(tmp_path, persist=False)
    assert waveform_case_dir(pid) == case.resolve()
    assert waveform_case_dir("deadbeef") is None
