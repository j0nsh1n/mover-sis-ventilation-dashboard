"""Derived features for plotting and anomaly rules."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.config import load_thresholds
from src.pipeline.merge import fill_minute_grid

# Signals that mark a grid minute as observed (see build_case_summary n_minutes).
OBSERVED_SIGNAL_COLS = [
    "TV", "RR", "PEEP", "PIP", "ETCO2", "FIO2", "Agent_Et", "Agent_Fi",
    "HR", "HRe", "HRp", "SPO2", "nMAP", "nSBP", "nDBP",
]


def age_adjusted_mac(agent: str, age: float, thresholds: dict | None = None) -> float:
    """Return age-adjusted 1.0 MAC in vol% for agent code S/D/I."""
    if thresholds is None:
        thresholds = load_thresholds()
    mac40 = thresholds.get("mac_age40", {})
    coef = thresholds.get("mac_age_coef", -0.00269)
    if agent not in mac40 or mac40[agent] is None:
        return np.nan
    if pd.isna(age):
        age = 40.0
    return float(mac40[agent]) * (10 ** (coef * (float(age) - 40.0)))


def add_features(ts: pd.DataFrame, thresholds: dict | None = None) -> pd.DataFrame:
    """Add TV mL/kg, MAC fraction, PIP rolling slope, mechanical-vent hint.

    The frame is first put on a one-row-per-minute grid (fill_minute_grid), so
    every row-based window below spans real minutes.
    """
    if thresholds is None:
        thresholds = load_thresholds()
    rules = thresholds.get("rules", {})

    out = fill_minute_grid(ts).copy()

    # TV per IBW
    if "TV" in out.columns and "IBW_kg" in out.columns:
        out["TV_mlkg"] = out["TV"] / out["IBW_kg"]
        out.loc[~np.isfinite(out["TV_mlkg"]), "TV_mlkg"] = np.nan
    else:
        out["TV_mlkg"] = np.nan

    # Age-adjusted MAC and ET MAC fraction
    ages = out["Age"] if "Age" in out.columns else pd.Series(np.nan, index=out.index)
    agents = out["Agent"] if "Agent" in out.columns else pd.Series(np.nan, index=out.index)

    mac1 = [
        age_adjusted_mac(a, age, thresholds) if pd.notna(a) else np.nan
        for a, age in zip(agents, ages)
    ]
    out["MAC_1pct"] = mac1
    if "Agent_Et" in out.columns:
        out["MAC_Et"] = out["Agent_Et"] / out["MAC_1pct"]
        out.loc[~np.isfinite(out["MAC_Et"]), "MAC_Et"] = np.nan
    else:
        out["MAC_Et"] = np.nan

    if "Agent_Fi" in out.columns:
        out["MAC_Fi"] = out["Agent_Fi"] / out["MAC_1pct"]
        out.loc[~np.isfinite(out["MAC_Fi"]), "MAC_Fi"] = np.nan
        out["Agent_Fi_Et_gap"] = out["Agent_Fi"] - out["Agent_Et"]
    else:
        out["MAC_Fi"] = np.nan
        out["Agent_Fi_Et_gap"] = np.nan

    # Total end-tidal MAC: volatile fraction plus N2O fraction (MACs are additive)
    n2o_mac40 = thresholds.get("n2o_mac_age40")
    if n2o_mac40 and "ETN2O" in out.columns:
        coef = thresholds.get("mac_age_coef", -0.00269)
        age_filled = pd.to_numeric(ages, errors="coerce").fillna(40.0)
        n2o_mac1 = float(n2o_mac40) * np.power(10.0, coef * (age_filled - 40.0))
        n2o_frac = out["ETN2O"] / n2o_mac1
    else:
        n2o_frac = pd.Series(np.nan, index=out.index)
    both_missing = out["MAC_Et"].isna() & n2o_frac.isna()
    out["MAC_total_Et"] = (out["MAC_Et"].fillna(0) + n2o_frac.fillna(0)).where(~both_missing)

    # Mechanical ventilation heuristic
    tv = out["TV"] if "TV" in out.columns else pd.Series(np.nan, index=out.index)
    rr = out["RR"] if "RR" in out.columns else pd.Series(np.nan, index=out.index)
    out["likely_mech_vent"] = (tv >= 200) & (rr >= 6)

    # PIP rolling slope (cmH2O per minute); one row per minute after gridding
    pip_window = int(rules.get("pip_rising", {}).get("window_min", 10))
    if "PIP" in out.columns:
        out["PIP_slope"] = (
            out.groupby("PID", group_keys=False)["PIP"]
            .apply(lambda s: s.rolling(window=pip_window, min_periods=5).apply(_linreg_slope, raw=True))
        )
    else:
        out["PIP_slope"] = np.nan

    # Agent Et rolling median for drift detection
    drift_window = int(rules.get("agent_drift", {}).get("window_min", 15))
    if "Agent_Et" in out.columns:
        out["Agent_Et_rollmed"] = (
            out.groupby("PID", group_keys=False)["Agent_Et"]
            .apply(lambda s: s.rolling(window=drift_window, min_periods=5).median())
        )
        out["Agent_Et_drift"] = (out["Agent_Et"] - out["Agent_Et_rollmed"]).abs()
    else:
        out["Agent_Et_rollmed"] = np.nan
        out["Agent_Et_drift"] = np.nan

    # Primary agent per case (modal non-N agent, else N)
    if "Agent" in out.columns:
        def _primary(s: pd.Series) -> str:
            s = s.dropna()
            non_n = s[s != "N"]
            if not non_n.empty:
                return non_n.mode().iloc[0]
            if not s.empty:
                return s.mode().iloc[0]
            return "N"

        primary = out.groupby("PID")["Agent"].transform(_primary)
        out["primary_agent"] = primary
        out["primary_agent_name"] = out["primary_agent"].map(
            {"S": "sevoflurane", "D": "desflurane", "I": "isoflurane", "N": "none"}
        )

    return out


def _linreg_slope(y: np.ndarray) -> float:
    """Slope of y vs 0..n-1; NaN if insufficient finite points."""
    mask = np.isfinite(y)
    if mask.sum() < 5:
        return np.nan
    x = np.arange(len(y), dtype=float)[mask]
    yy = y[mask]
    x = x - x.mean()
    denom = np.dot(x, x)
    if denom == 0:
        return np.nan
    return float(np.dot(x, yy - yy.mean()) / denom)


def build_case_summary(ts: pd.DataFrame, flags: pd.DataFrame | None = None) -> pd.DataFrame:
    """One row per PID with case-level aggregates (flags optional)."""
    if ts.empty:
        return pd.DataFrame()

    # Grid rows with no signal are gaps, not observed minutes
    signals = [c for c in OBSERVED_SIGNAL_COLS if c in ts.columns]
    observed = ts[signals].notna().any(axis=1) if signals else pd.Series(True, index=ts.index)
    work = ts.assign(_observed=observed)

    optional = {
        "Age": ("Age", "first"),
        "Gender": ("Gender", "first"),
        "Procedure_short": ("Procedure_short", "first"),
        "primary_agent": ("primary_agent", "first"),
        "primary_agent_name": ("primary_agent_name", "first"),
        "median_TV": ("TV", "median"),
        "median_PIP": ("PIP", "median"),
        "median_PEEP": ("PEEP", "median"),
        "median_ETCO2": ("ETCO2", "median"),
        "median_Agent_Et": ("Agent_Et", "median"),
        "case_duration_min": ("case_duration_min", "first"),
    }
    aggs = {
        "n_minutes": ("_observed", "sum"),
        "t_start": ("Obs_time", "min"),
        "t_end": ("Obs_time", "max"),
    }
    aggs.update({name: spec for name, spec in optional.items() if spec[0] in ts.columns})
    summary = work.groupby("PID").agg(**aggs).reset_index()
    summary["n_minutes"] = summary["n_minutes"].astype(int)

    # Recompute duration from span if needed
    if "t_start" in summary.columns and "t_end" in summary.columns:
        span = (summary["t_end"] - summary["t_start"]).dt.total_seconds() / 60.0
        if "case_duration_min" not in summary.columns:
            summary["case_duration_min"] = span
        else:
            summary["case_duration_min"] = summary["case_duration_min"].fillna(span)

    if flags is not None and not flags.empty:
        fc = flags.groupby(["PID", "severity"]).size().unstack(fill_value=0)
        for sev in ["info", "warn", "critical"]:
            if sev not in fc.columns:
                fc[sev] = 0
        fc = fc.rename(columns={
            "info": "n_info",
            "warn": "n_warn",
            "critical": "n_critical",
        }).reset_index()
        summary = summary.merge(fc, on="PID", how="left")
        for c in ["n_info", "n_warn", "n_critical"]:
            summary[c] = summary[c].fillna(0).astype(int)

        # Episode counts for composites
        composites = flags[flags["rule_id"].isin([
            "compliance_concern", "hypoventilation_pattern", "desat_with_vent_issue"
        ])]
        if not composites.empty:
            ep = composites.groupby("PID").size().rename("n_composite_rows")
            summary = summary.merge(ep, on="PID", how="left")
            summary["n_composite_rows"] = summary["n_composite_rows"].fillna(0).astype(int)
        else:
            summary["n_composite_rows"] = 0

        # Distinct rule types
        n_rules = flags.groupby("PID")["rule_id"].nunique().rename("n_rule_types")
        summary = summary.merge(n_rules, on="PID", how="left")
        summary["n_rule_types"] = summary["n_rule_types"].fillna(0).astype(int)
    else:
        summary["n_info"] = 0
        summary["n_warn"] = 0
        summary["n_critical"] = 0
        summary["n_composite_rows"] = 0
        summary["n_rule_types"] = 0

    return summary
