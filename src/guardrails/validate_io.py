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
    return Path(__file__).resolve().parents[2]


def _resolve(path: Path) -> Path:
    return path.expanduser().resolve()


def resolve_emr_dir(emr_dir: Path | str | None = None) -> Path:
    """
    Resolve EMR directory containing patient_*.csv files.

    Accepts the EMR folder itself, or a parent that contains EMR/.
    """
    if emr_dir is None:
        candidates = [
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


def resolve_output_dir(
    output_dir: Path | str | None = None,
    *,
    create: bool = True,
) -> Path:
    """
    Resolve output directory. By default must stay under the repo root
    unless MOVER_ALLOW_EXTERNAL_OUTPUT=1.
    """
    root = repo_root()
    if output_dir is None:
        out = root / "data" / "processed"
    else:
        out = Path(output_dir)

    out = _resolve(out) if out.exists() or out.parent.exists() else out.expanduser().absolute()

    # Path containment check
    if not allow_external_output():
        try:
            out_resolved = out if out.exists() else out
            # Compare against repo root after resolving parent
            root_res = _resolve(root)
            candidate = out_resolved
            # If not yet created, resolve parent + name
            if not candidate.exists():
                parent = _resolve(candidate.parent) if candidate.parent.exists() else candidate.parent.absolute()
                candidate = parent / candidate.name
            else:
                candidate = _resolve(candidate)

            if not str(candidate).startswith(str(root_res)):
                raise PathSafetyError(
                    f"Refusing to write outside repository ({root_res}). "
                    f"Requested: {candidate}. Set MOVER_ALLOW_EXTERNAL_OUTPUT=1 to override."
                )
            out = candidate
        except PathSafetyError:
            raise
        except OSError as e:
            raise PathSafetyError(f"Cannot resolve output path {out}: {e}") from e

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
