"""UI-agnostic data loading and pipeline orchestration."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.guardrails.exceptions import GuardrailError
from src.guardrails.limits import ALLOWED_PRESETS, MAX_N_CASES, MIN_N_CASES
from src.guardrails.validate_data import validate_pipeline_outputs
from src.pipeline.run import run_pipeline
from src.runtime_paths import app_dir, emr_dir as default_emr_dir
from src.runtime_paths import processed_dir as default_processed_dir

REPO_ROOT = app_dir()
PROCESSED_DIR = default_processed_dir()


def load_processed(
    processed_dir: Path | str | None = None,
    *,
    validate: bool = True,
):
    """
    Load pipeline outputs.

    Parameters
    ----------
    validate :
        When True (default), run full output guardrails. UI auto-load may
        pass False for speed after the pipeline has already validated on write.
    """
    p = Path(processed_dir) if processed_dir is not None else default_processed_dir()
    required = ["cases.parquet", "timeseries.parquet", "flags.parquet"]
    missing = [f for f in required if not (p / f).is_file()]
    if missing:
        raise FileNotFoundError(f"Missing processed files in {p}: {missing}")

    cases = pd.read_parquet(p / "cases.parquet")
    ts = pd.read_parquet(p / "timeseries.parquet")
    flags = pd.read_parquet(p / "flags.parquet")
    episodes = (
        pd.read_parquet(p / "episodes.parquet")
        if (p / "episodes.parquet").exists()
        else pd.DataFrame()
    )
    events = (
        pd.read_parquet(p / "events.parquet")
        if (p / "events.parquet").exists()
        else pd.DataFrame()
    )

    if validate:
        validate_pipeline_outputs(
            timeseries=ts,
            cases=cases,
            flags=flags,
            episodes=episodes if not episodes.empty else None,
        )
    else:
        # Lightweight schema sanity only
        for name, df, cols in (
            ("cases", cases, ["PID", "anomaly_score"]),
            ("timeseries", ts, ["PID", "Obs_time"]),
            ("flags", flags, ["PID", "rule_id", "severity"]),
        ):
            missing_cols = [c for c in cols if c not in df.columns]
            if missing_cols:
                raise FileNotFoundError(
                    f"{name} missing columns {missing_cols} under {p}"
                )
    return cases, ts, flags, episodes, events


def ensure_data(
    n_cases: int = 50,
    preset: str = "default",
    force: bool = False,
    processed_dir: Path | str | None = None,
    emr_dir: Path | str | None = None,
    pids: list[str] | None = None,
    min_vent_rows: int = 30,
    seed: int = 42,
):
    """
    Ensure processed data exists; run pipeline if missing or force=True.

    Raises GuardrailError on invalid params or pipeline guardrail failures.
    """
    if pids is None and (n_cases < MIN_N_CASES or n_cases > MAX_N_CASES):
        raise GuardrailError(
            f"n_cases={n_cases} outside [{MIN_N_CASES}, {MAX_N_CASES}]"
        )
    if preset not in ALLOWED_PRESETS:
        raise GuardrailError(f"Invalid preset {preset!r}")

    out = Path(processed_dir) if processed_dir is not None else default_processed_dir()
    emr = Path(emr_dir) if emr_dir is not None else default_emr_dir()
    need = force or not (out / "cases.parquet").exists()
    if need:
        run_pipeline(
            emr_dir=emr,
            n_cases=n_cases,
            pids=pids,
            preset=preset,
            output_dir=out,
            validate=True,
            min_vent_rows=min_vent_rows,
            seed=seed,
            write_sample_csv=False,
        )
    return load_processed(out)
