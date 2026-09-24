"""
Aggregate-only profile of processed pipeline output, for threshold calibration.

Reads ``timeseries.parquet``, ``flags.parquet`` and ``cases.parquet`` from a
processed directory and reports:

- coverage: cases, observed vs gap minutes, gap lengths
- per-signal distributions (percentiles over observed minutes)
- per-rule firing rates, and where each warn/critical threshold falls in the
  observed distribution of its signal

No PIDs, timestamps or row-level values are written. Case counts between 1
and ``min_cell - 1`` are shown as ``<min_cell``, and signal distributions
drawn from fewer than ``min_cell`` cases are suppressed. Check your MOVER data
use agreement before sharing even aggregate output.

    PYTHONPATH=. python -m src.pipeline.profile --processed-dir data/processed --out profile.md
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.config import load_thresholds
from src.pipeline.features import OBSERVED_SIGNAL_COLS

PERCENTILES = (1, 5, 25, 50, 75, 95, 99)

PROFILE_SIGNALS = [
    ("TV", "mL"), ("TV_mlkg", "mL/kg IBW"), ("RR", "/min"), ("PIP", "cmH2O"),
    ("PEEP", "cmH2O"), ("ETCO2", "mmHg"), ("FIO2", "%"), ("Agent_Et", "vol %"),
    ("MAC_total_Et", "MAC"), ("ETN2O", "%"), ("SPO2", "%"), ("HR", "/min"),
    ("nMAP", "mmHg"), ("PIP_slope", "cmH2O/min"),
]

# Gaps are measured per stream: vitals every minute would hide ventilator gaps.
# NIBP is left out on purpose; it is sampled every few minutes by design.
STREAMS = {
    "vent": ["TV", "RR", "PEEP", "PIP", "ETCO2", "FIO2", "Agent_Et"],
    "vitals": ["HR", "HRe", "HRp", "SPO2"],
}

# rule -> (signal, higher_is_worse, warn key, critical key)
THRESHOLD_RULES = {
    "pip_high": ("PIP", True, "warn", "critical"),
    "peep_high": ("PEEP", True, "warn", "critical"),
    "tv_low_mlkg": ("TV_mlkg", False, "warn", "critical"),
    "tv_high_mlkg": ("TV_mlkg", True, "warn", "critical"),
    "rr_low": ("RR", False, "warn", "critical"),
    "rr_high": ("RR", True, "warn", "critical"),
    "etco2_high": ("ETCO2", True, "warn", "critical"),
    "etco2_low": ("ETCO2", False, "warn", "critical"),
    "spo2_low": ("SPO2", False, "warn", "critical"),
    "hr_high": ("HR", True, "warn", "critical"),
    "hr_low": ("HR", False, "warn", "critical"),
    "map_low": ("nMAP", False, "warn", "critical"),
    "agent_high": ("MAC_total_Et", True, "warn_mac", "critical_mac"),
}


def _case_count(n: int, min_cell: int) -> int | str:
    return f"<{min_cell}" if 0 < n < min_cell else int(n)


def _gap_runs(observed: pd.Series) -> list[int]:
    """Lengths of runs of unobserved minutes between a stream's first and last sample."""
    runs: list[int] = []
    current = 0
    seen = False
    for ok in observed.to_numpy():
        if ok:
            if current and seen:
                runs.append(current)
            current = 0
            seen = True
        else:
            current += 1
    return runs  # leading and trailing runs are outside the stream's span


def _stream_gaps(ts: pd.DataFrame, stream: str, cols: list[str], min_cell: int) -> dict:
    present = [c for c in cols if c in ts.columns]
    if not present:
        return {}
    has = ts[present].notna().any(axis=1)
    runs: list[int] = []
    long_gap_cases = 0
    span_minutes = 0
    for _, idx in ts.groupby("PID", sort=False).groups.items():
        case_runs = _gap_runs(has.loc[idx])
        runs.extend(case_runs)
        long_gap_cases += any(r >= 10 for r in case_runs)
        obs_idx = has.loc[idx]
        if obs_idx.any():
            pos = np.flatnonzero(obs_idx.to_numpy())
            span_minutes += int(pos[-1] - pos[0] + 1)
    gap_total = sum(runs)
    return {
        f"{stream}_gap_minutes_pct": round(100 * gap_total / span_minutes, 2) if span_minutes else 0.0,
        f"{stream}_gap_runs": len(runs),
        f"{stream}_gap_run_p50_min": round(float(np.median(runs)), 1) if runs else 0.0,
        f"{stream}_gap_run_p90_min": round(float(np.percentile(runs, 90)), 1) if runs else 0.0,
        f"{stream}_gap_run_max_min": int(max(runs)) if runs else 0,
        f"{stream}_cases_with_gap_ge_10_min": _case_count(long_gap_cases, min_cell),
    }


def _pctl(values: pd.Series) -> dict[str, float]:
    arr = values.to_numpy(dtype=float)
    return {f"p{p}": round(float(np.percentile(arr, p)), 2) for p in PERCENTILES}


def profile_processed(
    processed_dir: Path | str,
    thresholds: dict | None = None,
    min_cell: int = 11,
) -> dict[str, Any]:
    """Build the aggregate profile dict for a processed directory."""
    root = Path(processed_dir)
    ts = pd.read_parquet(root / "timeseries.parquet")
    flags_path = root / "flags.parquet"
    flags = pd.read_parquet(flags_path) if flags_path.exists() else pd.DataFrame()
    if thresholds is None:
        thresholds = load_thresholds()
    rules = thresholds.get("rules", {})

    signals = [c for c in OBSERVED_SIGNAL_COLS if c in ts.columns]
    observed = ts[signals].notna().any(axis=1)
    ts = ts.sort_values(["PID", "Obs_time"]).assign(_observed=observed)
    n_cases = int(ts["PID"].nunique())
    observed_minutes = int(observed.sum())

    coverage: dict[str, Any] = {
        "n_cases": n_cases,
        "grid_minutes": int(len(ts)),
        "observed_minutes": observed_minutes,
    }
    for stream, cols in STREAMS.items():
        coverage.update(_stream_gaps(ts, stream, cols, min_cell))

    signal_stats: dict[str, Any] = {}
    obs = ts[ts["_observed"]]
    for col, unit in PROFILE_SIGNALS:
        if col not in obs.columns:
            continue
        vals = pd.to_numeric(obs[col], errors="coerce")
        present = vals.notna()
        cases_with = int(obs.loc[present, "PID"].nunique())
        entry: dict[str, Any] = {
            "unit": unit,
            "n_minutes": int(present.sum()),
            "n_cases": _case_count(cases_with, min_cell),
            "missing_pct": round(100 * (1 - present.mean()), 2) if len(vals) else 100.0,
        }
        if cases_with >= min_cell:
            entry.update(_pctl(vals[present]))
        else:
            entry["suppressed"] = True
        signal_stats[col] = entry

    obs_hours = observed_minutes / 60.0
    rule_stats: dict[str, Any] = {}
    for rule_id, spec in rules.items():
        f = flags[flags["rule_id"] == rule_id] if not flags.empty else flags
        n_minutes = int(f[["PID", "Obs_time"]].drop_duplicates().shape[0]) if len(f) else 0
        n_flagged = int(f["PID"].nunique()) if len(f) else 0
        entry = {
            "flagged_minutes": n_minutes,
            "cases_flagged": _case_count(n_flagged, min_cell),
            "cases_flagged_pct": (
                round(100 * n_flagged / n_cases, 1)
                if n_cases and not 0 < n_flagged < min_cell
                else None
            ),
            "flagged_minutes_per_hour": round(n_minutes / obs_hours, 3) if obs_hours else None,
        }
        if rule_id in THRESHOLD_RULES and rule_id in rules:
            col, higher, wkey, ckey = THRESHOLD_RULES[rule_id]
            if col in obs.columns:
                vals = pd.to_numeric(obs[col], errors="coerce").dropna()
                if rule_id == "etco2_low":
                    vals = vals[vals > 0]  # the rule excludes zeros
                warn, crit = spec.get(wkey), spec.get(ckey)
                if len(vals) and warn is not None and crit is not None:
                    beyond = (lambda v: vals >= v) if higher else (lambda v: vals <= v)
                    entry.update({
                        "signal": col,
                        "direction": "high" if higher else "low",
                        "warn": warn,
                        "critical": crit,
                        "pct_minutes_beyond_warn": round(100 * beyond(warn).mean(), 3),
                        "pct_minutes_beyond_critical": round(100 * beyond(crit).mean(), 3),
                    })
        rule_stats[rule_id] = entry

    return {
        "min_cell": min_cell,
        "coverage": coverage,
        "signals": signal_stats,
        "rules": rule_stats,
    }


def render_markdown(profile: dict[str, Any]) -> str:
    """Render a profile dict as a Markdown report."""
    c = profile["coverage"]
    lines = [
        "# MOVER SIS pipeline profile (aggregate only)",
        "",
        f"No PIDs or timestamps. Case counts under {profile['min_cell']} are shown "
        f"as <{profile['min_cell']}; distributions from fewer cases are suppressed.",
        "",
        "## Coverage",
        "",
        "| Measure | Value |",
        "|---|---|",
    ]
    lines += [f"| {k} | {v} |" for k, v in c.items()]

    pcols = [f"p{p}" for p in PERCENTILES]
    lines += [
        "",
        "## Signals (observed minutes)",
        "",
        "| Signal | Unit | Minutes | Cases | Missing % | " + " | ".join(pcols) + " |",
        "|---|---|---|---|---|" + "---|" * len(pcols),
    ]
    for col, s in profile["signals"].items():
        pct = ["suppressed"] * len(pcols) if s.get("suppressed") else [str(s[p]) for p in pcols]
        lines.append(
            f"| {col} | {s['unit']} | {s['n_minutes']} | {s['n_cases']} | "
            f"{s['missing_pct']} | " + " | ".join(pct) + " |"
        )

    lines += [
        "",
        "## Rules",
        "",
        "Threshold columns show the share of observed minutes past each threshold "
        "(before duration or ventilation gating), next to how often the rule fired.",
        "",
        "| Rule | Cases flagged | % cases | Flagged min | Per hour | Signal | Warn | "
        "% min ≥ warn | Critical | % min ≥ critical |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for rule_id, r in profile["rules"].items():
        pct_cases = "" if r["cases_flagged_pct"] is None else r["cases_flagged_pct"]
        lines.append(
            f"| {rule_id} | {r['cases_flagged']} | {pct_cases} | {r['flagged_minutes']} | "
            f"{r['flagged_minutes_per_hour']} | {r.get('signal', '')} | {r.get('warn', '')} | "
            f"{r.get('pct_minutes_beyond_warn', '')} | {r.get('critical', '')} | "
            f"{r.get('pct_minutes_beyond_critical', '')} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Aggregate-only profile of processed MOVER SIS output"
    )
    parser.add_argument("--processed-dir", type=str, default=None)
    parser.add_argument(
        "--preset", type=str, default="default", choices=["default", "strict", "lenient"]
    )
    parser.add_argument("--min-cell", type=int, default=11)
    parser.add_argument("--out", type=str, default=None, help="Markdown path (default: stdout)")
    parser.add_argument("--json", type=str, default=None, help="Also write the profile as JSON")
    args = parser.parse_args(argv)

    if args.processed_dir is None:
        from src.runtime_paths import processed_dir

        root = processed_dir()
    else:
        root = Path(args.processed_dir)
    profile = profile_processed(root, load_thresholds(args.preset), min_cell=args.min_cell)
    md = render_markdown(profile)
    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
        print(f"[profile] wrote {args.out}")
    else:
        print(md)
    if args.json:
        Path(args.json).write_text(json.dumps(profile, indent=2), encoding="utf-8")
        print(f"[profile] wrote {args.json}")


if __name__ == "__main__":
    main()
