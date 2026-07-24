"""End-to-end pipeline: load → clean → merge → features → flags → save."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from src.config import load_thresholds
from src.pipeline.clean import (
    clean_case_info,
    clean_procedure_events,
    clean_ventilator,
    clean_vitals,
    filter_to_case_window,
)
from src.pipeline.features import add_features, build_case_summary
from src.pipeline.flags import collapse_episodes, flag_anomalies, score_cases
from src.pipeline.load import (
    load_case_info,
    load_procedure_events,
    load_ventilator,
    load_vitals,
    sample_pids_with_ventilator,
)
from src.pipeline.merge import merge_vent_vitals


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def run_pipeline(
    emr_dir: Path | str | None = None,
    output_dir: Path | str | None = None,
    n_cases: int = 50,
    pids: list[str] | None = None,
    preset: str = "default",
    seed: int = 42,
    vent_scan_rows: int = 400_000,
    min_vent_rows: int = 30,
    write_sample_csv: bool = True,
) -> dict[str, pd.DataFrame]:
    """
    Run the full cleaning / merge / flag pipeline on a sample of cases.

    Returns dict with keys: cases, timeseries, flags, episodes, scores.
    """
    root = repo_root()
    if emr_dir is None:
        emr_dir = root / "data" / "raw" / "EMR"
    else:
        emr_dir = Path(emr_dir)

    if output_dir is None:
        output_dir = root / "data" / "processed"
    else:
        output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    thresholds = load_thresholds(preset)
    print(f"[pipeline] preset={preset} emr_dir={emr_dir}")

    # --- select PIDs ---
    if pids is None:
        pids = sample_pids_with_ventilator(
            emr_dir,
            n=n_cases,
            min_vent_rows=min_vent_rows,
            seed=seed,
            vent_nrows_scan=vent_scan_rows,
        )
        print(f"[pipeline] sampled {len(pids)} PIDs with ventilator data")
    else:
        pids = [str(p) for p in pids]
        print(f"[pipeline] using {len(pids)} provided PIDs")

    if not pids:
        raise RuntimeError("No PIDs selected — check ventilator file path / scan size.")

    # --- load ---
    print("[pipeline] loading case info…")
    cases_raw = load_case_info(emr_dir)
    cases_raw["PID"] = cases_raw["PID"].astype(str)
    cases_raw = cases_raw[cases_raw["PID"].isin(pids)].copy()

    print("[pipeline] loading ventilator…")
    vent_raw = load_ventilator(emr_dir, pids=pids)
    print(f"  vent rows: {len(vent_raw)}")

    print("[pipeline] loading vitals…")
    vitals_raw = load_vitals(emr_dir, pids=pids)
    print(f"  vitals rows: {len(vitals_raw)}")

    events_raw = load_procedure_events(emr_dir, pids=pids)

    # --- clean ---
    print("[pipeline] cleaning…")
    cases = clean_case_info(cases_raw)
    vent = clean_ventilator(vent_raw, thresholds)
    vitals = clean_vitals(vitals_raw, thresholds)
    events = clean_procedure_events(events_raw)

    vent = filter_to_case_window(vent, cases, pad_minutes=5)
    vitals = filter_to_case_window(vitals, cases, pad_minutes=5)

    # --- merge + features ---
    print("[pipeline] merging & features…")
    ts = merge_vent_vitals(vent, vitals, cases)
    ts = add_features(ts, thresholds)
    print(f"  timeseries rows: {len(ts)}  cases: {ts['PID'].nunique()}")

    # --- flags ---
    print("[pipeline] flagging anomalies…")
    flags = flag_anomalies(ts, thresholds)
    episodes = collapse_episodes(flags)
    scores = score_cases(flags, thresholds)

    case_summary = build_case_summary(ts, flags)
    # Drop flag-count columns from build_case_summary; prefer score_cases
    drop_pre = [c for c in ["n_info", "n_warn", "n_critical", "n_composite_rows", "n_rule_types"]
                if c in case_summary.columns]
    case_summary = case_summary.drop(columns=drop_pre, errors="ignore")
    case_summary["PID"] = case_summary["PID"].astype(str)
    if scores is not None and not scores.empty:
        scores = scores.copy()
        scores["PID"] = scores["PID"].astype(str)
        case_summary = case_summary.merge(scores, on="PID", how="left")
    for c in ["n_info", "n_warn", "n_critical", "n_composite", "anomaly_score"]:
        if c not in case_summary.columns:
            case_summary[c] = 0
        case_summary[c] = case_summary[c].fillna(0).astype(int)
    if "top_rules" not in case_summary.columns:
        case_summary["top_rules"] = ""
    else:
        case_summary["top_rules"] = case_summary["top_rules"].fillna("")
    case_summary = case_summary.sort_values("anomaly_score", ascending=False).reset_index(drop=True)

    # --- write ---
    print(f"[pipeline] writing outputs → {output_dir}")
    ts.to_parquet(output_dir / "timeseries.parquet", index=False)
    case_summary.to_parquet(output_dir / "cases.parquet", index=False)
    flags.to_parquet(output_dir / "flags.parquet", index=False)
    episodes.to_parquet(output_dir / "episodes.parquet", index=False)
    if not events.empty:
        events.to_parquet(output_dir / "events.parquet", index=False)

    meta = {
        "n_cases": int(case_summary["PID"].nunique()),
        "n_timeseries_rows": int(len(ts)),
        "n_flag_rows": int(len(flags)),
        "preset": preset,
        "pids": pids,
    }
    with open(output_dir / "run_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    if write_sample_csv:
        sample_dir = root / "data" / "sample"
        sample_dir.mkdir(parents=True, exist_ok=True)
        case_summary.head(20).to_csv(sample_dir / "cases_preview.csv", index=False)
        flags.head(500).to_csv(sample_dir / "flags_preview.csv", index=False)

    print("[pipeline] done.")
    print(case_summary[["PID", "primary_agent_name", "anomaly_score", "n_warn", "n_critical", "top_rules"]].head(10).to_string(index=False))

    return {
        "cases": case_summary,
        "timeseries": ts,
        "flags": flags,
        "episodes": episodes,
        "scores": scores,
        "events": events,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run MOVER SIS ventilation pipeline")
    parser.add_argument("--emr-dir", type=str, default=None)
    parser.add_argument("--output-dir", type=str, default=None)
    parser.add_argument("--n-cases", type=int, default=50)
    parser.add_argument("--preset", type=str, default="default", choices=["default", "strict", "lenient"])
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--vent-scan-rows", type=int, default=400_000)
    args = parser.parse_args(argv)

    run_pipeline(
        emr_dir=args.emr_dir,
        output_dir=args.output_dir,
        n_cases=args.n_cases,
        preset=args.preset,
        seed=args.seed,
        vent_scan_rows=args.vent_scan_rows,
    )


if __name__ == "__main__":
    main()
