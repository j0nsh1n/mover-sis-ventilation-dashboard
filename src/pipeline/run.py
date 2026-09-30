"""End-to-end pipeline: load → clean → merge → features → flags → save."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd

from src.config import load_thresholds
from src.guardrails.exceptions import GuardrailError, PipelineError
from src.guardrails.limits import MAX_VENT_SCAN_ROWS
from src.guardrails.validate_data import (
    assert_cleaned_cases,
    assert_raw_cases,
    assert_raw_ventilator,
    assert_raw_vitals,
    validate_pipeline_outputs,
)
from src.guardrails.validate_io import (
    atomic_write_json,
    atomic_write_parquet,
    resolve_emr_dir,
    resolve_output_dir,
    validate_emr_dir,
    validate_pipeline_params,
)
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

logger = logging.getLogger(__name__)


def repo_root() -> Path:
    from src.runtime_paths import app_dir

    return app_dir()


def process_frames(
    cases_raw: pd.DataFrame,
    vent_raw: pd.DataFrame,
    vitals_raw: pd.DataFrame,
    events_raw: pd.DataFrame,
    thresholds: dict,
    *,
    pad_minutes: float = 5.0,
    validate: bool = True,
    verbose: bool = True,
) -> dict[str, pd.DataFrame]:
    """Clean, merge, feature and flag raw SIS frames for a set of surgeries.

    Shared by ``run_pipeline`` (a sample written to disk) and the full-EMR
    scan (batches whose timeseries are dropped after scoring).
    Returns dict with keys: cases, timeseries, flags, episodes, scores, events.
    """

    def log(message: str) -> None:
        if verbose:
            print(message)

    # --- clean ---
    log("[pipeline] cleaning…")
    cases = clean_case_info(cases_raw)
    vent = clean_ventilator(vent_raw, thresholds)
    vitals = clean_vitals(vitals_raw, thresholds)
    events = clean_procedure_events(events_raw)

    if validate:
        assert_cleaned_cases(cases)

    vent = filter_to_case_window(vent, cases, pad_minutes=pad_minutes)
    vitals = filter_to_case_window(vitals, cases, pad_minutes=pad_minutes)

    if vent.empty:
        raise PipelineError(
            "No ventilator rows remain after cleaning/window filter. "
            "Check timestamps and case OR windows."
        )

    # --- merge + features ---
    log("[pipeline] merging & features…")
    ts = merge_vent_vitals(vent, vitals, cases)
    if ts.empty:
        raise PipelineError("Merged timeseries is empty")
    ts = add_features(ts, thresholds)
    # Enforce sort + unique minutes (invariant for flagging and plots)
    ts = ts.sort_values(["PID", "Obs_time"]).drop_duplicates(
        subset=["PID", "Obs_time"], keep="last"
    ).reset_index(drop=True)
    log(f"  timeseries rows: {len(ts)}  cases: {ts['PID'].nunique()}")

    # --- flags ---
    log("[pipeline] flagging anomalies…")
    flags = flag_anomalies(ts, thresholds)
    episodes = collapse_episodes(flags)

    scores = score_cases(flags, thresholds)

    case_summary = build_case_summary(ts, flags)
    drop_pre = [
        c
        for c in ["n_info", "n_warn", "n_critical", "n_composite_rows", "n_rule_types"]
        if c in case_summary.columns
    ]
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
    # Score per observed hour, so long cases do not outrank short ones on
    # duration alone; cases with no observed minutes stay NaN
    hours = case_summary["n_minutes"].astype(float) / 60.0
    case_summary["anomaly_score_per_hour"] = (
        case_summary["anomaly_score"] / hours.where(hours > 0)
    ).round(2)
    if "top_rules" not in case_summary.columns:
        case_summary["top_rules"] = ""
    else:
        case_summary["top_rules"] = case_summary["top_rules"].fillna("")
    case_summary = case_summary.sort_values(
        "anomaly_score", ascending=False
    ).reset_index(drop=True)

    return {
        "cases": case_summary,
        "timeseries": ts,
        "flags": flags,
        "episodes": episodes,
        "scores": scores,
        "events": events,
    }


def run_pipeline(
    emr_dir: Path | str | None = None,
    output_dir: Path | str | None = None,
    n_cases: int = 50,
    pids: list[str] | None = None,
    preset: str = "default",
    seed: int = 42,
    vent_scan_rows: int = MAX_VENT_SCAN_ROWS,
    min_vent_rows: int = 30,
    write_sample_csv: bool = True,
    pad_minutes: float = 5.0,
    validate: bool = True,
) -> dict[str, pd.DataFrame]:
    """
    Run the full cleaning / merge / flag pipeline on a sample of cases.

    Guardrails:
      - parameter bounds
      - EMR directory + required files
      - threshold schema validation
      - stage schema checks
      - final output invariants
      - atomic parquet/json writes (no partial files)

    Returns dict with keys: cases, timeseries, flags, episodes, scores, events.
    """
    validate_pipeline_params(
        n_cases=n_cases,
        preset=preset,
        seed=seed,
        vent_scan_rows=vent_scan_rows,
        min_vent_rows=min_vent_rows,
        pad_minutes=pad_minutes,
        pids=pids,
    )

    root = repo_root()
    if emr_dir is not None:
        emr_path = validate_emr_dir(emr_dir)
    else:
        emr_path = validate_emr_dir(resolve_emr_dir(None))
    out_path = resolve_output_dir(output_dir, create=True)

    thresholds = load_thresholds(preset, validate=validate)
    logger.info("pipeline start preset=%s emr=%s out=%s", preset, emr_path, out_path)
    print(f"[pipeline] preset={preset} emr_dir={emr_path}")

    # --- select PIDs ---
    if pids is None:
        pids = sample_pids_with_ventilator(
            emr_path,
            n=n_cases,
            min_vent_rows=min_vent_rows,
            seed=seed,
            vent_nrows_scan=vent_scan_rows,
        )
        print(f"[pipeline] sampled {len(pids)} PIDs with ventilator data")
    else:
        pids = [str(p).strip() for p in pids if str(p).strip()]
        print(f"[pipeline] using {len(pids)} provided PIDs")

    if not pids:
        raise PipelineError(
            "No PIDs selected — check ventilator file path / scan size / min_vent_rows."
        )

    # --- load ---
    print("[pipeline] loading case info…")
    cases_raw = load_case_info(emr_path)
    cases_raw["PID"] = cases_raw["PID"].astype(str)
    cases_raw = cases_raw[cases_raw["PID"].isin(pids)].copy()
    if validate:
        if cases_raw.empty:
            raise PipelineError(
                f"None of the selected PIDs appear in patient_information "
                f"(n_pids={len(pids)})"
            )
        assert_raw_cases(cases_raw)

    print("[pipeline] loading ventilator…")
    vent_raw = load_ventilator(emr_path, pids=pids)
    print(f"  vent rows: {len(vent_raw)}")
    if validate:
        assert_raw_ventilator(vent_raw)

    print("[pipeline] loading vitals…")
    vitals_raw = load_vitals(emr_path, pids=pids)
    print(f"  vitals rows: {len(vitals_raw)}")
    if validate:
        assert_raw_vitals(vitals_raw)

    events_raw = load_procedure_events(emr_path, pids=pids)

    frames = process_frames(
        cases_raw,
        vent_raw,
        vitals_raw,
        events_raw,
        thresholds,
        pad_minutes=pad_minutes,
        validate=validate,
    )
    case_summary = frames["cases"]
    ts = frames["timeseries"]
    flags = frames["flags"]
    episodes = frames["episodes"]
    scores = frames["scores"]
    events = frames["events"]

    if validate:
        validate_pipeline_outputs(
            timeseries=ts,
            cases=case_summary,
            flags=flags,
            episodes=episodes,
        )

    # --- write (atomic) ---
    print(f"[pipeline] writing outputs → {out_path}")
    atomic_write_parquet(ts, out_path / "timeseries.parquet")
    atomic_write_parquet(case_summary, out_path / "cases.parquet")
    atomic_write_parquet(flags, out_path / "flags.parquet")
    atomic_write_parquet(episodes, out_path / "episodes.parquet")
    if not events.empty:
        atomic_write_parquet(events, out_path / "events.parquet")

    meta = {
        "n_cases": int(case_summary["PID"].nunique()),
        "n_timeseries_rows": int(len(ts)),
        "n_flag_rows": int(len(flags)),
        "preset": preset,
        "pids": pids,
        "emr_dir": str(emr_path),
        "guardrails_validated": bool(validate),
    }
    atomic_write_json(meta, out_path / "run_meta.json")

    if write_sample_csv:
        sample_dir = root / "data" / "sample"
        sample_dir.mkdir(parents=True, exist_ok=True)
        case_summary.head(20).to_csv(sample_dir / "cases_preview.csv", index=False)
        flags.head(500).to_csv(sample_dir / "flags_preview.csv", index=False)

    print("[pipeline] done.")
    cols = [
        c
        for c in [
            "PID",
            "primary_agent_name",
            "anomaly_score",
            "n_warn",
            "n_critical",
            "top_rules",
        ]
        if c in case_summary.columns
    ]
    if cols:
        print(case_summary[cols].head(10).to_string(index=False))

    return {
        "cases": case_summary,
        "timeseries": ts,
        "flags": flags,
        "episodes": episodes,
        "scores": scores,
        "events": events,
    }


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Run MOVER SIS ventilation pipeline")
    parser.add_argument("--emr-dir", type=str, default=None)
    parser.add_argument("--output-dir", type=str, default=None)
    parser.add_argument("--n-cases", type=int, default=50)
    parser.add_argument(
        "--preset",
        type=str,
        default="default",
        choices=sorted(["default", "strict", "lenient"]),
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--vent-scan-rows",
        type=int,
        default=MAX_VENT_SCAN_ROWS,
        help="Max ventilator rows scanned when sampling PIDs (default: whole file)",
    )
    parser.add_argument(
        "--no-validate",
        action="store_true",
        help="Disable stage/output guardrails (not recommended)",
    )
    args = parser.parse_args(argv)

    try:
        run_pipeline(
            emr_dir=args.emr_dir,
            output_dir=args.output_dir,
            n_cases=args.n_cases,
            preset=args.preset,
            seed=args.seed,
            vent_scan_rows=args.vent_scan_rows,
            validate=not args.no_validate,
        )
    except GuardrailError as e:
        logger.error("Pipeline blocked by guardrail: %s", e)
        raise SystemExit(2) from e


if __name__ == "__main__":
    main()
