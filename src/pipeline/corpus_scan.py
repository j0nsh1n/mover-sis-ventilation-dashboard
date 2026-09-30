"""
Full-EMR scan: score every ventilated surgery without holding all timeseries.

The normal pipeline analyzes a sample (up to 500 cases) and keeps its
minute-level timeseries for charts. This scan covers every surgery with
ventilator rows, but memory stays bounded:

1. Stream ``patient_ventilator.csv``, ``patient_vitals.csv`` and
   ``patient_procedure_events.csv`` in chunks and split their rows by PID
   into shard files on disk.
2. Run the same clean → merge → features → flags code (``process_frames``)
   on one shard at a time, keep only the per-case summary and episodes, and
   drop the shard's timeseries before the next one.

Outputs in the processed directory (the sample's files are left untouched):

- ``corpus_cases.parquet``   one row per scanned surgery (scores, top rules)
- ``corpus_episodes.parquet`` contiguous flag runs for every scanned surgery
- ``corpus_meta.json``       scope counts, preset, thresholds hash, timing

    PYTHONPATH=. python -m src.pipeline.corpus_scan --preset default
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import shutil
import time
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

import pandas as pd

from src.config import load_thresholds
from src.guardrails.exceptions import GuardrailError, PipelineError
from src.guardrails.validate_io import (
    atomic_write_json,
    atomic_write_parquet,
    resolve_emr_dir,
    resolve_output_dir,
    validate_emr_dir,
)
from src.pipeline.load import NA_VALUES, VENT_RENAME, VITALS_RENAME, load_case_info
from src.pipeline.run import process_frames

logger = logging.getLogger(__name__)

CORPUS_CASES = "corpus_cases.parquet"
CORPUS_EPISODES = "corpus_episodes.parquet"
CORPUS_META = "corpus_meta.json"

# Surgeries per shard: large enough to amortize pandas overhead, small enough
# that one shard's minute-level frames stay well under a few hundred MB.
DEFAULT_CASES_PER_SHARD = 300
READ_CHUNK_ROWS = 200_000

ProgressCb = Callable[[str, int, int], None]


class ScanCancelled(PipelineError):
    """The caller asked the scan to stop."""


@dataclass
class ScanResult:
    cases: pd.DataFrame
    episodes: pd.DataFrame
    meta: dict


def _shard_of(pid: str, n_shards: int) -> int:
    return zlib.crc32(pid.encode("utf-8")) % n_shards


def _split_csv(src: Path, dest_dir: Path, name: str, n_shards: int) -> int:
    """Stream *src* into ``dest_dir/<shard>/<name>.csv`` by PID; return rows."""
    if not src.is_file():
        return 0
    rows = 0
    written: set[int] = set()
    for chunk in pd.read_csv(
        src,
        dtype=str,
        keep_default_na=False,
        chunksize=READ_CHUNK_ROWS,
    ):
        chunk.columns = [c.strip().strip('"') for c in chunk.columns]
        if "PID" not in chunk.columns:
            raise PipelineError(f"{src.name} has no PID column")
        shard = chunk["PID"].astype(str).map(lambda p: _shard_of(p, n_shards))
        for key, part in chunk.groupby(shard):
            out = dest_dir / str(key) / f"{name}.csv"
            out.parent.mkdir(parents=True, exist_ok=True)
            part.to_csv(out, mode="a", header=key not in written, index=False)
            written.add(key)
        rows += len(chunk)
    return rows


def _read_shard(path: Path, rename: dict[str, str] | None = None) -> pd.DataFrame:
    """Read a shard as ``load_ventilator`` / ``load_vitals`` would the full file."""
    if not path.is_file():
        return pd.DataFrame()
    df = pd.read_csv(path, na_values=NA_VALUES, keep_default_na=True, low_memory=False)
    df = df.rename(columns=rename or {})
    df["PID"] = df["PID"].astype(str)
    return df


def _thresholds_hash(thresholds: dict) -> str:
    blob = json.dumps(thresholds, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def scan_corpus(
    emr_dir: Path | str | None = None,
    output_dir: Path | str | None = None,
    *,
    preset: str = "default",
    cases_per_shard: int = DEFAULT_CASES_PER_SHARD,
    pad_minutes: float = 5.0,
    on_progress: ProgressCb | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> ScanResult:
    """Score every ventilated surgery in the EMR, one shard at a time.

    ``should_stop`` is polled between shards; when it returns True the scan
    stops, removes its temporary shards and raises ScanCancelled. Nothing is
    written in that case, so a previous scan stays in place.
    """

    def progress(message: str, done: int = 0, total: int = 0) -> None:
        logger.info(message)
        if on_progress is not None:
            on_progress(message, done, total)

    started = time.monotonic()
    emr_path = validate_emr_dir(emr_dir if emr_dir is not None else resolve_emr_dir(None))
    out_path = resolve_output_dir(output_dir, create=True)
    thresholds = load_thresholds(preset, validate=True)

    info = load_case_info(emr_path)
    info["PID"] = info["PID"].astype(str)
    n_indexed = int(info["PID"].nunique())

    vent_pids = pd.read_csv(
        emr_path / "patient_ventilator.csv", usecols=["PID"], dtype=str
    )["PID"].dropna().unique()
    n_ventilated = int(len(vent_pids))
    if n_ventilated == 0:
        raise PipelineError("No ventilator rows in the EMR; nothing to scan.")
    n_shards = max(1, -(-n_ventilated // max(1, int(cases_per_shard))))

    tmp = out_path / "_corpus_scan_tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    try:
        progress(f"Splitting EMR tables into {n_shards} shard(s)…")
        for fname, key in (
            ("patient_ventilator.csv", "vent"),
            ("patient_vitals.csv", "vitals"),
            ("patient_procedure_events.csv", "events"),
        ):
            _split_csv(emr_path / fname, tmp, key, n_shards)

        case_frames: list[pd.DataFrame] = []
        episode_frames: list[pd.DataFrame] = []
        failed: list[dict] = []
        scanned_pids = 0
        for shard in range(n_shards):
            if should_stop is not None and should_stop():
                raise ScanCancelled("Full-EMR scan cancelled; the previous scan was kept.")
            shard_dir = tmp / str(shard)
            progress(f"Scoring shard {shard + 1} of {n_shards}…", shard, n_shards)
            vent = _read_shard(shard_dir / "vent.csv", VENT_RENAME)
            if vent.empty:
                continue
            pids = set(vent["PID"].unique())
            cases_raw = info[info["PID"].isin(pids)].copy()
            if cases_raw.empty:
                continue
            # Same surgeries as the ventilator shard; vitals-only surgeries in
            # this shard would otherwise pass the window filter unmatched
            vitals = _read_shard(shard_dir / "vitals.csv", VITALS_RENAME)
            events = _read_shard(shard_dir / "events.csv")
            if not vitals.empty:
                vitals = vitals[vitals["PID"].isin(pids)]
            if not events.empty:
                events = events[events["PID"].isin(pids)]
            try:
                frames = process_frames(
                    cases_raw,
                    vent,
                    vitals,
                    events,
                    thresholds,
                    pad_minutes=pad_minutes,
                    validate=False,
                    verbose=False,
                )
            except (GuardrailError, PipelineError, ValueError, KeyError) as exc:
                failed.append({"shard": shard, "n_pids": len(pids), "error": str(exc)[:300]})
                continue
            cases = frames["cases"]
            case_frames.append(cases)
            if not frames["episodes"].empty:
                episode_frames.append(frames["episodes"])
            scanned_pids += int(cases["PID"].nunique())
            # Minute-level frames for this shard are released here
            del frames, vent
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if not case_frames:
        raise PipelineError("The full-EMR scan produced no scored cases.")
    corpus_cases = (
        pd.concat(case_frames, ignore_index=True)
        .sort_values("anomaly_score", ascending=False)
        .reset_index(drop=True)
    )
    corpus_episodes = (
        pd.concat(episode_frames, ignore_index=True) if episode_frames else pd.DataFrame()
    )
    meta = {
        "scope": "full_emr_scan",
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "preset": preset,
        "thresholds_hash": _thresholds_hash(thresholds),
        "n_indexed_surgeries": n_indexed,
        "n_ventilated_surgeries": n_ventilated,
        "n_scanned_cases": int(corpus_cases["PID"].nunique()),
        "n_shards": n_shards,
        "failed_shards": failed,
        "seconds": round(time.monotonic() - started, 1),
    }
    atomic_write_parquet(corpus_cases, out_path / CORPUS_CASES)
    atomic_write_parquet(corpus_episodes, out_path / CORPUS_EPISODES)
    atomic_write_json(meta, out_path / CORPUS_META)
    progress(
        f"Full-EMR scan done: {meta['n_scanned_cases']} of {n_ventilated} ventilated "
        f"surgeries scored in {meta['seconds']} s.",
        n_shards,
        n_shards,
    )
    return ScanResult(corpus_cases, corpus_episodes, meta)


def load_corpus_scan(processed_dir: Path | str) -> ScanResult | None:
    """Read a previous scan from *processed_dir*, or None if there is none."""
    root = Path(processed_dir)
    if not (root / CORPUS_CASES).is_file() or not (root / CORPUS_META).is_file():
        return None
    cases = pd.read_parquet(root / CORPUS_CASES)
    episodes = (
        pd.read_parquet(root / CORPUS_EPISODES)
        if (root / CORPUS_EPISODES).is_file()
        else pd.DataFrame()
    )
    meta = json.loads((root / CORPUS_META).read_text(encoding="utf-8"))
    return ScanResult(cases, episodes, meta)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Score every ventilated surgery in the EMR (bounded memory)"
    )
    parser.add_argument("--emr-dir", type=str, default=None)
    parser.add_argument("--output-dir", type=str, default=None)
    parser.add_argument(
        "--preset", type=str, default="default", choices=["default", "strict", "lenient"]
    )
    parser.add_argument("--cases-per-shard", type=int, default=DEFAULT_CASES_PER_SHARD)
    args = parser.parse_args(argv)
    try:
        result = scan_corpus(
            args.emr_dir,
            args.output_dir,
            preset=args.preset,
            cases_per_shard=args.cases_per_shard,
        )
    except GuardrailError as e:
        logger.error("Scan blocked by guardrail: %s", e)
        raise SystemExit(2) from e
    print(json.dumps(result.meta, indent=2))


if __name__ == "__main__":
    main()
