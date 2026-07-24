"""DataFrame schema and invariant checks for pipeline stages."""

from __future__ import annotations

import logging
from typing import Iterable

import numpy as np
import pandas as pd

from src.guardrails.exceptions import DataValidationError
from src.guardrails.limits import (
    ALLOWED_SEVERITIES,
    MAX_FLAG_ROWS_WARN,
    MAX_TIMESERIES_ROWS_WARN,
    REQUIRED_CASE_COLS,
    REQUIRED_CASE_SUMMARY_COLS,
    REQUIRED_FLAG_COLS,
    REQUIRED_TS_COLS,
    REQUIRED_VENT_COLS,
    REQUIRED_VITALS_COLS,
)

logger = logging.getLogger(__name__)


def _require_columns(df: pd.DataFrame, required: Iterable[str], name: str) -> None:
    if df is None:
        raise DataValidationError(f"{name}: DataFrame is None")
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise DataValidationError(f"{name}: missing columns {missing}")


def _require_nonempty(df: pd.DataFrame, name: str) -> None:
    if df is None or len(df) == 0:
        raise DataValidationError(f"{name}: expected non-empty DataFrame")


def assert_raw_cases(df: pd.DataFrame) -> None:
    _require_nonempty(df, "case_info")
    _require_columns(df, REQUIRED_CASE_COLS, "case_info")
    if df["PID"].isna().all():
        raise DataValidationError("case_info: all PIDs are null")


def assert_raw_ventilator(df: pd.DataFrame) -> None:
    _require_columns(df, REQUIRED_VENT_COLS, "ventilator")
    # Empty vent is allowed only if caller handles it; for pipeline we require data
    if len(df) == 0:
        raise DataValidationError("ventilator: no rows for selected PIDs")


def assert_raw_vitals(df: pd.DataFrame) -> None:
    _require_columns(df, REQUIRED_VITALS_COLS, "vitals")


def assert_cleaned_cases(df: pd.DataFrame) -> None:
    _require_nonempty(df, "cleaned cases")
    _require_columns(df, ["PID", "case_start", "case_end"], "cleaned cases")
    if df["PID"].duplicated().any():
        dups = df.loc[df["PID"].duplicated(), "PID"].unique()[:5]
        raise DataValidationError(f"cleaned cases: duplicate PIDs e.g. {list(dups)}")
    # PID must be string-like
    if not pd.api.types.is_string_dtype(df["PID"]) and df["PID"].dtype != object:
        # still ok if convertible
        try:
            df["PID"].astype(str)
        except Exception as e:
            raise DataValidationError(f"cleaned cases: PID not string-convertible: {e}") from e


def assert_timeseries(df: pd.DataFrame, *, allow_empty: bool = False) -> None:
    if allow_empty and (df is None or len(df) == 0):
        return
    _require_nonempty(df, "timeseries")
    _require_columns(df, REQUIRED_TS_COLS, "timeseries")

    if df["PID"].isna().any():
        raise DataValidationError("timeseries: null PID values present")

    if not pd.api.types.is_datetime64_any_dtype(df["Obs_time"]):
        raise DataValidationError(
            f"timeseries: Obs_time must be datetime, got {df['Obs_time'].dtype}"
        )

    # Sorted within PID
    for pid, g in df.groupby("PID", sort=False):
        if not g["Obs_time"].is_monotonic_increasing:
            raise DataValidationError(
                f"timeseries: Obs_time not sorted for PID={pid}"
            )
        if g["Obs_time"].duplicated().any():
            raise DataValidationError(
                f"timeseries: duplicate Obs_time for PID={pid}"
            )

    # t_min finite when present
    if "t_min" in df.columns:
        bad = df["t_min"].isna().mean()
        if bad > 0.5:
            raise DataValidationError(
                f"timeseries: >50% of t_min is null ({bad:.0%})"
            )

    if len(df) > MAX_TIMESERIES_ROWS_WARN:
        logger.warning(
            "timeseries: %s rows exceeds soft safety limit %s; "
            "consider reducing n_cases",
            len(df),
            MAX_TIMESERIES_ROWS_WARN,
        )


def assert_flags(df: pd.DataFrame, *, allow_empty: bool = True) -> None:
    if df is None:
        raise DataValidationError("flags: DataFrame is None")
    _require_columns(df, REQUIRED_FLAG_COLS, "flags")
    if len(df) == 0:
        if allow_empty:
            return
        raise DataValidationError("flags: empty but allow_empty=False")

    bad_sev = set(df["severity"].dropna().unique()) - ALLOWED_SEVERITIES
    if bad_sev:
        raise DataValidationError(f"flags: invalid severities {bad_sev}")

    if df["rule_id"].isna().any():
        raise DataValidationError("flags: null rule_id present")

    if df["PID"].isna().any():
        raise DataValidationError("flags: null PID present")

    if len(df) > MAX_FLAG_ROWS_WARN:
        logger.warning(
            "flags: %s rows exceeds soft safety limit %s",
            len(df),
            MAX_FLAG_ROWS_WARN,
        )


def assert_case_summary(df: pd.DataFrame) -> None:
    _require_nonempty(df, "case_summary")
    _require_columns(df, REQUIRED_CASE_SUMMARY_COLS, "case_summary")

    if df["PID"].duplicated().any():
        raise DataValidationError("case_summary: duplicate PIDs")

    for col in ("anomaly_score", "n_warn", "n_critical"):
        if not pd.api.types.is_numeric_dtype(df[col]):
            raise DataValidationError(f"case_summary: {col} must be numeric")
        if (df[col] < 0).any():
            raise DataValidationError(f"case_summary: {col} has negative values")

    # Scores must be integers-like
    for col in ("anomaly_score", "n_warn", "n_critical"):
        s = df[col]
        if not np.allclose(s, np.round(s), equal_nan=True):
            raise DataValidationError(f"case_summary: {col} must be integer-valued")


def assert_flag_pid_subset(flags: pd.DataFrame, cases: pd.DataFrame) -> None:
    """Every flagged PID must appear in the case summary."""
    if flags is None or flags.empty:
        return
    case_pids = set(cases["PID"].astype(str))
    flag_pids = set(flags["PID"].astype(str))
    orphan = flag_pids - case_pids
    if orphan:
        sample = list(orphan)[:5]
        raise DataValidationError(
            f"flags reference PIDs not in case_summary: {sample}"
        )


def assert_no_future_leakage(ts: pd.DataFrame, cases: pd.DataFrame, pad_minutes: float = 5.0) -> None:
    """
    Soft check: observations should not be far outside case window.
    Allows pad_minutes on each side (same as filter_to_case_window).
    """
    if "case_start" not in ts.columns or "case_end" not in ts.columns:
        return
    meta = cases[["PID", "case_start", "case_end"]].drop_duplicates("PID")
    m = ts.merge(meta, on="PID", how="left", suffixes=("", "_meta"))
    start = m["case_start_meta"] if "case_start_meta" in m.columns else m["case_start"]
    end = m["case_end_meta"] if "case_end_meta" in m.columns else m["case_end"]
    # Only check rows with known windows
    known = start.notna() & end.notna()
    if not known.any():
        return
    lo = start - pd.Timedelta(minutes=pad_minutes + 1)
    hi = end + pd.Timedelta(minutes=pad_minutes + 1)
    outside = known & ((m["Obs_time"] < lo) | (m["Obs_time"] > hi))
    frac = outside.mean()
    if frac > 0.05:
        raise DataValidationError(
            f"timeseries: {frac:.1%} of rows fall outside case window ± pad; "
            "filter_to_case_window may have been skipped"
        )


def validate_pipeline_outputs(
    *,
    timeseries: pd.DataFrame,
    cases: pd.DataFrame,
    flags: pd.DataFrame,
    episodes: pd.DataFrame | None = None,
) -> None:
    """Final gate before writing/serving results."""
    assert_timeseries(timeseries)
    assert_case_summary(cases)
    assert_flags(flags, allow_empty=True)
    assert_flag_pid_subset(flags, cases)

    # Case PIDs should be subset of timeseries PIDs
    ts_pids = set(timeseries["PID"].astype(str))
    case_pids = set(cases["PID"].astype(str))
    missing_ts = case_pids - ts_pids
    if missing_ts:
        raise DataValidationError(
            f"case_summary PIDs missing from timeseries: {list(missing_ts)[:5]}"
        )

    if episodes is not None and len(episodes) > 0:
        _require_columns(
            episodes,
            ["PID", "rule_id", "severity", "t_start_min", "t_end_min"],
            "episodes",
        )
        if (episodes["t_end_min"] < episodes["t_start_min"]).any():
            raise DataValidationError("episodes: t_end_min < t_start_min")
