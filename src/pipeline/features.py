"""Derived features for plotting and anomaly rules."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.config import load_thresholds


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
    """Add TV mL/kg, MAC fraction, PIP rolling slope, mechanical-vent hint."""
    if thresholds is None:
        thresholds = load_thresholds()

    out = ts.copy()

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

    # Mechanical ventilation heuristic
    tv = out["TV"] if "TV" in out.columns else pd.Series(np.nan, index=out.index)
    rr = out["RR"] if "RR" in out.columns else pd.Series(np.nan, index=out.index)
    out["likely_mech_vent"] = (tv >= 200) & (rr >= 6)

    # PIP rolling slope (cmH2O per minute) over 10-min window
    if "PIP" in out.columns:
        out["PIP_slope"] = (
            out.groupby("PID", group_keys=False)["PIP"]
            .apply(lambda s: s.rolling(window=10, min_periods=5).apply(_linreg_slope, raw=True))
        )
    else:
        out["PIP_slope"] = np.nan

    # Agent Et rolling median for drift detection
    if "Agent_Et" in out.columns:
        out["Agent_Et_rollmed"] = (
            out.groupby("PID", group_keys=False)["Agent_Et"]
            .apply(lambda s: s.rolling(window=15, min_periods=5).median())
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

    g = ts.groupby("PID")
    summary = g.agg(
        n_minutes=("Obs_time", "count"),
        t_start=("Obs_time", "min"),
        t_end=("Obs_time", "max"),
        Age=("Age", "first") if "Age" in ts.columns else ("Obs_time", "count"),
        Gender=("Gender", "first") if "Gender" in ts.columns else ("Obs_time", "count"),
        Procedure_short=("Procedure_short", "first") if "Procedure_short" in ts.columns else ("Obs_time", "count"),
        primary_agent=("primary_agent", "first") if "primary_agent" in ts.columns else ("Obs_time", "count"),
        primary_agent_name=("primary_agent_name", "first") if "primary_agent_name" in ts.columns else ("Obs_time", "count"),
        median_TV=("TV", "median") if "TV" in ts.columns else ("Obs_time", "count"),
        median_PIP=("PIP", "median") if "PIP" in ts.columns else ("Obs_time", "count"),
        median_PEEP=("PEEP", "median") if "PEEP" in ts.columns else ("Obs_time", "count"),
        median_ETCO2=("ETCO2", "median") if "ETCO2" in ts.columns else ("Obs_time", "count"),
        median_Agent_Et=("Agent_Et", "median") if "Agent_Et" in ts.columns else ("Obs_time", "count"),
        case_duration_min=("case_duration_min", "first") if "case_duration_min" in ts.columns else ("Obs_time", "count"),
    ).reset_index()

    # Clean up accidental agg when columns missing
    keep = [c for c in summary.columns if c == "PID" or not str(summary[c].dtype).startswith]
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
