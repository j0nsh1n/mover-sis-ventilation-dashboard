"""Time-align ventilator and vitals streams per surgery."""

from __future__ import annotations

import numpy as np
import pandas as pd


VENT_VALUE_COLS = [
    "Agent", "Agent_name", "Agent_Fi", "Agent_Et",
    "ETCO2", "FICO2", "TV", "RR", "PEEP", "PIP",
    "FIN2O", "ETN2O", "FIO2", "ETO2",
]

VITALS_VALUE_COLS = ["HRe", "HRp", "HR", "nSBP", "nMAP", "nDBP", "SPO2"]

# Per-case constants copied onto the empty minutes that fill_minute_grid inserts.
CASE_CONSTANT_COLS = [
    "Age", "Ht", "Wt", "Gender", "BMI", "IBW_kg",
    "dq_anthropometrics_ok", "dq_reasons",
    "Procedure", "Procedure_short",
    "OR_start", "OR_end", "Surgery_start", "Surgery_end",
    "case_start", "case_end", "OR_duration_min", "Surgery_duration_min",
    "case_duration_min",
]

# A case spanning more than this is left as-is rather than expanded minute by minute.
MAX_GRID_MINUTES = 24 * 60 + 240


def fill_minute_grid(ts: pd.DataFrame) -> pd.DataFrame:
    """
    Give every case one row per minute from its first to its last observation.

    Rolling windows and run-length rules count rows. Without this, a gap in
    the SIS export (the ventilator file is truncated at 1,048,575 rows) makes
    "10 rows" span far more than 10 minutes. Inserted rows carry the case's
    metadata and NaN for every signal. Idempotent: a gridded frame is returned
    unchanged. Frames that are not minute-aligned or have duplicate
    (PID, Obs_time) rows are returned unchanged; a case spanning more than
    MAX_GRID_MINUTES is kept as-is.
    """
    if ts.empty or "PID" not in ts.columns or "Obs_time" not in ts.columns:
        return ts
    times = ts["Obs_time"]
    if not pd.api.types.is_datetime64_any_dtype(times) or times.isna().any():
        return ts
    if not (times == times.dt.floor("min")).all():
        return ts
    if ts.duplicated(subset=["PID", "Obs_time"]).any():
        return ts

    pieces = []
    changed = False
    for pid, g in ts.groupby("PID", sort=False):
        g = g.sort_values("Obs_time")
        start, end = g["Obs_time"].iloc[0], g["Obs_time"].iloc[-1]
        n_expected = int((end - start) / pd.Timedelta(minutes=1)) + 1
        if n_expected == len(g) or n_expected > MAX_GRID_MINUTES:
            pieces.append(g)
            continue
        changed = True
        grid = pd.date_range(start, end, freq="min", name="Obs_time")
        full = g.set_index("Obs_time").reindex(grid).reset_index()
        full["PID"] = pid
        consts = [c for c in CASE_CONSTANT_COLS if c in full.columns]
        if consts:
            full[consts] = full[consts].ffill().bfill()
        if "t_min" in full.columns:
            # t_min is linear in Obs_time; recover its anchor from a real row
            first = g.iloc[0]
            anchor = first["Obs_time"] - pd.to_timedelta(first["t_min"], unit="min")
            full["t_min"] = (full["Obs_time"] - anchor).dt.total_seconds() / 60.0
            if "case_frac" in full.columns and "case_duration_min" in full.columns:
                dur = full["case_duration_min"]
                full["case_frac"] = np.where(dur > 0, full["t_min"] / dur, np.nan)
        pieces.append(full[g.columns])

    if not changed:
        return ts
    return pd.concat(pieces, ignore_index=True)


def _minute_bin(df: pd.DataFrame, time_col: str = "Obs_time") -> pd.DataFrame:
    out = df.copy()
    out["time_bin"] = out[time_col].dt.floor("min")
    return out


def _agg_mode(s: pd.Series):
    s = s.dropna()
    if s.empty:
        return np.nan
    return s.mode().iloc[0]


def aggregate_to_minute(df: pd.DataFrame, value_cols: list[str], time_col: str = "Obs_time") -> pd.DataFrame:
    """Collapse to one row per (PID, minute) using median / mode."""
    if df.empty:
        return df
    out = _minute_bin(df, time_col)
    present = [c for c in value_cols if c in out.columns]
    cat_cols = [c for c in present if c in ("Agent", "Agent_name")]
    num_cols = [c for c in present if c not in cat_cols]

    aggs: dict = {}
    for c in num_cols:
        aggs[c] = "median"
    for c in cat_cols:
        aggs[c] = _agg_mode

    grouped = out.groupby(["PID", "time_bin"], as_index=False).agg(aggs)
    return grouped


def merge_vent_vitals(
    vent: pd.DataFrame,
    vitals: pd.DataFrame,
    cases: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    Outer-merge minute-aligned ventilator and vitals on (PID, time_bin).

    Adds case metadata and minutes-from-start when `cases` is provided.
    """
    v = aggregate_to_minute(vent, VENT_VALUE_COLS)
    w = aggregate_to_minute(vitals, VITALS_VALUE_COLS)

    if v.empty and w.empty:
        return pd.DataFrame()

    if v.empty:
        merged = w.copy()
    elif w.empty:
        merged = v.copy()
    else:
        merged = pd.merge(v, w, on=["PID", "time_bin"], how="outer", suffixes=("", "_vit"))

    merged = merged.sort_values(["PID", "time_bin"]).reset_index(drop=True)
    merged = merged.rename(columns={"time_bin": "Obs_time"})

    if cases is not None and not cases.empty:
        meta_cols = [
            "PID", "Age", "Ht", "Wt", "Gender", "BMI", "IBW_kg",
            "dq_anthropometrics_ok", "dq_reasons",
            "Procedure", "Procedure_short",
            "OR_start", "OR_end", "Surgery_start", "Surgery_end",
            "case_start", "case_end", "OR_duration_min", "Surgery_duration_min",
        ]
        meta_cols = [c for c in meta_cols if c in cases.columns]
        meta = cases[meta_cols].drop_duplicates("PID")
        merged = merged.merge(meta, on="PID", how="left")

        # Minutes from case start (fallback: first observation in case)
        first_obs = merged.groupby("PID")["Obs_time"].transform("min")
        anchor = merged["case_start"].fillna(first_obs)
        merged["t_min"] = (merged["Obs_time"] - anchor).dt.total_seconds() / 60.0
        end_anchor = merged["case_end"]
        duration = (end_anchor - anchor).dt.total_seconds() / 60.0
        duration = duration.fillna(merged.groupby("PID")["t_min"].transform("max"))
        merged["case_duration_min"] = duration
        merged["case_frac"] = np.where(
            duration > 0,
            merged["t_min"] / duration,
            np.nan,
        )
    else:
        first_obs = merged.groupby("PID")["Obs_time"].transform("min")
        merged["t_min"] = (merged["Obs_time"] - first_obs).dt.total_seconds() / 60.0
        merged["case_duration_min"] = merged.groupby("PID")["t_min"].transform("max")
        merged["case_frac"] = merged["t_min"] / merged["case_duration_min"].replace(0, np.nan)

    return fill_minute_grid(merged)
