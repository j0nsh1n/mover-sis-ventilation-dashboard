"""Application version (keep in sync with top-level VERSION file)."""

from __future__ import annotations

from pathlib import Path

__version__ = "0.1.0"


def get_version() -> str:
    """Return version string; prefers repo VERSION file when present."""
    candidates = [
        Path(__file__).resolve().parents[1] / "VERSION",
    ]
    try:
        from src.runtime_paths import app_dir, bundle_dir

        candidates.extend(
            [
                app_dir() / "VERSION",
                bundle_dir() / "VERSION",
            ]
        )
    except Exception:
        pass
    for path in candidates:
        try:
            if path.is_file():
                text = path.read_text(encoding="utf-8").strip()
                if text:
                    return text
        except OSError:
            continue
    return __version__
