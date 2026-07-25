"""Resolve repo / bundle / user-data paths for dev and frozen executables."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Session overrides (set by desktop UI or tests).
# Resolution order per path: env var → session override → default.
_session_data_dir: Path | None = None
_session_emr_dir: Path | None = None
_session_processed_dir: Path | None = None
_session_wave_dir: Path | None = None


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"))


def bundle_dir() -> Path:
    if is_frozen():
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[1]


def app_dir() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def clear_session_path_overrides() -> None:
    global _session_data_dir, _session_emr_dir, _session_processed_dir, _session_wave_dir
    _session_data_dir = None
    _session_emr_dir = None
    _session_processed_dir = None
    _session_wave_dir = None


def set_session_paths(
    *,
    data_dir: Path | str | None = None,
    emr_dir: Path | str | None = None,
    processed_dir: Path | str | None = None,
    wave_dir: Path | str | None = None,
) -> None:
    """Set in-process path overrides (used by the desktop UI)."""
    global _session_data_dir, _session_emr_dir, _session_processed_dir, _session_wave_dir
    if data_dir is not None:
        _session_data_dir = Path(data_dir).expanduser().resolve()
    if emr_dir is not None:
        _session_emr_dir = Path(emr_dir).expanduser().resolve()
    if processed_dir is not None:
        _session_processed_dir = Path(processed_dir).expanduser().resolve()
    if wave_dir is not None:
        _session_wave_dir = Path(wave_dir).expanduser().resolve()


def apply_persisted_settings() -> None:
    """Load last chosen directories from user settings into the session."""
    try:
        from src.user_settings import load_settings
    except Exception:
        return
    cfg = load_settings()
    kwargs = {}
    for key in ("data_dir", "emr_dir", "processed_dir", "wave_dir"):
        val = cfg.get(key)
        if val:
            kwargs[key] = val
    if kwargs:
        set_session_paths(**kwargs)


def data_dir() -> Path:
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


def wave_dir() -> Path | None:
    """
    Optional waveform root (SIS wave extract or folder containing archives).

    Returns None if not configured (wave is optional for tabular dashboard).
    """
    override = os.environ.get("MOVER_WAVE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if _session_wave_dir is not None:
        return _session_wave_dir
    # Soft default: project data/wave if present
    default = data_dir() / "wave"
    if default.is_dir():
        return default.resolve()
    return None


def thresholds_path() -> Path:
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
    return (path / "patient_information.csv").is_file()


def looks_like_wave_dir(path: Path) -> bool:
    """
    True if path looks like SIS wave data.

    Accepts:
      - folder containing Waveforms/
      - folder containing nested */Waveforms/
      - folder with sis_wave*.tar.gz / *wave*.tar.gz archives
      - extracted sis_wave_v2-style root
    """
    if not path.is_dir():
        return False
    if (path / "Waveforms").is_dir():
        return True
    # Nested one level
    try:
        for child in path.iterdir():
            if child.is_dir() and (child / "Waveforms").is_dir():
                return True
            if child.is_file() and "wave" in child.name.lower() and child.suffix in {
                ".gz",
                ".tgz",
                ".tar",
            }:
                return True
            if child.is_file() and child.name.lower().startswith("sis_wave"):
                return True
    except OSError:
        return False
    # Archives only at this level
    for pat in ("*wave*.tar.gz", "*wave*.tgz", "sis_wave*"):
        if any(path.glob(pat)):
            return True
    return False


def resolve_emr_directory(path: Path | str) -> Path:
    """Resolve a user path to an EMR folder containing patient_information.csv."""
    p = Path(path).expanduser().resolve()
    if not p.is_dir():
        raise FileNotFoundError(f"Not a directory: {p}")
    for cand in (p, p / "EMR", p / "raw" / "EMR", p / "data" / "raw" / "EMR"):
        if looks_like_emr_dir(cand):
            return cand.resolve()
    raise FileNotFoundError(
        f"No SIS EMR tables found under {p}. "
        "Select the folder that contains patient_information.csv "
        "(or a parent that contains EMR/ or raw/EMR/)."
    )


def resolve_wave_directory(path: Path | str) -> Path:
    """Resolve a user path to a wave root (archives or Waveforms tree)."""
    p = Path(path).expanduser().resolve()
    if not p.is_dir():
        raise FileNotFoundError(f"Not a directory: {p}")
    if looks_like_wave_dir(p):
        return p
    for cand in (p / "wave", p / "waves", p / "sis_wave", p / "sis_wave_v2", p / "data" / "wave"):
        if cand.is_dir() and looks_like_wave_dir(cand):
            return cand.resolve()
    raise FileNotFoundError(
        f"No SIS waveform data found under {p}. "
        "Select a folder containing Waveforms/ or sis_wave*.tar.gz archives "
        f"(e.g. NAS/MOVER DATA with sis_wave_v2.tar.gz)."
    )


def default_processed_for_emr(emr: Path) -> Path:
    if emr.name == "EMR" and emr.parent.name == "raw":
        return (emr.parent.parent / "processed").resolve()
    if emr.name == "EMR":
        return (emr.parent / "processed").resolve()
    return (emr / "processed").resolve()


def resolve_user_data_choice(path: Path | str) -> dict[str, Path]:
    """
    Backward-compatible: interpret a single folder as EMR (+ processed).
    Does not set wave_dir (use configure_wave_directory separately).
    """
    p = Path(path).expanduser().resolve()
    emr = resolve_emr_directory(p)

    processed_candidates = [
        p / "processed",
        p / "data" / "processed",
        default_processed_for_emr(emr),
        emr.parent / "processed",
    ]
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
        processed = default_processed_for_emr(emr)

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
    """Resolve EMR folder from path and apply session overrides."""
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


def configure_emr_directory(path: Path | str, *, persist: bool = True) -> Path:
    emr = resolve_emr_directory(path)
    proc = processed_dir()
    # If processed still points at default empty relative to old data, retarget
    if not (proc / "cases.parquet").is_file():
        proc = default_processed_for_emr(emr)
    data_root = emr.parent.parent if (emr.name == "EMR" and emr.parent.name == "raw") else emr.parent
    set_session_paths(data_dir=data_root, emr_dir=emr, processed_dir=proc)
    if persist:
        from src.user_settings import update_settings

        update_settings(
            data_dir=str(data_root.resolve()),
            emr_dir=str(emr.resolve()),
            processed_dir=str(proc.resolve()),
        )
    return emr


def configure_wave_directory(path: Path | str, *, persist: bool = True) -> Path:
    wave = resolve_wave_directory(path)
    set_session_paths(wave_dir=wave)
    if persist:
        from src.user_settings import update_settings

        update_settings(wave_dir=str(wave.resolve()))
    return wave


def configure_processed_directory(path: Path | str, *, persist: bool = True) -> Path:
    p = Path(path).expanduser().resolve()
    if not p.is_dir():
        p.mkdir(parents=True, exist_ok=True)
    set_session_paths(processed_dir=p)
    if persist:
        from src.user_settings import update_settings

        update_settings(processed_dir=str(p))
    return p


def waveform_case_dir(pid: str, wave_root: Path | None = None) -> Path | None:
    """
    Best-effort path to a case's waveform folder under the wave root.

    SIS layout example:
      .../Waveforms/03/03e4d41adce6e85f/
    """
    root = wave_root if wave_root is not None else wave_dir()
    if root is None or not root.is_dir():
        return None
    pid = str(pid)
    prefix = pid[:2] if len(pid) >= 2 else pid
    candidates = [
        root / "Waveforms" / prefix / pid,
        root / "waveforms" / prefix / pid,
    ]
    # Nested extract roots (sis_wave_v2/.../Waveforms/..)
    try:
        for child in root.iterdir():
            if child.is_dir():
                candidates.append(child / "Waveforms" / prefix / pid)
                candidates.append(child / "waveforms" / prefix / pid)
    except OSError:
        pass
    for c in candidates:
        if c.is_dir():
            return c
    return None
