"""Resolve repo / bundle / user-data paths for dev and frozen executables."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"))


def bundle_dir() -> Path:
    """
    Read-only resources shipped with the app.

    - Dev: repository root
    - Frozen (PyInstaller): extraction dir (sys._MEIPASS)
    """
    if is_frozen():
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[1]


def app_dir() -> Path:
    """
    Directory that "owns" the running app for writable data.

    - Dev: repository root
    - Frozen: directory containing the executable
      (so users can place ``data/raw/EMR`` next to the binary)
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def data_dir() -> Path:
    """Root for raw + processed datasets (writable)."""
    override = os.environ.get("MOVER_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return app_dir() / "data"


def emr_dir() -> Path:
    override = os.environ.get("MOVER_EMR_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return data_dir() / "raw" / "EMR"


def processed_dir() -> Path:
    override = os.environ.get("MOVER_PROCESSED_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return data_dir() / "processed"


def thresholds_path() -> Path:
    """Bundled thresholds.yaml (read-only in frozen builds)."""
    # Prefer packaged location used by PyInstaller datas
    candidates = [
        bundle_dir() / "src" / "config" / "thresholds.yaml",
        bundle_dir() / "config" / "thresholds.yaml",
        Path(__file__).resolve().parent / "config" / "thresholds.yaml",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return candidates[0]
