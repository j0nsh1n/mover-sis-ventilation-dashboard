"""
Full-EMR scan: score every ventilated surgery without holding all timeseries.

The normal pipeline analyzes a sample (up to 500 cases) and keeps its
minute-level timeseries for charts. This scan covers every surgery with
ventilator rows, but memory stays bounded:

1. Stream ``patient_ventilator.csv``, ``patient_vitals.csv`` and
   ``patient_procedure_events.csv`` in chunks and split their rows by PID
   into shard files on disk.
2. Run the same clean → merge → features → flags code (``process_frames``)
   on each shard, keep only the per-case summary and episodes, and drop the
   shard's timeseries. Shards are independent, so several worker processes
   score them at the same time (``workers``); results are collected in shard
   order, so the output does not depend on the worker count.

Outputs in the processed directory (the sample's files are left untouched):

- ``corpus_cases.parquet``   one row per scanned surgery (scores, top rules)
- ``corpus_episodes.parquet`` contiguous flag runs for every scanned surgery
- ``corpus_meta.json``       scope counts, preset, thresholds hash, timing
- ``corpus_profile.json`` / ``corpus_profile.md``
                             aggregate profile (percentiles, rule and phase
                             counts) merged from per-shard histograms; no PIDs
                             or timestamps
- ``scan-errors.log``        only after a shard fails: full exception text and
                             traceback, which can hold paths and patient data
                             and never leave this file

    PYTHONPATH=. python -m src.pipeline.corpus_scan --preset default --workers 4

Memory: each worker process holds one shard's minute-level frames plus pandas
working copies. Measured on synthetic data (about 25 KB of CSV per surgery), a
300-surgery shard peaks at roughly 250 MB per process, of which about 100 MB is
the interpreter and pandas. Real surgeries have more rows than the synthetic
ones, so plan for 1 to 2 GB per worker until this is measured on real data. The
default therefore uses at most ``MAX_DEFAULT_WORKERS`` (6) workers, which is
under about 12 GB in that pessimistic case; lower ``workers`` or
``cases_per_shard`` on a smaller machine.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import multiprocessing
import os
import re
import shutil
import sys
import time
import traceback
import zlib
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

import pandas as pd

from src.config import load_thresholds, thresholds_preset_hash
from src.rules import thresholds_sha256
from src.guardrails.exceptions import GuardrailError, PipelineError
from src.guardrails.validate_io import (
    atomic_write_json,
    atomic_write_parquet,
    atomic_write_text,
    resolve_emr_dir,
    resolve_output_dir,
    validate_emr_dir,
)
from src.pipeline.load import NA_VALUES, VENT_RENAME, VITALS_RENAME, load_case_info
from src.pipeline.phases import PHASE_SOURCES
from src.pipeline.run import process_frames
from src.pipeline.scan_profile import (
    build_corpus_profile,
    merge_histograms,
    render_corpus_profile_md,
    shard_histogram,
)

logger = logging.getLogger(__name__)

CORPUS_CASES = "corpus_cases.parquet"
CORPUS_EPISODES = "corpus_episodes.parquet"
CORPUS_META = "corpus_meta.json"
CORPUS_PROFILE_JSON = "corpus_profile.json"
CORPUS_PROFILE_MD = "corpus_profile.md"
SCAN_ERROR_LOG = "scan-errors.log"

# Surgeries per shard: large enough to amortize pandas overhead, small enough
# that one shard's minute-level frames stay well under a few hundred MB.
DEFAULT_CASES_PER_SHARD = 300
READ_CHUNK_ROWS = 200_000
# Upper bound for the automatic worker count (see the memory note above)
MAX_DEFAULT_WORKERS = 6
# How often the parent checks should_stop while workers run
STOP_POLL_SECONDS = 0.25

ProgressCb = Callable[[str, int, int], None]


class ScanCancelled(PipelineError):
    """The caller asked the scan to stop."""


class ShardFailure(PipelineError):
    """A shard could not be scored.

    The message is fixed text plus a code, safe to show anywhere; the original
    exception text and traceback are in ``detail`` and go only to the log.
    """

    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.detail = detail


# Pipeline module that raised -> stage in the failure code
_STAGE_BY_MODULE = {
    "load": "load",
    "clean": "clean",
    "merge": "merge",
    "phases": "features",
    "features": "features",
    "flags": "flags",
    "scan_profile": "profile",
}
_STAGE_BY_SHARD_FUNCTION = {"_read_shard": "load", "_split_csv": "load"}
_TRACEBACK_FRAME = re.compile(r'File "([^"]+)", line \d+, in (\w+)')


def _failure_code(trace: str, exc: BaseException) -> str:
    """``<stage>:<ExceptionClass>`` from the innermost pipeline frame, else ``scan``."""
    stage = "scan"
    for filename, function in _TRACEBACK_FRAME.findall(trace):
        path = Path(filename.replace("\\", "/"))
        if path.parent.name == "pipeline" and path.stem in _STAGE_BY_MODULE:
            stage = _STAGE_BY_MODULE[path.stem]
        elif path.stem == "corpus_scan" and function in _STAGE_BY_SHARD_FUNCTION:
            stage = _STAGE_BY_SHARD_FUNCTION[function]
    return f"{stage}:{type(exc).__name__}"


def _failure_message(shard: int, code: str) -> str:
    return f"Shard {shard + 1} could not be scored ({code}). Details are in {SCAN_ERROR_LOG}."


def append_scan_error_log(out_dir: Path | str, entries: list[tuple[str, str]]) -> None:
    """Append ``(heading, detail)`` entries to the debug log; owner-only where supported."""
    path = Path(out_dir) / SCAN_ERROR_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        for heading, detail in entries:
            handle.write(f"[{stamp}] {heading}\n{detail.rstrip()}\n\n")


def scan_failure_message(exc: BaseException, out_dir: Path | str) -> str:
    """Text the app may show for a failed scan; the full detail goes to the log.

    A ShardFailure was already logged by ``scan_corpus``. Any other exception
    (a bad EMR folder, an unreadable file) is logged here.
    """
    if isinstance(exc, ShardFailure):
        return str(exc)
    trace = "".join(traceback.format_exception(exc))
    code = _failure_code(trace, exc)
    try:
        append_scan_error_log(out_dir, [(f"scan failed ({code})", trace)])
    except OSError:
        logger.exception("Could not write %s", SCAN_ERROR_LOG)
    return f"The full-EMR scan could not finish ({code}). Details are in {SCAN_ERROR_LOG}."


@dataclass
class ScanResult:
    cases: pd.DataFrame
    episodes: pd.DataFrame
    meta: dict


def _shard_of(pid: str, n_shards: int) -> int:
    return zlib.crc32(pid.encode("utf-8")) % n_shards


def _split_csv(
    src: Path,
    dest_dir: Path,
    name: str,
    n_shards: int,
    check_stop: Callable[[], None] | None = None,
) -> int:
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
        if check_stop is not None:
            check_stop()
        chunk.columns = [c.strip().strip('"') for c in chunk.columns]
        if "PID" not in chunk.columns:
            raise PipelineError(f"{src.name} has no PID column")
        # crc32 once per distinct PID in the chunk, not once per row
        pids = chunk["PID"].astype(str)
        shard = pids.map({p: _shard_of(p, n_shards) for p in pids.unique()})
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


def default_workers(n_shards: int = MAX_DEFAULT_WORKERS) -> int:
    """One core is left for the desktop / OS; never more workers than shards."""
    cores = os.cpu_count() or 1
    return max(1, min(cores - 1, n_shards, MAX_DEFAULT_WORKERS))


def _peak_rss_mb() -> dict[str, float | None]:
    """Process-lifetime high-water RSS; children reports the largest reaped child.

    A serial CLI scan runs in the parent, so its worker RSS is zero. Repeated
    scans in one process can retain peaks from earlier work.
    """
    try:
        import resource
    except ImportError:
        return {"parent": None, "largest_worker": None}
    scale = 1024 * 1024 if sys.platform == "darwin" else 1024
    return {
        "parent": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / scale, 1),
        "largest_worker": round(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / scale, 1),
    }


def _score_shard(
    shard: int,
    shard_dir: str,
    cases_info: pd.DataFrame,
    thresholds: dict,
    pad_minutes: float,
) -> dict:
    """Score one shard; runs in the parent (workers=1) or in a worker process.

    Top-level and given only picklable arguments so a spawned process can
    import and call it. Returns the small per-case and episode tables and the
    mergeable histogram of the shard, never the minute-level frames. A shard that cannot be scored for data reasons
    comes back with ``error`` (a fixed code message) and ``detail`` (full text,
    for the log only) set; any other exception propagates.
    """
    path = Path(shard_dir)
    empty: dict = {
        "shard": shard,
        "cases": None,
        "episodes": None,
        "histogram": None,
        "error": None,
        "detail": None,
        "n_pids": 0,
    }
    vent = _read_shard(path / "vent.csv", VENT_RENAME)
    if vent.empty:
        return empty
    pids = set(vent["PID"].unique())
    cases_raw = cases_info[cases_info["PID"].isin(pids)].copy()
    if cases_raw.empty:
        return empty
    # Same surgeries as the ventilator shard; vitals-only surgeries in
    # this shard would otherwise pass the window filter unmatched
    vitals = _read_shard(path / "vitals.csv", VITALS_RENAME)
    events = _read_shard(path / "events.csv")
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
        histogram = shard_histogram(
            frames["timeseries"], frames["flags"], frames["episodes"], thresholds
        )
    except (GuardrailError, PipelineError, ValueError, KeyError) as exc:
        trace = "".join(traceback.format_exception(exc))
        return {
            **empty,
            "error": _failure_message(shard, _failure_code(trace, exc)),
            "detail": trace,
            "n_pids": len(pids),
        }
    episodes = frames["episodes"]
    return {
        **empty,
        "cases": frames["cases"],
        "episodes": None if episodes.empty else episodes,
        "histogram": histogram,
    }


def _shard_error(shard: int, exc: BaseException) -> ShardFailure:
    trace = "".join(traceback.format_exception(exc))
    detail = (
        f"{trace}\nIf a worker process was killed, the machine may have run out of "
        "memory; retry with fewer workers or a smaller cases_per_shard."
    )
    return ShardFailure(_failure_message(shard, _failure_code(trace, exc)), detail)


def _run_serial(
    jobs: list[tuple],
    progress: Callable[..., None],
    should_stop: Callable[[], bool] | None,
) -> dict[int, dict]:
    results: dict[int, dict] = {}
    n_shards = len(jobs)
    for done, job in enumerate(jobs):
        if should_stop is not None and should_stop():
            raise ScanCancelled("Full-EMR scan cancelled; the previous scan was kept.")
        progress(f"Scoring shard {done + 1} of {n_shards}…", done, n_shards)
        try:
            results[job[0]] = _score_shard(*job)
        except Exception as exc:
            raise _shard_error(job[0], exc) from None
    return results


def _run_parallel(
    jobs: list[tuple],
    workers: int,
    progress: Callable[..., None],
    should_stop: Callable[[], bool] | None,
) -> dict[int, dict]:
    """Score shards in a spawn-started process pool, polling should_stop here.

    ``spawn`` (not fork) so Linux and Windows behave the same, a frozen app
    re-launches itself cleanly, and no Qt state is copied into workers. On
    cancel or error the workers are terminated at once instead of finishing
    their current shard.
    """
    n_shards = len(jobs)
    results: dict[int, dict] = {}
    executor = ProcessPoolExecutor(
        max_workers=workers, mp_context=multiprocessing.get_context("spawn")
    )
    futures: dict[Future, int] = {}
    finished_cleanly = False
    try:
        futures = {executor.submit(_score_shard, *job): job[0] for job in jobs}
        pending = set(futures)
        progress(f"Scoring {n_shards} shard(s) on {workers} CPU cores…", 0, n_shards)
        while pending:
            if should_stop is not None and should_stop():
                raise ScanCancelled("Full-EMR scan cancelled; the previous scan was kept.")
            done, pending = wait(pending, timeout=STOP_POLL_SECONDS, return_when=FIRST_COMPLETED)
            for future in done:
                shard = futures[future]
                try:
                    results[shard] = future.result()
                except Exception as exc:
                    raise _shard_error(shard, exc) from None
            if done:
                progress(
                    f"Scored {len(results)} of {n_shards} shard(s) on {workers} CPU cores…",
                    len(results),
                    n_shards,
                )
        finished_cleanly = True
    finally:
        if finished_cleanly:
            executor.shutdown(wait=True)
        else:
            # Cancel queued shards and stop running ones now rather than
            # letting each finish its current shard (up to about a minute)
            for future in futures:
                future.cancel()
            executor.terminate_workers()
    return results


def _remove_tree(path: Path) -> None:
    """Delete *path*; retry briefly because terminated workers may still hold files (Windows)."""
    for _ in range(25):
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return
        time.sleep(0.2)


def scan_corpus(
    emr_dir: Path | str | None = None,
    output_dir: Path | str | None = None,
    *,
    preset: str = "default",
    cases_per_shard: int = DEFAULT_CASES_PER_SHARD,
    pad_minutes: float = 5.0,
    workers: int | None = None,
    on_progress: ProgressCb | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> ScanResult:
    """Score every ventilated surgery in the EMR, shard by shard.

    ``workers`` is the number of worker processes scoring shards at once;
    None picks ``default_workers`` (CPU cores minus one, at most
    ``MAX_DEFAULT_WORKERS``). ``workers=1`` scores in this process unless
    ``should_stop`` is supplied, which requires a cancellable process pool.
    The result does not depend on the worker count.

    With a process pool, a script that calls this must be guarded by
    ``if __name__ == "__main__":`` (the ``spawn`` start method re-imports it).

    ``on_progress`` and ``should_stop`` are only ever called from the calling
    thread. ``should_stop`` is polled while splitting and while shards are
    scored; when it returns True the scan terminates its workers, removes its
    temporary shards and raises ScanCancelled. Nothing is written in that
    case, so a previous scan stays in place.
    """

    def progress(message: str, done: int = 0, total: int = 0) -> None:
        logger.info(message)
        if on_progress is not None:
            on_progress(message, done, total)

    started = time.monotonic()
    emr_path = validate_emr_dir(emr_dir if emr_dir is not None else resolve_emr_dir(None))
    out_path = resolve_output_dir(output_dir, create=True)
    thresholds = load_thresholds(preset, validate=True)
    file_hash = thresholds_sha256()

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
    if workers is not None and workers < 1:
        raise PipelineError(f"workers must be at least 1 (got {workers}).")
    n_workers = min(workers, n_shards) if workers is not None else default_workers(n_shards)
    if workers is not None and workers > n_shards:
        progress(f"Requested {workers} workers; using {n_workers} for {n_shards} shard(s).")

    def check_stop() -> None:
        if should_stop is not None and should_stop():
            raise ScanCancelled("Full-EMR scan cancelled; the previous scan was kept.")

    tmp = out_path / "_corpus_scan_tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    try:
        split_started = time.monotonic()
        progress(f"Splitting EMR tables into {n_shards} shard(s)…")
        for fname, key in (
            ("patient_ventilator.csv", "vent"),
            ("patient_vitals.csv", "vitals"),
            ("patient_procedure_events.csv", "events"),
        ):
            _split_csv(emr_path / fname, tmp, key, n_shards, check_stop)
        seconds_split = time.monotonic() - split_started

        score_started = time.monotonic()
        info_pids = info["PID"].astype(str)
        info_shard = info_pids.map({p: _shard_of(p, n_shards) for p in info_pids.unique()})
        jobs = [
            (
                shard,
                str(tmp / str(shard)),
                info[info_shard == shard].reset_index(drop=True),
                thresholds,
                pad_minutes,
            )
            for shard in range(n_shards)
        ]
        try:
            if n_workers == 1 and should_stop is None:
                results = _run_serial(jobs, progress, should_stop)
            else:
                results = _run_parallel(jobs, n_workers, progress, should_stop)
        except ShardFailure as failure:
            append_scan_error_log(out_path, [(failure.args[0], failure.detail)])
            raise
        seconds_score = time.monotonic() - score_started
    finally:
        _remove_tree(tmp)

    # Shard order, not completion order, so the output is the same for any worker count
    case_frames: list[pd.DataFrame] = []
    episode_frames: list[pd.DataFrame] = []
    failed: list[dict] = []
    failure_log: list[tuple[str, str]] = []
    histograms: list[dict] = []
    for shard in range(n_shards):
        res = results[shard]
        if res["error"] is not None:
            failed.append({"shard": shard, "n_pids": res["n_pids"], "error": res["error"]})
            failure_log.append((res["error"], res["detail"] or ""))
            continue
        if res["cases"] is not None:
            case_frames.append(res["cases"])
        if res["episodes"] is not None:
            episode_frames.append(res["episodes"])
        if res["histogram"] is not None:
            histograms.append(res["histogram"])

    if failure_log:
        append_scan_error_log(out_path, failure_log)
    if not case_frames:
        raise PipelineError(
            "The full-EMR scan produced no scored cases."
            + (f" Details are in {SCAN_ERROR_LOG}." if failure_log else "")
        )
    corpus_cases = (
        pd.concat(case_frames, ignore_index=True)
        .sort_values(["anomaly_score", "PID"], ascending=[False, True], kind="stable")
        .reset_index(drop=True)
    )
    corpus_episodes = (
        pd.concat(episode_frames, ignore_index=True) if episode_frames else pd.DataFrame()
    )
    cases_left_out = sum(f["n_pids"] for f in failed)
    profile = build_corpus_profile(
        merge_histograms(histograms),
        thresholds,
        n_cases_left_out=cases_left_out,
        n_failed_shards=len(failed),
    )
    meta = {
        "scope": "full_emr_scan",
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "preset": preset,
        "thresholds_hash": thresholds_preset_hash(thresholds),
        "thresholds_sha256": file_hash,
        "emr_fingerprint": emr_fingerprint(emr_path),
        "n_indexed_surgeries": n_indexed,
        "n_ventilated_surgeries": n_ventilated,
        "n_scanned_cases": int(corpus_cases["PID"].nunique()),
        "n_shards": n_shards,
        "failed_shards": failed,
        "cases_left_out_by_failed_shards": cases_left_out,
        "workers_requested": workers,
        "workers": n_workers,
        "cases_by_phase_source": {
            source: int(corpus_cases["phase_source"].eq(source).sum())
            for source in PHASE_SOURCES
        },
        "seconds_split": round(seconds_split, 1),
        "seconds_score": round(seconds_score, 1),
        "seconds": round(time.monotonic() - started, 1),
    }
    if should_stop is not None and should_stop():
        raise ScanCancelled("Full-EMR scan cancelled; the previous scan was kept.")
    atomic_write_parquet(corpus_cases, out_path / CORPUS_CASES)
    atomic_write_parquet(corpus_episodes, out_path / CORPUS_EPISODES)
    atomic_write_json(profile, out_path / CORPUS_PROFILE_JSON)
    atomic_write_text(render_corpus_profile_md(profile, file_hash), out_path / CORPUS_PROFILE_MD)
    meta["peak_rss_mb"] = _peak_rss_mb()
    atomic_write_json(meta, out_path / CORPUS_META)
    progress(
        f"Full-EMR scan done: {meta['n_scanned_cases']} of {n_ventilated} ventilated "
        f"surgeries scored in {meta['seconds']} s using {n_workers} CPU core(s).",
        n_shards,
        n_shards,
    )
    return ScanResult(corpus_cases, corpus_episodes, meta)


def emr_fingerprint(emr_dir: Path | str) -> str:
    """Identify an EMR folder by its resolved path and its patient_information.csv.

    The path, size and modification time are hashed, so a scan's meta names no
    folder. A different folder, or the same folder with a replaced EMR, changes it.
    """
    root = Path(emr_dir).expanduser().resolve()
    stat = (root / "patient_information.csv").stat()
    key = f"{root}\0{stat.st_size}\0{stat.st_mtime_ns}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def scan_matches_emr(meta: dict, emr_dir: Path | str) -> bool:
    """False only when *meta* records a different EMR than *emr_dir*.

    Scans written before the fingerprint existed, and an EMR folder that cannot
    be read right now, are not treated as a mismatch.
    """
    recorded = meta.get("emr_fingerprint")
    if not isinstance(recorded, str):
        return True
    try:
        return emr_fingerprint(emr_dir) == recorded
    except OSError:
        return True


def load_corpus_scan(
    processed_dir: Path | str, emr_dir: Path | str | None = None
) -> ScanResult | None:
    """Read a previous scan from *processed_dir*, or None if there is none.

    With *emr_dir*, a scan recorded for a different EMR is also None.
    """
    root = Path(processed_dir)
    if not (root / CORPUS_CASES).is_file() or not (root / CORPUS_META).is_file():
        return None
    meta = json.loads((root / CORPUS_META).read_text(encoding="utf-8"))
    if emr_dir is not None and not scan_matches_emr(meta, emr_dir):
        return None
    cases = pd.read_parquet(root / CORPUS_CASES)
    episodes = (
        pd.read_parquet(root / CORPUS_EPISODES)
        if (root / CORPUS_EPISODES).is_file()
        else pd.DataFrame()
    )
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
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="worker processes scoring shards (default: CPU cores - 1, at most "
        f"{MAX_DEFAULT_WORKERS}; 1 = single process)",
    )
    args = parser.parse_args(argv)
    try:
        result = scan_corpus(
            args.emr_dir,
            args.output_dir,
            preset=args.preset,
            cases_per_shard=args.cases_per_shard,
            workers=args.workers,
        )
    except GuardrailError as e:
        logger.error("Scan blocked by guardrail: %s", e)
        raise SystemExit(2) from e
    print(json.dumps(result.meta, indent=2))


if __name__ == "__main__":
    main()
