"""
Aggregate profile of a full-EMR scan, built from mergeable per-shard histograms.

A shard worker never returns its minute-level frames, so percentiles cannot be
computed from the whole corpus. Each worker instead returns ``shard_histogram``:
fixed-width bin counts per signal plus case, minute and episode counts. Shards
partition PIDs, so every count adds up across shards (``merge_histograms``) and
the merged bins give percentiles to within one bin width.

The rendered profile is aggregate only: no PIDs, timestamps, case rows or
per-case values, and every count between 1 and ``min_cell - 1`` is shown as
``<min_cell``.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from src.pipeline.features import OBSERVED_SIGNAL_COLS
from src.pipeline.phases import PHASE_SOURCES, PHASES
from src.pipeline.profile import PROFILE_SIGNALS
from src.rules import RuleCatalog

N_BINS = 100
SCAN_PERCENTILES = (5, 25, 50, 75, 95)
DEFAULT_MIN_CELL = 11

# Derived signals have no cleaning range in thresholds.yaml; these cover their
# plausible values, and anything outside is counted as under or over.
DERIVED_RANGES: dict[str, tuple[float, float]] = {
    "TV_mlkg": (0.0, 20.0),
    "MAC_total_Et": (0.0, 4.0),
    "PIP_slope": (-5.0, 5.0),
}


def signal_ranges(thresholds: dict) -> dict[str, tuple[float, float]]:
    """Bin range (low, high) of every profiled signal."""
    clean = thresholds.get("clean_ranges") or {}
    ranges: dict[str, tuple[float, float]] = {}
    for col, _unit in PROFILE_SIGNALS:
        if col in clean:
            low, high = clean[col]
            ranges[col] = (float(low), float(high))
        elif col in DERIVED_RANGES:
            ranges[col] = DERIVED_RANGES[col]
    return ranges


def _bin_counts(values: np.ndarray, low: float, high: float) -> tuple[np.ndarray, int, int]:
    """Counts in ``N_BINS`` equal bins over [low, high], plus values below and above."""
    width = (high - low) / N_BINS
    under = int((values < low).sum())
    over = int((values > high).sum())
    inside = values[(values >= low) & (values <= high)]
    index = np.minimum(np.floor((inside - low) / width).astype(np.int64), N_BINS - 1)
    return np.bincount(index, minlength=N_BINS).astype(np.int64), under, over


def shard_histogram(
    ts: pd.DataFrame,
    flags: pd.DataFrame,
    episodes: pd.DataFrame,
    thresholds: dict,
) -> dict[str, Any]:
    """Mergeable counts for one shard's scored frames; small and free of PIDs."""
    # Work on single columns, not a filtered copy of the whole frame: the
    # shard's timeseries is the largest object a worker holds
    signals = [c for c in OBSERVED_SIGNAL_COLS if c in ts.columns]
    observed = (
        ts[signals].notna().any(axis=1).to_numpy()
        if signals
        else np.ones(len(ts), dtype=bool)
    )
    case_codes = pd.factorize(ts["PID"])[0][observed]
    hist: dict[str, Any] = {
        "n_cases": int(ts["PID"].nunique()),
        "observed_minutes": int(observed.sum()),
        "signals": {},
        "rules": {},
        "phases": {},
        "phase_source": {},
    }

    for col, (low, high) in signal_ranges(thresholds).items():
        if col not in ts.columns:
            continue
        values = pd.to_numeric(ts[col], errors="coerce").to_numpy(dtype=float)[observed]
        present = np.isfinite(values)
        bins, under, over = _bin_counts(values[present], low, high)
        hist["signals"][col] = {
            "bins": bins,
            "under": under,
            "over": over,
            "minutes": int(present.sum()),
            "cases": int(np.unique(case_codes[present]).size),
        }

    if flags is not None and not flags.empty:
        unique_minutes = flags.drop_duplicates(["rule_id", "PID", "Obs_time"])
        minutes = unique_minutes.groupby("rule_id").size()
        cases = flags.groupby("rule_id")["PID"].nunique()
        n_episodes = (
            episodes.groupby("rule_id").size()
            if episodes is not None and not episodes.empty
            else pd.Series(dtype=int)
        )
        for rule_id in minutes.index:
            hist["rules"][str(rule_id)] = {
                "minutes": int(minutes[rule_id]),
                "cases": int(cases[rule_id]),
                "episodes": int(n_episodes.get(rule_id, 0)),
            }

    if "phase" in ts.columns:
        by_phase = pd.DataFrame(
            {"phase": ts["phase"].to_numpy()[observed], "case": case_codes}
        ).groupby("phase", sort=False)["case"]
        phase_cases = by_phase.nunique()
        for phase, minutes in by_phase.size().items():
            hist["phases"][str(phase)] = {
                "minutes": int(minutes),
                "cases": int(phase_cases[phase]),
            }
    if "phase_source" in ts.columns:
        per_case = ts.drop_duplicates("PID")["phase_source"].value_counts()
        hist["phase_source"] = {str(k): int(v) for k, v in per_case.items()}
    return hist


def _add(a: Any, b: Any) -> Any:
    if isinstance(a, dict):
        merged = dict(a)
        for key, value in b.items():
            merged[key] = _add(a[key], value) if key in a else value
        return merged
    return a + b


def merge_histograms(histograms: list[dict[str, Any]]) -> dict[str, Any]:
    """Element-wise sum of shard histograms; ``{}`` when there are none."""
    merged: dict[str, Any] = {}
    for hist in histograms:
        merged = _add(merged, hist) if merged else hist
    return merged


def binned_percentile(entry: dict[str, Any], low: float, high: float, q: float) -> float | str | None:
    """Percentile *q* from merged bins, to within one bin width.

    Returns ``"<low"`` or ``">high"`` when the percentile falls outside the
    binned range, and None when there are no values.
    """
    bins = np.asarray(entry["bins"], dtype=np.int64)
    under, over = int(entry["under"]), int(entry["over"])
    total = under + int(bins.sum()) + over
    if total == 0:
        return None
    target = q / 100.0 * total
    if under and target <= under:
        return f"<{low:g}"
    cumulative = under + np.cumsum(bins)
    idx = int(np.searchsorted(cumulative, target, side="left"))
    if idx >= N_BINS:
        return f">{high:g}"
    width = (high - low) / N_BINS
    before = cumulative[idx] - bins[idx]
    return round(float(low + width * (idx + (target - before) / bins[idx])), 2)


def _count(n: int, min_cell: int) -> int | str:
    return f"<{min_cell}" if 0 < n < min_cell else int(n)


def _share(part: int, whole: int, min_cell: int) -> float | None:
    """Percent of *whole*, withheld when *part* or the rest is a small cell."""
    if whole < min_cell or 0 < part < min_cell or 0 < whole - part < min_cell:
        return None
    return round(100 * part / whole, 1)


def build_corpus_profile(
    hist: dict[str, Any],
    thresholds: dict,
    *,
    n_cases_left_out: int = 0,
    n_failed_shards: int = 0,
    min_cell: int = DEFAULT_MIN_CELL,
) -> dict[str, Any]:
    """Aggregate profile dict from a merged histogram."""
    if min_cell < 1:
        raise ValueError("min_cell must be at least 1")
    n_cases = int(hist.get("n_cases", 0))
    observed_minutes = int(hist.get("observed_minutes", 0))
    ranges = signal_ranges(thresholds)

    signals: dict[str, Any] = {}
    units = dict(PROFILE_SIGNALS)
    for col, (low, high) in ranges.items():
        entry = hist.get("signals", {}).get(col)
        if entry is None:
            continue
        width = (high - low) / N_BINS
        out: dict[str, Any] = {
            "unit": units[col],
            "range": [low, high],
            "bin_width": round(width, 6),
            "n_cases": _count(entry["cases"], min_cell),
        }
        if entry["cases"] >= min_cell:
            out["n_minutes"] = _count(entry["minutes"], min_cell)
            out["missing_pct"] = (
                round(100 * (1 - entry["minutes"] / observed_minutes), 2)
                if observed_minutes
                else None
            )
            out["below_range_minutes"] = _count(entry["under"], min_cell)
            out["above_range_minutes"] = _count(entry["over"], min_cell)
            for p in SCAN_PERCENTILES:
                out[f"p{p}"] = binned_percentile(entry, low, high, p)
        else:
            out["suppressed"] = True
        signals[col] = out

    catalog = RuleCatalog.from_thresholds(thresholds)
    rules: dict[str, Any] = {}
    for spec in catalog.specs:
        r = hist.get("rules", {}).get(spec.id, {"minutes": 0, "cases": 0, "episodes": 0})
        small = 0 < r["cases"] < min_cell
        rules[spec.id] = {
            "cases_flagged": _count(r["cases"], min_cell),
            "cases_flagged_pct": None if small else _share(r["cases"], n_cases, min_cell),
            "flagged_minutes": None if small else _count(r["minutes"], min_cell),
            "episodes": None if small else _count(r["episodes"], min_cell),
        }

    phases: dict[str, Any] = {}
    for phase in PHASES:
        p = hist.get("phases", {}).get(phase, {"minutes": 0, "cases": 0})
        small = 0 < p["cases"] < min_cell
        phases[phase] = {
            "cases": _count(p["cases"], min_cell),
            "minutes": None if small else _count(p["minutes"], min_cell),
            "minutes_pct": (
                None
                if small or observed_minutes < min_cell
                else _share(p["minutes"], observed_minutes, min_cell)
            ),
        }

    sources = hist.get("phase_source", {})
    phase_source = {
        src: {
            "cases": _count(int(sources.get(src, 0)), min_cell),
            "cases_pct": _share(int(sources.get(src, 0)), n_cases, min_cell),
        }
        for src in PHASE_SOURCES
    }

    return {
        "metadata": {
            "preset": thresholds.get("_preset", "unknown"),
            "bins": N_BINS,
        },
        "min_cell": min_cell,
        "coverage": {
            "n_cases": _count(n_cases, min_cell),
            "observed_minutes": observed_minutes if n_cases >= min_cell else None,
            "failed_shards": n_failed_shards,
            "cases_left_out_by_failed_shards": _count(n_cases_left_out, min_cell),
        },
        "signals": signals,
        "rules": rules,
        "phases": phases,
        "phase_source": phase_source,
    }


def _cell(value: Any) -> str:
    return "suppressed" if value is None else str(value)


def render_corpus_profile_md(profile: dict[str, Any], thresholds_sha256: str = "") -> str:
    """Markdown rendering of ``build_corpus_profile``."""
    cov = profile["coverage"]
    min_cell = profile["min_cell"]
    lines = [
        "# MOVER SIS full-EMR scan profile (aggregate only)",
        "",
        f"Preset: {profile['metadata']['preset']}."
        + (f" Thresholds SHA256: {thresholds_sha256}." if thresholds_sha256 else ""),
        f"Cases profiled: {cov['n_cases']}.",
    ]
    if cov["failed_shards"]:
        lines.append(
            f"{cov['failed_shards']} shard(s) could not be scored and are left out of this "
            f"profile, which leaves out {cov['cases_left_out_by_failed_shards']} case(s)."
        )
    lines += [
        "",
        f"No PIDs or timestamps. Counts under {min_cell} are shown as <{min_cell}; "
        "distributions from fewer cases are suppressed. Percentiles come from "
        f"{profile['metadata']['bins']} equal-width bins per signal, so each is exact "
        "only to within the bin width shown.",
        "",
        "## Signals (observed minutes)",
        "",
        "| Signal | Unit | Minutes | Cases | Missing % | Bin width | "
        + " | ".join(f"p{p}" for p in SCAN_PERCENTILES)
        + " | Below range | Above range |",
        "|---|---|---|---|---|---|" + "---|" * len(SCAN_PERCENTILES) + "---|---|",
    ]
    for col, s in profile["signals"].items():
        if s.get("suppressed"):
            values = ["suppressed"] * (len(SCAN_PERCENTILES) + 4)
        else:
            values = [
                _cell(s["n_minutes"]),
                _cell(s["missing_pct"]),
                *(_cell(s[f"p{p}"]) for p in SCAN_PERCENTILES),
                _cell(s["below_range_minutes"]),
                _cell(s["above_range_minutes"]),
            ]
        minutes, missing, *rest = values
        lines.append(
            f"| {col} | {s['unit']} | {minutes} | {s['n_cases']} | {missing} | "
            f"{s['bin_width']:g} | " + " | ".join(rest) + " |"
        )

    lines += [
        "",
        "## Rules",
        "",
        "| Rule | Cases flagged | % cases | Flagged minutes | Episodes |",
        "|---|---|---|---|---|",
    ]
    for rule_id, r in profile["rules"].items():
        lines.append(
            f"| {rule_id} | {r['cases_flagged']} | {_cell(r['cases_flagged_pct'])} | "
            f"{_cell(r['flagged_minutes'])} | {_cell(r['episodes'])} |"
        )

    lines += [
        "",
        "## Phases (observed minutes)",
        "",
        "| Phase | Cases | Minutes | % of minutes |",
        "|---|---|---|---|",
    ]
    for phase, p in profile["phases"].items():
        lines.append(f"| {phase} | {p['cases']} | {_cell(p['minutes'])} | {_cell(p['minutes_pct'])} |")

    lines += [
        "",
        "## How phases were derived (cases)",
        "",
        "| phase_source | Cases | % cases |",
        "|---|---|---|",
    ]
    for src, p in profile["phase_source"].items():
        lines.append(f"| {src} | {p['cases']} | {_cell(p['cases_pct'])} |")
    return "\n".join(lines) + "\n"
