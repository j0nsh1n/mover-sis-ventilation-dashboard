"""Cleaning and physiologic clamping for SIS tables."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.config import load_thresholds
from src.pipeline.load import AGENT_MAP


def parse_datetime(series: pd.Series) -> pd.Series:
    """Parse mixed SIS datetime formats."""
    return pd.to_datetime(series, format="mixed", errors="coerce")


def _to_numeric(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    out = df.copy()
    for c in cols:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    return out


def _clamp(series: pd.Series, low: float, high: float) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    return s.where(s.between(low, high))


def apply_clean_ranges(df: pd.DataFrame, ranges: dict, colmap: dict[str, str] | None = None) -> pd.DataFrame:
    """Apply soft physiologic clamps from thresholds.yaml clean_ranges."""
    out = df.copy()
    colmap = colmap or {}
    for key, bounds in ranges.items():
        col = colmap.get(key, key)
        if col not in out.columns:
            continue
        low, high = bounds
        out[col] = _clamp(out[col], low, high)
    return out


def ideal_body_weight_kg(height_cm: float, gender: str) -> float | np.floating:
    """Devine IBW formula. Height in cm → kg."""
    if pd.isna(height_cm) or height_cm <= 0:
        return np.nan
    height_in = float(height_cm) / 2.54
    g = (gender or "").strip().upper()
    if g.startswith("M"):
        return 50.0 + 2.3 * (height_in - 60.0)
    if g.startswith("F"):
        return 45.5 + 2.3 * (height_in - 60.0)
    # Unknown sex: average of male/female formulas
    return 47.75 + 2.3 * (height_in - 60.0)


def clean_case_info(df: pd.DataFrame, max_or_hours: float = 24.0) -> pd.DataFrame:
    """Clean patient_information: times, duration, BMI, IBW."""
    out = df.copy()
    out["PID"] = out["PID"].astype(str)

    for col in ["OR_start", "OR_end", "Surgery_start", "Surgery_end"]:
        if col in out.columns:
            out[col] = parse_datetime(out[col])

    out["Age"] = pd.to_numeric(out.get("Age"), errors="coerce")
    out["Ht"] = pd.to_numeric(out.get("Ht"), errors="coerce")
    out["Wt"] = pd.to_numeric(out.get("Wt"), errors="coerce")

    # Height: values look like cm (median ~165). Weight kg.
    out["BMI"] = out["Wt"] / (out["Ht"] / 100.0) ** 2
    out["BMI"] = out["BMI"].where(out["BMI"].between(10, 80))

    out["Gender"] = (
        out.get("Gender", pd.Series(index=out.index, dtype="object"))
        .astype(str)
        .str.strip()
        .replace({"Unknown": "U", "nan": "U", "": "U"})
    )
    out.loc[~out["Gender"].isin(["M", "F", "U"]), "Gender"] = "U"

    out["IBW_kg"] = [
        ideal_body_weight_kg(h, g) for h, g in zip(out["Ht"], out["Gender"])
    ]
    out["IBW_kg"] = pd.to_numeric(out["IBW_kg"], errors="coerce")
    out.loc[out["IBW_kg"] <= 0, "IBW_kg"] = np.nan

    out["OR_duration_min"] = (out["OR_end"] - out["OR_start"]).dt.total_seconds() / 60.0
    out["Surgery_duration_min"] = (
        out["Surgery_end"] - out["Surgery_start"]
    ).dt.total_seconds() / 60.0

    # Drop absurd durations (timestamp shift artifacts)
    max_min = max_or_hours * 60.0
    out.loc[out["OR_duration_min"] > max_min, "OR_duration_min"] = np.nan
    out.loc[out["OR_duration_min"] < 0, "OR_duration_min"] = np.nan
    out.loc[out["Surgery_duration_min"] > max_min, "Surgery_duration_min"] = np.nan
    out.loc[out["Surgery_duration_min"] < 0, "Surgery_duration_min"] = np.nan

    if "Procedure" in out.columns:
        out["Procedure"] = out["Procedure"].astype(str).str.replace(r"\s+", " ", regex=True).str.strip()
        out["Procedure_short"] = out["Procedure"].str.slice(0, 80)

    # Preferred case anchor time
    out["case_start"] = out["Surgery_start"].fillna(out["OR_start"])
    out["case_end"] = out["Surgery_end"].fillna(out["OR_end"])

    return out


def clean_ventilator(df: pd.DataFrame, thresholds: dict | None = None) -> pd.DataFrame:
    """Clean ventilator table to numeric signals + parsed time."""
    if thresholds is None:
        thresholds = load_thresholds()
    ranges = thresholds.get("clean_ranges", {})

    out = df.copy()
    out["PID"] = out["PID"].astype(str)
    out["Obs_time"] = parse_datetime(out["Obs_time"])

    num_cols = [
        "Agent_Fi", "Agent_Et", "ETCO2", "FICO2", "TV", "RR", "PEEP", "PIP",
        "FIN2O", "ETN2O", "FIO2", "ETO2",
    ]
    out = _to_numeric(out, num_cols)

    if "Agent" in out.columns:
        out["Agent"] = out["Agent"].astype(str).str.strip().str.upper()
        out.loc[~out["Agent"].isin(["S", "D", "I", "N"]), "Agent"] = np.nan
        out["Agent_name"] = out["Agent"].map(AGENT_MAP)

    # Soft clamps (zeros often = not connected; leave 0 ETCO2 before clamp for flag logic)
    out["_ETCO2_raw"] = out["ETCO2"] if "ETCO2" in out.columns else np.nan
    out = apply_clean_ranges(out, ranges)

    # Restore true zeros for ETCO2 flag detection (clamp removed 0–5)
    if "_ETCO2_raw" in out.columns:
        raw = out["_ETCO2_raw"]
        # Keep cleaned values; re-insert exact zeros
        out.loc[raw == 0, "ETCO2"] = 0.0
        out = out.drop(columns=["_ETCO2_raw"])

    out = out.dropna(subset=["Obs_time"])
    out = out.sort_values(["PID", "Obs_time"])
    out = out.drop_duplicates(subset=["PID", "Obs_time"], keep="last")
    return out.reset_index(drop=True)


def clean_vitals(df: pd.DataFrame, thresholds: dict | None = None) -> pd.DataFrame:
    """Clean vitals table."""
    if thresholds is None:
        thresholds = load_thresholds()
    ranges = thresholds.get("clean_ranges", {})

    out = df.copy()
    out["PID"] = out["PID"].astype(str)
    out["Obs_time"] = parse_datetime(out["Obs_time"])

    num_cols = ["HRe", "HRp", "nSBP", "nMAP", "nDBP", "SPO2"]
    out = _to_numeric(out, num_cols)

    # Prefer ECG HR, fall back to pulse ox HR
    out["HR"] = out["HRe"].fillna(out["HRp"]) if "HRe" in out.columns else out.get("HRp")

    out = apply_clean_ranges(
        out,
        ranges,
        colmap={"HR": "HR", "SPO2": "SPO2", "nSBP": "nSBP", "nMAP": "nMAP", "nDBP": "nDBP"},
    )
    # Also clamp HRe/HRp individually if present
    if "HRe" in out.columns:
        out["HRe"] = _clamp(out["HRe"], *ranges.get("HR", [30, 200]))
    if "HRp" in out.columns:
        out["HRp"] = _clamp(out["HRp"], *ranges.get("HR", [30, 200]))

    out = out.dropna(subset=["Obs_time"])
    out = out.sort_values(["PID", "Obs_time"])
    out = out.drop_duplicates(subset=["PID", "Obs_time"], keep="last")
    return out.reset_index(drop=True)


def clean_procedure_events(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["PID", "Event_time", "Event_name"])
    out = df.copy()
    out["PID"] = out["PID"].astype(str)
    time_col = "Event_time" if "Event_time" in out.columns else out.columns[1]
    name_col = "Event_name" if "Event_name" in out.columns else out.columns[2]
    out = out.rename(columns={time_col: "Event_time", name_col: "Event_name"})
    out["Event_time"] = parse_datetime(out["Event_time"])
    out["Event_name"] = out["Event_name"].astype(str).str.strip()
    out = out.dropna(subset=["Event_time"])
    return out.sort_values(["PID", "Event_time"]).reset_index(drop=True)


def filter_to_case_window(
    ts: pd.DataFrame,
    cases: pd.DataFrame,
    pad_minutes: float = 5.0,
    time_col: str = "Obs_time",
) -> pd.DataFrame:
    """Keep rows within [case_start - pad, case_end + pad]."""
    meta = cases[["PID", "case_start", "case_end"]].drop_duplicates("PID")
    out = ts.merge(meta, on="PID", how="left")
    start = out["case_start"] - pd.Timedelta(minutes=pad_minutes)
    end = out["case_end"] + pd.Timedelta(minutes=pad_minutes)
    mask = out["case_start"].isna() | (
        (out[time_col] >= start) & (out[time_col] <= end)
    )
    return out.loc[mask].drop(columns=["case_start", "case_end"], errors="ignore").reset_index(drop=True)
