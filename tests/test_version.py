"""Version stamping for releases."""

from __future__ import annotations

from pathlib import Path

from src.__version__ import __version__, get_version

ROOT = Path(__file__).resolve().parents[1]


def test_version_file_semver_xyz():
    text = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    parts = text.split(".")
    assert len(parts) == 3 and all(p.isdigit() for p in parts), f"bad VERSION {text!r}"
    assert __version__ == text
    assert get_version() == text
    # Current feature line (gear-icon settings + on-demand EMR fetch + wave decode)
    assert text == "0.6.0"


def test_get_version_reads_file(tmp_path, monkeypatch):
    monkeypatch.setenv("MOVER_DATA_DIR", str(tmp_path))
    # drop session overrides if any
    from src.runtime_paths import clear_session_path_overrides

    clear_session_path_overrides()
    assert get_version().startswith("0.")
