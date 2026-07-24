"""Rule-based anomaly flags for ventilation and anesthesia depth."""

from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd

from src.config import load_thresholds

COMPOSITE_RULES = {
    "compliance_concern",
    "hypoventilation_pattern",
    "desat_with_vent_issue",
}


def _sev_threshold(value: float, warn: float, critical: float, higher_is_worse: bool = True) -> str | None:
    if pd.isna(value):
        return None
    if higher_is_worse:
        if value >= critical:
            return "critical"
        if value >= warn:
            return "warn"
    else:
        if value <= critical:
            return "critical"
        if value <= warn:
            return "warn"
    return None


def _episodes(mask: pd.Series, min_len: int = 1) -> pd.Series:
    """Return boolean series True only for points inside runs of length >= min_len."""
    if mask.empty:
        return mask
    m = mask.fillna(False).astype(bool)
    # group consecutive True runs
    run_id = (m != m.shift(fill_value=False)).cumsum()
    run_id = run_id.where(m, 0)
    sizes = m.groupby(run_id).transform("sum")
    return m & (sizes >= min_len)


def _append_flags(
    rows: list[dict],
    df: pd.DataFrame,
    mask: pd.Series,
    rule_id: str,
    severity: str | pd.Series,
    message: str,
    value_col: str | None = None,
) -> None:
    idx = df.index[mask.fillna(False)]
    if len(idx) == 0:
        return
    sub = df.loc[idx]
    if isinstance(severity, str):
        sevs = [severity] * len(sub)
    else:
        sevs = severity.loc[idx].tolist()
    values = sub[value_col].tolist() if value_col and value_col in sub.columns else [np.nan] * len(sub)
    for i, (_, r) in enumerate(sub.iterrows()):
        rows.append({
            "PID": r["PID"],
            "Obs_time": r["Obs_time"],
            "t_min": r.get("t_min", np.nan),
            "rule_id": rule_id,
            "severity": sevs[i],
            "value": values[i],
            "message": message,
        })


def flag_anomalies(ts: pd.DataFrame, thresholds: dict | None = None) -> pd.DataFrame:
    """
    Apply rule-based flags to a minute-aligned timeseries.

    Returns long-form DataFrame: PID, Obs_time, t_min, rule_id, severity, value, message.
    """
    if thresholds is None:
        thresholds = load_thresholds()
    rules = thresholds.get("rules", {})
    rows: list[dict] = []

    if ts.empty:
        return pd.DataFrame(columns=[
            "PID", "Obs_time", "t_min", "rule_id", "severity", "value", "message"
        ])

    df = ts.sort_values(["PID", "Obs_time"]).reset_index(drop=True)

    # ---- Pointwise threshold rules ----
    r = rules.get("pip_high", {})
    if "PIP" in df.columns and r:
        sevs = df["PIP"].map(
            lambda v: _sev_threshold(v, r.get("warn", 30), r.get("critical", 35), True)
        )
        mask = sevs.notna()
        _append_flags(rows, df, mask, "pip_high", sevs, r.get("description", "High PIP"), "PIP")

    r = rules.get("peep_high", {})
    if "PEEP" in df.columns and r:
        sevs = df["PEEP"].map(
            lambda v: _sev_threshold(v, r.get("warn", 12), r.get("critical", 15), True)
        )
        mask = sevs.notna()
        _append_flags(rows, df, mask, "peep_high", sevs, r.get("description", "High PEEP"), "PEEP")

    r = rules.get("tv_low_mlkg", {})
    if "TV_mlkg" in df.columns and r:
        sevs = df["TV_mlkg"].map(
            lambda v: _sev_threshold(v, r.get("warn", 4), r.get("critical", 3), False)
        )
        mask = sevs.notna() & df.get("likely_mech_vent", True)
        _append_flags(rows, df, mask, "tv_low_mlkg", sevs, r.get("description", "Low TV mL/kg"), "TV_mlkg")

    r = rules.get("tv_high_mlkg", {})
    if "TV_mlkg" in df.columns and r:
        sevs = df["TV_mlkg"].map(
            lambda v: _sev_threshold(v, r.get("warn", 10), r.get("critical", 12), True)
        )
        mask = sevs.notna()
        _append_flags(rows, df, mask, "tv_high_mlkg", sevs, r.get("description", "High TV mL/kg"), "TV_mlkg")

    r = rules.get("tv_abs_extreme", {})
    if "TV" in df.columns and r:
        adult = df["Age"].fillna(40) >= r.get("adult_age_min", 16) if "Age" in df.columns else True
        low, high = r.get("low", 100), r.get("high", 1000)
        mask = adult & df["TV"].notna() & ((df["TV"] < low) | (df["TV"] > high))
        _append_flags(rows, df, mask, "tv_abs_extreme", "warn", r.get("description", "Extreme TV"), "TV")

    r = rules.get("rr_low", {})
    if "RR" in df.columns and r:
        sevs = df["RR"].map(
            lambda v: _sev_threshold(v, r.get("warn", 6), r.get("critical", 4), False)
        )
        mask = sevs.notna() & df.get("likely_mech_vent", True)
        _append_flags(rows, df, mask, "rr_low", sevs, r.get("description", "Low RR"), "RR")

    r = rules.get("rr_high", {})
    if "RR" in df.columns and r:
        sevs = df["RR"].map(
            lambda v: _sev_threshold(v, r.get("warn", 20), r.get("critical", 30), True)
        )
        mask = sevs.notna()
        _append_flags(rows, df, mask, "rr_high", sevs, r.get("description", "High RR"), "RR")

    r = rules.get("etco2_high", {})
    if "ETCO2" in df.columns and r:
        sevs = df["ETCO2"].map(
            lambda v: _sev_threshold(v, r.get("warn", 50), r.get("critical", 60), True)
        )
        mask = sevs.notna()
        _append_flags(rows, df, mask, "etco2_high", sevs, r.get("description", "High ETCO2"), "ETCO2")

    r = rules.get("etco2_low", {})
    if "ETCO2" in df.columns and r:
        # exclude zeros
        sevs = df["ETCO2"].map(
            lambda v: _sev_threshold(v, r.get("warn", 28), r.get("critical", 22), False)
            if pd.notna(v) and v > 0 else None
        )
        mask = sevs.notna()
        _append_flags(rows, df, mask, "etco2_low", sevs, r.get("description", "Low ETCO2"), "ETCO2")

    r = rules.get("spo2_low", {})
    if "SPO2" in df.columns and r:
        sevs = df["SPO2"].map(
            lambda v: _sev_threshold(v, r.get("warn", 92), r.get("critical", 88), False)
        )
        mask = sevs.notna()
        _append_flags(rows, df, mask, "spo2_low", sevs, r.get("description", "Low SpO2"), "SPO2")

    r = rules.get("hr_high", {})
    if "HR" in df.columns and r:
        sevs = df["HR"].map(
            lambda v: _sev_threshold(v, r.get("warn", 120), r.get("critical", 140), True)
        )
        mask = sevs.notna()
        _append_flags(rows, df, mask, "hr_high", sevs, r.get("description", "High HR"), "HR")

    r = rules.get("hr_low", {})
    if "HR" in df.columns and r:
        sevs = df["HR"].map(
            lambda v: _sev_threshold(v, r.get("warn", 45), r.get("critical", 40), False)
        )
        mask = sevs.notna()
        _append_flags(rows, df, mask, "hr_low", sevs, r.get("description", "Low HR"), "HR")

    r = rules.get("map_low", {})
    if "nMAP" in df.columns and r:
        sevs = df["nMAP"].map(
            lambda v: _sev_threshold(v, r.get("warn", 60), r.get("critical", 50), False)
        )
        mask = sevs.notna()
        _append_flags(rows, df, mask, "map_low", sevs, r.get("description", "Low MAP"), "nMAP")

    r = rules.get("agent_high", {})
    if "MAC_Et" in df.columns and r:
        sevs = df["MAC_Et"].map(
            lambda v: _sev_threshold(v, r.get("warn_mac", 1.5), r.get("critical_mac", 2.0), True)
        )
        mask = sevs.notna()
        _append_flags(rows, df, mask, "agent_high", sevs, r.get("description", "High MAC"), "MAC_Et")

    # ---- Duration / slope rules (per PID) ----
    for pid, g in df.groupby("PID", sort=False):
        idx = g.index
        g = g.copy()

        # pip_rising
        r = rules.get("pip_rising", {})
        if "PIP_slope" in g.columns and r:
            min_slope = r.get("min_slope_cmh2o_per_min", 0.5)
            min_pip = r.get("min_pip", 20)
            min_dur = int(r.get("min_duration_min", 5))
            raw = (g["PIP_slope"] >= min_slope) & (g["PIP"] >= min_pip)
            mask = _episodes(raw, min_dur)
            full_mask = pd.Series(False, index=df.index)
            full_mask.loc[idx] = mask.values
            _append_flags(
                rows, df, full_mask, "pip_rising", "warn",
                r.get("description", "Rising PIP"), "PIP_slope",
            )

        # peep_zero_long
        r = rules.get("peep_zero_long", {})
        if "PEEP" in g.columns and r:
            min_dur = int(r.get("min_duration_min", 15))
            raw = (
                (g["PEEP"] <= r.get("max_peep", 0))
                & (g.get("TV", pd.Series(np.nan, index=g.index)) >= r.get("min_tv", 200))
                & (g.get("RR", pd.Series(np.nan, index=g.index)) >= r.get("min_rr", 6))
            )
            mask = _episodes(raw, min_dur)
            full_mask = pd.Series(False, index=df.index)
            full_mask.loc[idx] = mask.values
            _append_flags(
                rows, df, full_mask, "peep_zero_long", "info",
                r.get("description", "Prolonged ZEEP"), "PEEP",
            )

        # etco2_zero_vent
        r = rules.get("etco2_zero_vent", {})
        if "ETCO2" in g.columns and r:
            min_dur = int(r.get("min_duration_min", 3))
            raw = (
                (g["ETCO2"] == 0)
                & (g.get("TV", pd.Series(np.nan, index=g.index)) >= r.get("min_tv", 200))
                & (g.get("RR", pd.Series(np.nan, index=g.index)) >= r.get("min_rr", 8))
            )
            mask = _episodes(raw, min_dur)
            full_mask = pd.Series(False, index=df.index)
            full_mask.loc[idx] = mask.values
            _append_flags(
                rows, df, full_mask, "etco2_zero_vent", "critical",
                r.get("description", "Zero ETCO2 while ventilated"), "ETCO2",
            )

        # fio2_high_long
        r = rules.get("fio2_high_long", {})
        if "FIO2" in g.columns and "t_min" in g.columns and r:
            min_dur = int(r.get("min_duration_min", 30))
            skip = r.get("skip_first_min", 15)
            raw = (g["FIO2"] >= r.get("fio2_min", 90)) & (g["t_min"] >= skip)
            mask = _episodes(raw, min_dur)
            full_mask = pd.Series(False, index=df.index)
            full_mask.loc[idx] = mask.values
            _append_flags(
                rows, df, full_mask, "fio2_high_long", "info",
                r.get("description", "Prolonged high FiO2"), "FIO2",
            )

        # fio2_room_air_vent
        r = rules.get("fio2_room_air_vent", {})
        if "FIO2" in g.columns and r:
            min_dur = int(r.get("min_duration_min", 5))
            raw = (
                (g["FIO2"] <= r.get("fio2_max", 25))
                & (g.get("TV", pd.Series(np.nan, index=g.index)) >= r.get("min_tv", 200))
                & (g.get("RR", pd.Series(np.nan, index=g.index)) >= r.get("min_rr", 6))
            )
            mask = _episodes(raw, min_dur)
            full_mask = pd.Series(False, index=df.index)
            full_mask.loc[idx] = mask.values
            _append_flags(
                rows, df, full_mask, "fio2_room_air_vent", "warn",
                r.get("description", "Room-air FiO2 on vent"), "FIO2",
            )

        # agent_low_maint
        r = rules.get("agent_low_maint", {})
        if "MAC_Et" in g.columns and r:
            frac_lo = r.get("case_frac_start", 0.2)
            frac_hi = r.get("case_frac_end", 0.8)
            n2o_ex = r.get("n2o_et_exempt", 30)
            etn2o = g["ETN2O"] if "ETN2O" in g.columns else pd.Series(0, index=g.index)
            mid = g["case_frac"].between(frac_lo, frac_hi) if "case_frac" in g.columns else True
            raw = (
                mid
                & g["Agent"].notna()
                & (g["Agent"] != "N")
                & (g["MAC_Et"] < r.get("max_mac", 0.3))
                & (etn2o.fillna(0) < n2o_ex)
            )
            full_mask = pd.Series(False, index=df.index)
            full_mask.loc[idx] = raw.fillna(False).values
            _append_flags(
                rows, df, full_mask, "agent_low_maint", "warn",
                r.get("description", "Low agent mid-case"), "MAC_Et",
            )

        # agent_drift
        r = rules.get("agent_drift", {})
        if "Agent_Et_drift" in g.columns and r:
            min_dur = int(r.get("min_duration_min", 5))
            deltas = r.get("delta_vol_pct", {"S": 0.5, "I": 0.5, "D": 1.5, "N": 0.5})
            thr = g["Agent"].map(lambda a: deltas.get(a, 0.5) if pd.notna(a) else np.nan)
            raw = g["Agent_Et_drift"] >= thr
            mask = _episodes(raw, min_dur)
            full_mask = pd.Series(False, index=df.index)
            full_mask.loc[idx] = mask.values
            _append_flags(
                rows, df, full_mask, "agent_drift", "info",
                r.get("description", "Agent drift"), "Agent_Et_drift",
            )

        # agent_fi_et_gap
        r = rules.get("agent_fi_et_gap", {})
        if "Agent_Fi_Et_gap" in g.columns and r:
            min_dur = int(r.get("min_duration_min", 10))
            frac_lo = r.get("case_frac_start", 0.15)
            frac_hi = r.get("case_frac_end", 0.85)
            gaps = r.get("gap_vol_pct", {"S": 1.0, "I": 1.0, "D": 3.0})
            thr = g["Agent"].map(lambda a: gaps.get(a, np.nan) if pd.notna(a) else np.nan)
            mid = g["case_frac"].between(frac_lo, frac_hi) if "case_frac" in g.columns else True
            raw = mid & (g["Agent_Fi_Et_gap"] >= thr)
            mask = _episodes(raw, min_dur)
            full_mask = pd.Series(False, index=df.index)
            full_mask.loc[idx] = mask.values
            _append_flags(
                rows, df, full_mask, "agent_fi_et_gap", "info",
                r.get("description", "Fi-Et agent gap"), "Agent_Fi_Et_gap",
            )

    flags = pd.DataFrame(rows)
    if flags.empty:
        flags = pd.DataFrame(columns=[
            "PID", "Obs_time", "t_min", "rule_id", "severity", "value", "message"
        ])
        return flags

    # ---- Composite patterns (vectorized on full df using flag presence) ----
    flags = _add_composites(df, flags, rules)
    flags = flags.sort_values(["PID", "Obs_time", "rule_id"]).reset_index(drop=True)
    return flags


def _add_composites(df: pd.DataFrame, flags: pd.DataFrame, rules: dict) -> pd.DataFrame:
    if flags.empty:
        return flags

    def has_rule(rule: str) -> pd.Series:
        hit = flags.loc[flags["rule_id"] == rule, ["PID", "Obs_time"]].drop_duplicates()
        if hit.empty:
            return pd.Series(False, index=df.index)
        key = set(zip(hit["PID"], hit["Obs_time"]))
        return pd.Series(
            [(p, t) in key for p, t in zip(df["PID"], df["Obs_time"])],
            index=df.index,
        )

    extra: list[dict] = []

    # compliance_concern: pip_rising + TV not rising
    if "TV" in df.columns:
        tv_slope = (
            df.groupby("PID", group_keys=False)["TV"]
            .apply(lambda s: s.rolling(10, min_periods=5).apply(
                lambda y: np.nan if np.isfinite(y).sum() < 5 else np.polyfit(
                    np.arange(len(y))[np.isfinite(y)],
                    y[np.isfinite(y)],
                    1,
                )[0],
                raw=True,
            ))
        )
        mask = has_rule("pip_rising") & (tv_slope.fillna(0) <= 5)
        for _, r in df.loc[mask].iterrows():
            extra.append({
                "PID": r["PID"], "Obs_time": r["Obs_time"], "t_min": r.get("t_min", np.nan),
                "rule_id": "compliance_concern", "severity": "warn",
                "value": r.get("PIP_slope", np.nan),
                "message": rules.get("compliance_concern", {}).get(
                    "description", "Rising PIP with stable/falling TV"
                ),
            })

    # hypoventilation_pattern
    mask = has_rule("etco2_high") & (has_rule("tv_low_mlkg") | has_rule("rr_low"))
    for _, r in df.loc[mask].iterrows():
        extra.append({
            "PID": r["PID"], "Obs_time": r["Obs_time"], "t_min": r.get("t_min", np.nan),
            "rule_id": "hypoventilation_pattern", "severity": "warn",
            "value": r.get("ETCO2", np.nan),
            "message": rules.get("hypoventilation_pattern", {}).get(
                "description", "High ETCO2 with low TV or RR"
            ),
        })

    # desat_with_vent_issue
    vent_issue = (
        has_rule("pip_high") | has_rule("etco2_zero_vent") | has_rule("tv_low_mlkg")
    )
    mask = has_rule("spo2_low") & vent_issue
    for _, r in df.loc[mask].iterrows():
        extra.append({
            "PID": r["PID"], "Obs_time": r["Obs_time"], "t_min": r.get("t_min", np.nan),
            "rule_id": "desat_with_vent_issue", "severity": "critical",
            "value": r.get("SPO2", np.nan),
            "message": rules.get("desat_with_vent_issue", {}).get(
                "description", "Desaturation with ventilation anomaly"
            ),
        })

    if not extra:
        return flags
    return pd.concat([flags, pd.DataFrame(extra)], ignore_index=True)


def score_cases(flags: pd.DataFrame, thresholds: dict | None = None) -> pd.DataFrame:
    """Per-PID anomaly score and rule tallies."""
    if thresholds is None:
        thresholds = load_thresholds()
    sc = thresholds.get("scoring", {})
    w_warn = sc.get("warn_minute_weight", 1)
    w_crit = sc.get("critical_minute_weight", 3)
    w_comp = sc.get("composite_episode_weight", 5)

    empty = pd.DataFrame(columns=[
        "PID", "n_info", "n_warn", "n_critical", "n_composite",
        "anomaly_score", "top_rules",
    ])
    if flags is None or flags.empty:
        return empty

    f = flags.copy()
    f["PID"] = f["PID"].astype(str)
    f["minute_key"] = f["PID"] + "|" + f["Obs_time"].astype(str)

    def _count_sev(sev: str) -> pd.Series:
        sub = f[f["severity"] == sev]
        if sub.empty:
            return pd.Series(dtype="int64", name=sev)
        return sub.groupby("PID")["minute_key"].nunique()

    n_info = _count_sev("info").rename("n_info")
    n_warn = _count_sev("warn").rename("n_warn")
    n_crit = _count_sev("critical").rename("n_critical")

    comp = f[f["rule_id"].isin(COMPOSITE_RULES)]
    if comp.empty:
        n_comp = pd.Series(dtype="int64", name="n_composite")
    else:
        n_comp = comp.groupby("PID")["minute_key"].nunique().rename("n_composite")

    # Top rules string (avoid groupby.apply version pitfalls)
    top = (
        f.groupby(["PID", "rule_id"]).size()
        .reset_index(name="n")
        .sort_values(["PID", "n"], ascending=[True, False])
    )
    top_map: dict[str, str] = {}
    for pid, g in top.groupby("PID", sort=False):
        parts = [f"{r}({n})" for r, n in zip(g["rule_id"].head(5), g["n"].head(5))]
        top_map[str(pid)] = ", ".join(parts)
    top_rules = pd.Series(top_map, name="top_rules")

    out = pd.DataFrame({"PID": sorted(f["PID"].unique())})
    out = out.merge(n_info.rename("n_info"), left_on="PID", right_index=True, how="left")
    out = out.merge(n_warn.rename("n_warn"), left_on="PID", right_index=True, how="left")
    out = out.merge(n_crit.rename("n_critical"), left_on="PID", right_index=True, how="left")
    out = out.merge(n_comp.rename("n_composite"), left_on="PID", right_index=True, how="left")
    out = out.merge(top_rules.rename("top_rules"), left_on="PID", right_index=True, how="left")

    for c in ["n_info", "n_warn", "n_critical", "n_composite"]:
        out[c] = out[c].fillna(0).astype(int)
    out["top_rules"] = out["top_rules"].fillna("")
    out["anomaly_score"] = (
        w_warn * out["n_warn"]
        + w_crit * out["n_critical"]
        + w_comp * out["n_composite"]
    ).astype(int)
    return out.sort_values("anomaly_score", ascending=False).reset_index(drop=True)


def collapse_episodes(flags: pd.DataFrame) -> pd.DataFrame:
    """Collapse contiguous same-rule flags into episodes with start/end."""
    if flags is None or flags.empty:
        return pd.DataFrame(columns=[
            "PID", "rule_id", "severity", "t_start_min", "t_end_min",
            "duration_min", "value_mean", "message",
        ])

    f = flags.sort_values(["PID", "rule_id", "Obs_time"]).copy()
    # 1-minute continuity
    f["gap"] = f.groupby(["PID", "rule_id"])["Obs_time"].diff().dt.total_seconds().div(60)
    f["new_ep"] = (f["gap"].isna()) | (f["gap"] > 1.5)
    f["episode"] = f.groupby(["PID", "rule_id"])["new_ep"].cumsum()

    agg = f.groupby(["PID", "rule_id", "episode"]).agg(
        severity=("severity", lambda s: "critical" if (s == "critical").any() else ("warn" if (s == "warn").any() else "info")),
        t_start_min=("t_min", "min"),
        t_end_min=("t_min", "max"),
        value_mean=("value", "mean"),
        message=("message", "first"),
        n_minutes=("Obs_time", "count"),
    ).reset_index()
    agg["duration_min"] = agg["t_end_min"] - agg["t_start_min"] + 1
    return agg.drop(columns=["episode"])
