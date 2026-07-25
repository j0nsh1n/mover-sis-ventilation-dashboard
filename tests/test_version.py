"""Version stamping for releases."""

from __future__ import annotations

from pathlib import Path

from src.__version__ import __version__, get_version

ROOT = Path(__file__).resolve().parents[1]


def test_version_file_is_0_1_series():
    text = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert text == "0.1.0"
    assert __version__ == "0.1.0"
    assert get_version() == "0.1.0"


def test_get_version_reads_file(tmp_path, monkeypatch):
    monkeypatch.setenv("MOVER_DATA_DIR", str(tmp_path))
    # drop session overrides if any
    from src.runtime_paths import clear_session_path_overrides

    clear_session_path_overrides()
    assert get_version().startswith("0.")
