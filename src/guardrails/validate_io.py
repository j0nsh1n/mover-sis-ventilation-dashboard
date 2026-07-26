"""Filesystem and parameter safety checks."""

from __future__ import annotations

from pathlib import Path

from src.guardrails.exceptions import PathSafetyError, PipelineError, SafetyLimitError
from src.guardrails.limits import (
    ALLOWED_PRESETS,
    MAX_N_CASES,
    MAX_PAD_MINUTES,
    MAX_VENT_SCAN_ROWS,
    MIN_N_CASES,
    MIN_PAD_MINUTES,
    MIN_VENT_ROWS_PER_CASE,
    MIN_VENT_SCAN_ROWS,
    REQUIRED_EMR_FILES,
    allow_external_output,
)


def repo_root() -> Path:
    from src.runtime_paths import app_dir

    return app_dir()


def _resolve(path: Path) -> Path:
    return path.expanduser().resolve()


def resolve_emr_dir(emr_dir: Path | str | None = None) -> Path:
    """
    Resolve EMR directory containing patient_*.csv files.

    Accepts the EMR folder itself, or a parent that contains EMR/.
    """
    if emr_dir is None:
        from src.runtime_paths import data_dir, emr_dir as default_emr

        candidates = [
            default_emr(),
            data_dir() / "raw" / "EMR",
            data_dir() / "raw",
            repo_root() / "data" / "raw" / "EMR",
            repo_root() / "data" / "raw",
        ]
    else:
        root = Path(emr_dir)
        candidates = [
            root,
            root / "EMR",
            root / "raw" / "EMR",
        ]

    for cand in candidates:
        if cand.is_dir() and (cand / "patient_information.csv").exists():
            return _resolve(cand)

    tried = ", ".join(str(c) for c in candidates)
    raise PathSafetyError(
        f"Could not locate SIS EMR directory with patient_information.csv. Tried: {tried}"
    )


def validate_emr_dir(emr_dir: Path | str) -> Path:
    """Confirm required EMR CSVs exist and are non-empty."""
    path = resolve_emr_dir(emr_dir)
    if not path.is_dir():
        raise PathSafetyError(f"EMR path is not a directory: {path}")

    missing = []
    empty = []
    for name in REQUIRED_EMR_FILES:
        f = path / name
        if not f.is_file():
            missing.append(name)
        elif f.stat().st_size == 0:
            empty.append(name)

    if missing:
        raise PipelineError(
            f"EMR directory {path} missing required files: {', '.join(missing)}"
        )
    if empty:
        raise PipelineError(
            f"EMR directory {path} has empty required files: {', '.join(empty)}"
        )
    return path


def _normalize_candidate(path: Path) -> Path:
    """Resolve path for containment checks (works for not-yet-created dirs)."""
    if path.exists():
        return _resolve(path)
    parent = path.parent
    if parent.exists():
        return _resolve(parent) / path.name
    return path.expanduser().absolute()


def _is_under(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        # Fallback string prefix for not-yet-created paths
        c, p = str(child), str(parent)
        return c == p or c.startswith(p.rstrip("/") + "/")


def resolve_output_dir(
    output_dir: Path | str | None = None,
    *,
    create: bool = True,
) -> Path:
    """
    Resolve output directory for processed pipeline artifacts.

    Allowed without MOVER_ALLOW_EXTERNAL_OUTPUT:
      - under the app install / repo root
      - the configured processed_dir()
      - under the configured data_dir()
      - under the parent of the configured EMR dir (e.g. /var/mnt/games/processed
        when EMR is /var/mnt/games/EMR)

    Set MOVER_ALLOW_EXTERNAL_OUTPUT=1 to allow any path.
    """
    from src.runtime_paths import app_dir, data_dir, emr_dir
    from src.runtime_paths import processed_dir as default_processed

    if output_dir is None:
        out = default_processed()
    else:
        out = Path(output_dir)

    candidate = _normalize_candidate(out)

    if not allow_external_output():
        allowed_roots: list[Path] = [app_dir()]
        try:
            allowed_roots.append(data_dir())
        except Exception:
            pass
        try:
            allowed_roots.append(default_processed())
        except Exception:
            pass
        try:
            # Parent of EMR is a natural place for a sibling processed/ folder
            allowed_roots.append(emr_dir().parent)
            if emr_dir().name == "EMR":
                allowed_roots.append(emr_dir().parent.parent)
        except Exception:
            pass

        # Deduplicate
        roots: list[Path] = []
        for r in allowed_roots:
            try:
                rr = _resolve(r) if r.exists() else Path(r).expanduser().absolute()
            except OSError:
                rr = Path(r).expanduser().absolute()
            if rr not in roots:
                roots.append(rr)

        if not any(_is_under(candidate, root) or candidate == root for root in roots):
            raise PathSafetyError(
                f"Refusing to write processed output to {candidate}. "
                f"Allowed roots include the app directory and your selected "
                f"EMR/data folders ({', '.join(str(r) for r in roots[:4])}…). "
                f"Set MOVER_ALLOW_EXTERNAL_OUTPUT=1 to allow any path."
            )
        out = candidate
    else:
        out = candidate

    if create:
        try:
            out.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise PipelineError(f"Cannot create output directory {out}: {e}") from e

    if out.exists() and not out.is_dir():
        raise PathSafetyError(f"Output path exists but is not a directory: {out}")

    return out


def validate_pipeline_params(
    *,
    n_cases: int,
    preset: str,
    seed: int,
    vent_scan_rows: int,
    min_vent_rows: int,
    pad_minutes: float = 5.0,
    pids: list[str] | None = None,
) -> None:
    """Raise SafetyLimitError if CLI/API parameters are out of bounds."""
    if preset not in ALLOWED_PRESETS:
        raise SafetyLimitError(
            f"Invalid preset {preset!r}; allowed: {sorted(ALLOWED_PRESETS)}"
        )

    if not isinstance(seed, int):
        raise SafetyLimitError(f"seed must be int, got {type(seed).__name__}")

    if pids is None:
        if not isinstance(n_cases, int) or isinstance(n_cases, bool):
            raise SafetyLimitError(f"n_cases must be int, got {n_cases!r}")
        if n_cases < MIN_N_CASES or n_cases > MAX_N_CASES:
            raise SafetyLimitError(
                f"n_cases={n_cases} outside allowed range "
                f"[{MIN_N_CASES}, {MAX_N_CASES}]"
            )
    else:
        if not isinstance(pids, (list, tuple)):
            raise SafetyLimitError("pids must be a list of strings")
        if len(pids) == 0:
            raise SafetyLimitError("pids list is empty")
        if len(pids) > MAX_N_CASES:
            raise SafetyLimitError(
                f"len(pids)={len(pids)} exceeds MAX_N_CASES={MAX_N_CASES}"
            )
        for p in pids:
            if not str(p).strip():
                raise SafetyLimitError("pids contains empty identifier")

    if not isinstance(vent_scan_rows, int) or isinstance(vent_scan_rows, bool):
        raise SafetyLimitError(f"vent_scan_rows must be int, got {vent_scan_rows!r}")
    if vent_scan_rows < MIN_VENT_SCAN_ROWS or vent_scan_rows > MAX_VENT_SCAN_ROWS:
        raise SafetyLimitError(
            f"vent_scan_rows={vent_scan_rows} outside "
            f"[{MIN_VENT_SCAN_ROWS}, {MAX_VENT_SCAN_ROWS}]"
        )

    if not isinstance(min_vent_rows, int) or isinstance(min_vent_rows, bool):
        raise SafetyLimitError(f"min_vent_rows must be int, got {min_vent_rows!r}")
    if min_vent_rows < MIN_VENT_ROWS_PER_CASE:
        raise SafetyLimitError(
            f"min_vent_rows={min_vent_rows} < MIN_VENT_ROWS_PER_CASE={MIN_VENT_ROWS_PER_CASE}"
        )

    if pad_minutes < MIN_PAD_MINUTES or pad_minutes > MAX_PAD_MINUTES:
        raise SafetyLimitError(
            f"pad_minutes={pad_minutes} outside [{MIN_PAD_MINUTES}, {MAX_PAD_MINUTES}]"
        )


def atomic_write_parquet(df, path: Path) -> None:
    """Write parquet via temp file + replace to avoid partial files."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        df.to_parquet(tmp, index=False)
        tmp.replace(path)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


def atomic_write_json(obj: dict, path: Path) -> None:
    import json

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2)
        tmp.replace(path)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise
