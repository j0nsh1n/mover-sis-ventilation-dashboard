"""
Aggregate-only profile of processed pipeline output, for threshold calibration.

Reads ``timeseries.parquet``, ``flags.parquet`` and ``cases.parquet`` from a
processed directory and reports:

- coverage: cases, observed vs gap minutes, gap lengths
- per-signal distributions (percentiles over observed minutes)
- per-rule firing rates, and where each warn/critical threshold falls in the
  observed distribution of its signal
- minutes per anesthesia phase and how each case's phases were derived
- procedure event names with counts (from ``events.parquet``, written by the
  sample pipeline), marked with the phase role the configured patterns give
  them, to check the ``phases`` patterns in thresholds.yaml against the real names

No PIDs, timestamps or row-level values are written. Case counts between 1
and ``min_cell - 1`` are shown as ``<min_cell``, signal distributions
drawn from fewer than ``min_cell`` cases are suppressed, and event names used
in fewer than ``min_cell`` cases are pooled into one row. Check your MOVER data
use agreement before sharing even aggregate output.

    PYTHONPATH=. python -m src.pipeline.profile --processed-dir data/processed --out profile.md
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.config import load_thresholds
from src.pipeline.features import OBSERVED_SIGNAL_COLS
from src.pipeline.phases import PHASE_SOURCES, PHASES, classify_event_names
from src.rules import RuleCatalog

PERCENTILES = (1, 5, 25, 50, 75, 95, 99)

# Event names listed individually; the rest are pooled with the small-cell names
MAX_EVENT_NAMES = 100

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


def _case_count(n: int, min_cell: int) -> int | str:
    return f"<{min_cell}" if 0 < n < min_cell else int(n)


def _gap_runs(observed: Iterable[bool]) -> list[int]:
    """Lengths of runs of unobserved minutes between a stream's first and last sample."""
    runs: list[int] = []
    current = 0
    seen = False
    for ok in observed:
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
    gap_cases = 0
    span_minutes = 0
    has_array = has.to_numpy()
    for idx in ts.groupby("PID", sort=False).indices.values():
        obs_idx = has_array[idx]
        case_runs = _gap_runs(obs_idx)
        runs.extend(case_runs)
        gap_cases += bool(case_runs)
        long_gap_cases += any(r >= 10 for r in case_runs)
        if obs_idx.any():
            pos = np.flatnonzero(obs_idx)
            span_minutes += int(pos[-1] - pos[0] + 1)
    gap_total = sum(runs)
    result: dict[str, Any] = {
        f"{stream}_gap_minutes_pct": round(100 * gap_total / span_minutes, 2) if span_minutes else 0.0,
        f"{stream}_gap_runs": len(runs),
        f"{stream}_gap_run_p50_min": round(float(np.median(runs)), 1) if runs else 0.0,
        f"{stream}_gap_run_p90_min": round(float(np.percentile(runs, 90)), 1) if runs else 0.0,
        f"{stream}_gap_run_max_min": int(max(runs)) if runs else 0,
        f"{stream}_cases_with_gap_ge_10_min": _case_count(long_gap_cases, min_cell),
    }

    stream_cases = int(ts.loc[has, "PID"].nunique())
    if stream_cases < min_cell or 0 < gap_cases < min_cell:
        for key in result:
            if key != f"{stream}_cases_with_gap_ge_10_min":
                result[key] = None
    return result


def _pctl(values: pd.Series) -> dict[str, float]:
    arr = values.to_numpy(dtype=float)
    return {f"p{p}": round(float(np.percentile(arr, p)), 2) for p in PERCENTILES}


def _event_profile(events: pd.DataFrame, thresholds: dict, n_cases: int, min_cell: int) -> dict:
    """Event-name counts, suppressed like the rest of the report."""
    ev = events.dropna(subset=["Event_name"]).copy()
    ev["Event_name"] = ev["Event_name"].astype(str).str.strip()
    private = ev["Event_name"].str.contains(
        r"\b(?:SYN[0-9a-f]+|[0-9a-f]{16})\b|\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}:\d{2}(?::\d{2})?\b",
        case=False, regex=True,
    )
    ev.loc[private, "Event_name"] = "(identifier or timestamp omitted)"
    roles = classify_event_names(ev["Event_name"], thresholds.get("phases") or {})
    ev = ev.assign(_role=roles)
    per_name = (
        ev.groupby("Event_name")
        .agg(events=("PID", "size"), cases=("PID", "nunique"), role=("_role", "first"))
        .reset_index()
        .sort_values(["cases", "events", "Event_name"], ascending=[False, False, True])
    )
    shown = per_name[per_name["cases"] >= min_cell].head(MAX_EVENT_NAMES)
    pooled = per_name.drop(shown.index)
    names = [
        {
            "name": r["Event_name"],
            "events": int(r["events"]),
            "cases": int(r["cases"]),
            "role": r["role"],
        }
        for _, r in shown.iterrows()
    ]

    def cases_with(role: str) -> int | str:
        return _case_count(int(ev.loc[ev["_role"] == role, "PID"].nunique()), min_cell)

    return {
        "n_events": int(len(ev)),
        "n_event_names": int(len(per_name)),
        "cases_with_events": _case_count(int(ev["PID"].nunique()), min_cell),
        "cases_with_intubation_match": cases_with("intubation"),
        "cases_with_extubation_match": cases_with("extubation"),
        "names": names,
        "pooled_names": int(len(pooled)),
        "pooled_events": int(pooled["events"].sum()) if len(pooled) else 0,
        "n_cases": _case_count(n_cases, min_cell),
    }


def profile_processed(
    processed_dir: Path | str,
    thresholds: dict | None = None,
    min_cell: int = 11,
    *,
    seed: int | None = None,
) -> dict[str, Any]:
    """Build the aggregate profile dict for a processed directory."""
    if min_cell < 1:
        raise ValueError("min_cell must be at least 1")
    root = Path(processed_dir)
    meta_path = root / "run_meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    if seed is None:
        seed = meta.get("seed")
    ts = pd.read_parquet(root / "timeseries.parquet")
    flags_path = root / "flags.parquet"
    flags = pd.read_parquet(flags_path) if flags_path.exists() else pd.DataFrame()
    if thresholds is None:
        thresholds = load_thresholds()
    catalog = RuleCatalog.from_thresholds(thresholds)
    threshold_specs = {
        spec.id: spec for spec in catalog.specs
        if spec.kind == "threshold" and ("warn" in spec.params or "warn_mac" in spec.params)
    }

    signals = [c for c in OBSERVED_SIGNAL_COLS if c in ts.columns]
    observed = ts[signals].notna().any(axis=1)
    ts = ts.sort_values(["PID", "Obs_time"]).assign(_observed=observed)
    n_cases = int(ts["PID"].nunique())
    observed_minutes = int(observed.sum())

    coverage: dict[str, Any] = {
        "n_cases": _case_count(n_cases, min_cell),
        "grid_minutes": int(len(ts)) if n_cases >= min_cell else None,
        "observed_minutes": observed_minutes if int(ts.loc[observed, "PID"].nunique()) >= min_cell else None,
    }
    for stream, cols in STREAMS.items():
        coverage.update(_stream_gaps(ts, stream, cols, min_cell))

    if "phase" in ts.columns:
        phase_minutes = ts.loc[ts["_observed"], "phase"].value_counts()
        coverage["observed_minutes_by_phase"] = {
            p: (int(phase_minutes.get(p, 0)) if not 0 < ts.loc[ts["_observed"] & ts["phase"].eq(p), "PID"].nunique() < min_cell else None)
            for p in PHASES
        }
    coverage["phase_source_events_pct"] = None
    if "phase_source" in ts.columns:
        per_case = ts.drop_duplicates("PID")["phase_source"].value_counts()
        coverage["cases_by_phase_source"] = {
            src: _case_count(int(per_case.get(src, 0)), min_cell) for src in PHASE_SOURCES
        }
        n_events = int(per_case.get("events", 0))
        if n_cases >= min_cell and not (
            0 < n_events < min_cell or 0 < n_cases - n_events < min_cell
        ):
            coverage["phase_source_events_pct"] = round(100 * n_events / n_cases, 1)

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
            entry["n_minutes"] = None
            entry["missing_pct"] = None
        signal_stats[col] = entry

    obs_hours = observed_minutes / 60.0
    rule_stats: dict[str, Any] = {}
    case_codes = pd.factorize(obs["PID"])[0]
    phase_groups = obs.groupby("phase", sort=False).indices if "phase" in obs.columns else {}
    eligible_indices: dict[tuple[str, ...], np.ndarray] = {}
    numeric_signals = {
        col: pd.to_numeric(obs[col], errors="coerce").to_numpy(dtype=float)
        for col in {spec.signals[0] for spec in threshold_specs.values()} if col in obs.columns
    }
    for spec in catalog.specs:
        rule_id = spec.id
        f = flags[flags["rule_id"] == rule_id] if not flags.empty else flags
        n_minutes = int(f[["PID", "Obs_time"]].drop_duplicates().shape[0]) if len(f) else 0
        n_flagged = int(f["PID"].nunique()) if len(f) else 0
        entry = {
            "flagged_minutes": n_minutes if not 0 < n_flagged < min_cell else None,
            "cases_flagged": _case_count(n_flagged, min_cell),
            "cases_flagged_pct": (
                round(100 * n_flagged / n_cases, 1)
                if n_cases and not 0 < n_flagged < min_cell
                else None
            ),
            "flagged_minutes_per_hour": round(n_minutes / obs_hours, 3) if obs_hours and not 0 < n_flagged < min_cell else None,
        }
        if rule_id in threshold_specs:
            col, higher = spec.signals[0], spec.direction == "high"
            wkey, ckey = ("warn", "critical") if "warn" in spec.params else ("warn_mac", "critical_mac")
            if col in obs.columns:
                skipped = spec.skip_phases
                if skipped not in eligible_indices:
                    eligible_indices[skipped] = (
                        np.flatnonzero(~obs["phase"].isin(skipped).to_numpy())
                        if skipped and phase_groups else np.arange(len(obs))
                    )
                positions = eligible_indices[skipped]
                values = numeric_signals[col]
                if rule_id == "etco2_low":
                    values = np.where(values > 0, values, np.nan)
                warn, crit = spec.params.get(wkey), spec.params.get(ckey)
                if isinstance(warn, (int, float)) and isinstance(crit, (int, float)):
                    entry.update({
                        "signal": col,
                        "direction": "high" if higher else "low",
                        "warn": warn,
                        "critical": crit,
                    })
                    entry.update(_threshold_shares(values[positions], case_codes[positions], higher, warn, crit, min_cell))
                    entry["by_phase"] = {
                        phase: _threshold_shares(values[idx], case_codes[idx], higher, warn, crit, min_cell)
                        for phase, idx in phase_groups.items() if phase not in skipped
                    }
        rule_stats[rule_id] = entry

    events_path = root / "events.parquet"
    events_profile: dict[str, Any] = {}
    if events_path.exists():
        events = pd.read_parquet(events_path)
        if not events.empty and {"PID", "Event_name"} <= set(events.columns):
            events_profile = _event_profile(events, thresholds, n_cases, min_cell)

    return {
        "metadata": {
            "preset": thresholds.get("_preset", meta.get("preset", "unknown")),
            "thresholds_sha256": catalog.thresholds_sha256,
            "seed": seed if seed is not None else "unknown",
        },
        "min_cell": min_cell,
        "coverage": coverage,
        "signals": signal_stats,
        "rules": rule_stats,
        "events": events_profile,
    }


def _threshold_shares(values: np.ndarray, cases: np.ndarray, higher: bool, warn: float, critical: float, min_cell: int) -> dict:
    present = ~np.isnan(values)
    values, cases = values[present], cases[present]
    n_cases = len(np.unique(cases))
    result: dict[str, Any] = {
        "n_cases": _case_count(n_cases, min_cell),
        "n_minutes": len(values) if n_cases >= min_cell or not len(values) else None,
    }
    for name, threshold in (("warn", warn), ("critical", critical)):
        beyond = values >= threshold if higher else values <= threshold
        cases_beyond = len(np.unique(cases[beyond]))
        result[f"pct_minutes_beyond_{name}"] = (
            round(float(100 * beyond.mean()), 3)
            if n_cases >= min_cell and not 0 < cases_beyond < min_cell else None
        )
    return result


def render_markdown(profile: dict[str, Any]) -> str:
    """Render a profile dict as a Markdown report."""
    c = profile["coverage"]
    metadata = profile["metadata"]
    events_pct = c.get("phase_source_events_pct")
    events_headline = (
        f"{events_pct}%" if events_pct is not None else
        "suppressed" if "cases_by_phase_source" in c else "unavailable"
    )
    lines = [
        "# MOVER SIS pipeline profile (aggregate only)",
        "",
        f"Preset: {metadata['preset']}. Thresholds SHA256: {metadata['thresholds_sha256']}.",
        f"Cases: {c['n_cases']}. Seed: {metadata['seed']}.",
        f"phase_source events: {events_headline} of cases.",
        "",
        f"No PIDs or timestamps. Case counts under {profile['min_cell']} are shown "
        f"as <{profile['min_cell']}; minutes, gaps, and distributions from fewer cases are suppressed.",
        "",
        "## Coverage",
        "",
        "| Measure | Value |",
        "|---|---|",
    ]
    for k, v in c.items():
        if isinstance(v, dict):
            lines += [f"| {k}.{sub} | {_md_cell(val)} |" for sub, val in v.items()]
        else:
            lines.append(f"| {k} | {_md_cell(v)} |")

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
            f"| {col} | {s['unit']} | {_md_cell(s['n_minutes'])} | {s['n_cases']} | "
            f"{_md_cell(s['missing_pct'])} | " + " | ".join(pct) + " |"
        )

    lines += [
        "",
        "## Rules",
        "",
        "Threshold columns show the share of observed minutes past each threshold "
        "(before duration or ventilation gating), next to how often the rule fired.",
        "",
        "| Rule | Cases flagged | % cases | Flagged min | Per hour | Signal | Warn | "
        "% min beyond warn | Critical | % min beyond critical |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for rule_id, r in profile["rules"].items():
        pct_cases = "" if r["cases_flagged_pct"] is None else r["cases_flagged_pct"]
        lines.append(
            f"| {rule_id} | {r['cases_flagged']} | {pct_cases} | {_md_cell(r['flagged_minutes'])} | "
            f"{_md_cell(r['flagged_minutes_per_hour'])} | {r.get('signal', '')} | {r.get('warn', '')} | "
            f"{_md_cell(r.get('pct_minutes_beyond_warn', ''))} | {r.get('critical', '')} | "
            f"{_md_cell(r.get('pct_minutes_beyond_critical', ''))} |"
        )
    lines += [
        "", "## Threshold shares by phase", "",
        "Skipped phases are excluded. Percentages precede duration and ventilation gating.", "",
        "| Rule | Phase | Minutes | Cases | % beyond warn | % beyond critical |",
        "|---|---|---|---|---|---|",
    ]
    for rule_id, rule in profile["rules"].items():
        for phase, share in rule.get("by_phase", {}).items():
            lines.append(
                f"| {rule_id} | {phase} | {_md_cell(share['n_minutes'])} | {share['n_cases']} | "
                f"{_md_cell(share['pct_minutes_beyond_warn'])} | {_md_cell(share['pct_minutes_beyond_critical'])} |"
            )
    lines += _render_events(profile)
    return "\n".join(lines) + "\n"


def _md_cell(text: Any) -> str:
    if text is None:
        return "suppressed"
    return " ".join(str(text).split()).replace("|", "\\|")


def _render_events(profile: dict[str, Any]) -> list[str]:
    ev = profile.get("events")
    lines = [
        "",
        "## Procedure event names",
        "",
    ]
    if not ev:
        return lines + [
            "No `events.parquet` in the processed directory (or it is empty). Run the "
            "sample pipeline on the EMR to list its event names.",
        ]
    min_cell = profile["min_cell"]
    lines += [
        "Names are listed as recorded. The last column shows which phase boundary the "
        "`phases` patterns in `thresholds.yaml` give each name; edit the patterns until "
        "the real intubation and extubation names are matched.",
        "",
        f"Events: {ev['n_events']} in {ev['cases_with_events']} of {ev['n_cases']} cases "
        f"({ev['n_event_names']} distinct names). Cases with a matched intubation event: "
        f"{ev['cases_with_intubation_match']}; with a matched extubation event: "
        f"{ev['cases_with_extubation_match']}.",
        "",
        "| Event name | Events | Cases | Matched as |",
        "|---|---|---|---|",
    ]
    for n in ev["names"]:
        lines.append(f"| {_md_cell(n['name'])} | {n['events']} | {n['cases']} | {n['role']} |")
    if ev["pooled_names"]:
        lines.append(
            f"| (other names, each in fewer than {min_cell} cases: {ev['pooled_names']} names) "
            f"| {ev['pooled_events']} | <{min_cell} | |"
        )
    return lines


def render_diff(before: dict[str, Any], after: dict[str, Any]) -> str:
    lines = ["# Profile changes", ""]
    old_hash = before.get("metadata", {}).get("thresholds_sha256", "unknown")
    new_hash = after.get("metadata", {}).get("thresholds_sha256", "unknown")
    lines.append(f"Thresholds hashes {'differ' if old_hash != new_hash else 'match'}: {old_hash} -> {new_hash}.")
    lines += ["", "| Rule | Phase | Measure | Before | After | Change |", "|---|---|---|---|---|---|"]
    for rule_id in sorted(before["rules"].keys() | after["rules"].keys()):
        old, new = before["rules"].get(rule_id, {}), after["rules"].get(rule_id, {})
        rows = [("all eligible", old, new)]
        old_phases, new_phases = old.get("by_phase", {}), new.get("by_phase", {})
        rows += [(phase, old_phases.get(phase, {}), new_phases.get(phase, {})) for phase in sorted(old_phases.keys() | new_phases.keys())]
        for phase, left, right in rows:
            for key in sorted(left.keys() | right.keys()):
                a, b = left.get(key), right.get(key)
                if key == "by_phase" or a == b:
                    continue
                delta = round(b - a, 3) if isinstance(a, (int, float)) and isinstance(b, (int, float)) else "unavailable"
                lines.append(f"| {rule_id} | {phase} | {key} | {_md_cell(a)} | {_md_cell(b)} | {delta} |")
    old_phases = before.get("coverage", {}).get("observed_minutes_by_phase", {})
    new_phases = after.get("coverage", {}).get("observed_minutes_by_phase", {})
    for phase in sorted(old_phases.keys() | new_phases.keys()):
        a, b = old_phases.get(phase), new_phases.get(phase)
        if a != b:
            delta = b - a if isinstance(a, int) and isinstance(b, int) else "unavailable"
            lines.append(f"| (coverage) | {phase} | observed_minutes | {_md_cell(a)} | {_md_cell(b)} | {delta} |")
    if len(lines) == 6:
        lines.append("No rule or phase changes.")
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
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--diff", nargs=2, metavar=("BEFORE", "AFTER"))
    parser.add_argument("--out", type=str, default=None, help="Markdown path (default: stdout)")
    parser.add_argument("--json", type=str, default=None, help="Also write the profile as JSON")
    args = parser.parse_args(argv)

    if args.diff:
        print(render_diff(*(json.loads(Path(path).read_text()) for path in args.diff)))
        return
    if args.min_cell < 1:
        parser.error("--min-cell must be at least 1")
    if args.processed_dir is None:
        from src.runtime_paths import processed_dir

        root = processed_dir()
    else:
        root = Path(args.processed_dir)
    profile = profile_processed(root, load_thresholds(args.preset), min_cell=args.min_cell, seed=args.seed)
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
