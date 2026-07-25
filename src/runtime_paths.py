"""Resolve repo / bundle / user-data paths for dev and frozen executables."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Session overrides (set by desktop UI or tests). Env vars still take precedence
# when explicit; see resolution order in each function.
_session_data_dir: Path | None = None
_session_emr_dir: Path | None = None
_session_processed_dir: Path | None = None


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
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def clear_session_path_overrides() -> None:
    global _session_data_dir, _session_emr_dir, _session_processed_dir
    _session_data_dir = None
    _session_emr_dir = None
    _session_processed_dir = None


def set_session_paths(
    *,
    data_dir: Path | str | None = None,
    emr_dir: Path | str | None = None,
    processed_dir: Path | str | None = None,
) -> None:
    """Set in-process path overrides (used by the desktop UI)."""
    global _session_data_dir, _session_emr_dir, _session_processed_dir
    if data_dir is not None:
        _session_data_dir = Path(data_dir).expanduser().resolve()
    if emr_dir is not None:
        _session_emr_dir = Path(emr_dir).expanduser().resolve()
    if processed_dir is not None:
        _session_processed_dir = Path(processed_dir).expanduser().resolve()


def apply_persisted_settings() -> None:
    """Load last chosen directories from user settings into the session."""
    try:
        from src.user_settings import load_settings
    except Exception:
        return
    cfg = load_settings()
    data = cfg.get("data_dir")
    emr = cfg.get("emr_dir")
    proc = cfg.get("processed_dir")
    kwargs = {}
    if data:
        kwargs["data_dir"] = data
    if emr:
        kwargs["emr_dir"] = emr
    if proc:
        kwargs["processed_dir"] = proc
    if kwargs:
        set_session_paths(**kwargs)


def data_dir() -> Path:
    """Root for raw + processed datasets (writable)."""
    override = os.environ.get("MOVER_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if _session_data_dir is not None:
        return _session_data_dir
    return app_dir() / "data"


def emr_dir() -> Path:
    override = os.environ.get("MOVER_EMR_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if _session_emr_dir is not None:
        return _session_emr_dir
    return data_dir() / "raw" / "EMR"


def processed_dir() -> Path:
    override = os.environ.get("MOVER_PROCESSED_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if _session_processed_dir is not None:
        return _session_processed_dir
    return data_dir() / "processed"


def thresholds_path() -> Path:
    """Bundled thresholds.yaml (read-only in frozen builds)."""
    candidates = [
        bundle_dir() / "src" / "config" / "thresholds.yaml",
        bundle_dir() / "config" / "thresholds.yaml",
        Path(__file__).resolve().parent / "config" / "thresholds.yaml",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return candidates[0]


def looks_like_emr_dir(path: Path) -> bool:
    """True if path itself contains patient_information.csv."""
    return (path / "patient_information.csv").is_file()


def resolve_user_data_choice(path: Path | str) -> dict[str, Path]:
    """
    Interpret a user-selected folder as EMR + processed locations.

    Accepts:
      - EMR folder (has patient_information.csv)
      - parent of EMR/ or raw/EMR/
      - a data root with raw/EMR (+ optional processed/)
    """
    p = Path(path).expanduser().resolve()
    if not p.is_dir():
        raise FileNotFoundError(f"Not a directory: {p}")

    emr: Path | None = None
    for cand in (p, p / "EMR", p / "raw" / "EMR", p / "data" / "raw" / "EMR"):
        if looks_like_emr_dir(cand):
            emr = cand
            break
    if emr is None:
        raise FileNotFoundError(
            f"No SIS EMR tables found under {p}. "
            "Select the folder that contains patient_information.csv "
            "(or a parent that contains EMR/ or raw/EMR/)."
        )

    # Processed: prefer existing caches near the chosen tree
    processed_candidates = [
        p / "processed",
        p / "data" / "processed",
        emr.parent.parent / "processed" if emr.name == "EMR" else emr.parent / "processed",
        emr.parent / "processed",
    ]
    # Prefer existing processed with cases.parquet; else default next to data layout
    processed: Path | None = None
    for cand in processed_candidates:
        try:
            c = cand.resolve()
        except OSError:
            continue
        if (c / "cases.parquet").is_file():
            processed = c
            break
    if processed is None:
        # Standard layout: .../data/raw/EMR → .../data/processed
        if emr.name == "EMR" and emr.parent.name == "raw":
            processed = emr.parent.parent / "processed"
        elif emr.name == "EMR":
            processed = emr.parent / "processed"
        else:
            processed = p / "processed"

    # Data root for display
    if emr.name == "EMR" and emr.parent.name == "raw":
        data_root = emr.parent.parent
    elif looks_like_emr_dir(p):
        data_root = p.parent if p.name == "EMR" else p
    else:
        data_root = p

    return {
        "data_dir": data_root.resolve(),
        "emr_dir": emr.resolve(),
        "processed_dir": processed.resolve(),
    }


def configure_from_user_directory(
    path: Path | str,
    *,
    persist: bool = True,
) -> dict[str, Path]:
    """Resolve a user folder, apply session overrides, optionally persist."""
    resolved = resolve_user_data_choice(path)
    set_session_paths(
        data_dir=resolved["data_dir"],
        emr_dir=resolved["emr_dir"],
        processed_dir=resolved["processed_dir"],
    )
    if persist:
        from src.user_settings import update_settings

        update_settings(
            data_dir=str(resolved["data_dir"]),
            emr_dir=str(resolved["emr_dir"]),
            processed_dir=str(resolved["processed_dir"]),
        )
    return resolved
