"""
On-demand case retrieval over the whole EMR.

The processed cache only ever holds the cases someone has actually looked at.
Retrieval instead works in two stages:

1. **Index** — ``patient_information.csv`` (~4 MB, every surgery) gives procedure
   text, age and gender for free. No pipeline, no flags, no waiting.
2. **Fetch** — only the shortlist from stage 1 is put through the pipeline, which
   is where flags/timeseries come from. Batch cost is ~2 s fixed (CSV scan) plus
   ~0.04 s per case, so a few hundred candidates land in ~10 s.

Results are memoised per EMR, surgery, preset, and threshold hash. A fetch
keeps its own copies, and it stores them only when the data generation is
still the one it started in. Nothing here writes to the user's
processed directory — the pipeline's own output goes to a scratch dir that is
discarded, and callers decide what to persist.
"""

from __future__ import annotations

import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from src.guardrails.limits import MAX_N_CASES
from src.runtime_paths import emr_dir, wave_dir, waveform_case_dir

_FRAME_KEYS = ("cases", "timeseries", "flags", "episodes", "events")

# Module-level memo: parsed EMR index, PIDs that have ventilator rows, and
# per-source pipeline output. The case key includes the EMR path, preset, and
# threshold hash so the same surgery id in two folders cannot share frames.
_index_cache: dict[str, pd.DataFrame] = {}
_vent_pid_cache: dict[str, set[str]] = {}
_case_cache: dict[tuple[str, str, str, str], tuple[dict[str, pd.DataFrame], str]] = {}
cache_lock = threading.Lock()


@dataclass
class FetchResult:
    """Pipeline frames for the requested PIDs, plus which ones were newly computed."""

    cases: pd.DataFrame = field(default_factory=pd.DataFrame)
    timeseries: pd.DataFrame = field(default_factory=pd.DataFrame)
    flags: pd.DataFrame = field(default_factory=pd.DataFrame)
    episodes: pd.DataFrame = field(default_factory=pd.DataFrame)
    events: pd.DataFrame = field(default_factory=pd.DataFrame)
    computed: list[str] = field(default_factory=list)
    reused: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    thresholds_sha256_by_pid: dict[str, str] = field(default_factory=dict)

    @property
    def pids(self) -> list[str]:
        if self.cases.empty or "PID" not in self.cases.columns:
            return []
        return [str(p) for p in self.cases["PID"].tolist()]


def discard_stored_caches() -> None:
    """Drop memoised index and fetch state. Caller holds ``cache_lock``."""
    _index_cache.clear()
    _vent_pid_cache.clear()
    _case_cache.clear()


def clear_caches() -> None:
    """Drop memoised index/fetch state (call after EMR path or preset changes)."""
    with cache_lock:
        discard_stored_caches()


def _generation() -> int:
    from src.services.data_context import current_context

    return current_context().generation


def _case_key(emr: Path | str | None, pid: str, preset: str, thresholds_hash: str) -> tuple[str, str, str, str]:
    """Source, surgery, preset, and threshold hash. The hash is the processing version."""
    return (str(_emr_path(emr).expanduser().resolve()), pid, preset, thresholds_hash)


def _emr_path(emr: Path | str | None = None) -> Path:
    return Path(emr) if emr is not None else emr_dir()


def vent_capable_pids(emr: Path | str | None = None) -> set[str]:
    """
    PIDs with ventilator rows — the only cases that can be flagged at all.

    Reads a single column, so the 70 MB+ ventilator file costs well under a second.
    """
    root = _emr_path(emr)
    key = str(root)
    with cache_lock:
        cached = _vent_pid_cache.get(key)
        if cached is not None:
            return cached
        started = _generation()
    path = root / "patient_ventilator.csv"
    if not path.is_file():
        found: set[str] = set()
    else:
        try:
            col = pd.read_csv(path, usecols=["PID"], dtype=str)["PID"]
            found = {str(p) for p in col.dropna().unique()}
        except (OSError, ValueError, KeyError):
            found = set()
    with cache_lock:
        if _generation() == started:
            _vent_pid_cache[key] = found
            return _vent_pid_cache[key]
    return found


def emr_index(emr: Path | str | None = None, *, with_wave: bool = False) -> pd.DataFrame:
    """
    Every surgery in the EMR with the fields needed to shortlist candidates.

    Adds ``has_vent`` (flaggable). ``with_wave`` additionally probes the waveform
    tree, which costs one filesystem lookup per surgery (~19k) — off by default,
    since parsing the whole index otherwise takes about 40 ms.
    """
    root = _emr_path(emr)
    key = f"{root}|wave={with_wave}"
    with cache_lock:
        cached = _index_cache.get(key)
        if cached is not None:
            return cached
        if not with_wave:
            with_wave_hit = _index_cache.get(f"{root}|wave=True")
            if with_wave_hit is not None:
                return with_wave_hit
        started = _generation()

    path = root / "patient_information.csv"
    if not path.is_file():
        df = pd.DataFrame()
        with cache_lock:
            if _generation() == started:
                _index_cache[key] = df
        return df

    df = pd.read_csv(path, dtype=str)
    if "PID" not in df.columns:
        df = pd.DataFrame()
        with cache_lock:
            if _generation() == started:
                _index_cache[key] = df
        return df
    df["PID"] = df["PID"].astype(str)
    for col in ("Age", "Ht", "Wt"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "Procedure" not in df.columns:
        df["Procedure"] = ""
    df["Procedure"] = df["Procedure"].fillna("")

    vent = vent_capable_pids(root)
    df["has_vent"] = df["PID"].isin(vent)

    if with_wave:
        wdir = wave_dir()
        df["has_wave"] = (
            [waveform_case_dir(pid, wdir) is not None for pid in df["PID"]]
            if wdir is not None
            else False
        )

    with cache_lock:
        if _generation() == started:
            _index_cache[key] = df
    return df


def search_emr(
    *,
    text: str | None = None,
    age: float | None = None,
    age_tolerance: float = 10.0,
    gender: str | None = None,
    require_vent: bool = True,
    require_wave: bool = False,
    limit: int = 50,
    emr: Path | str | None = None,
) -> pd.DataFrame:
    """
    Shortlist candidate surgeries from the index — no pipeline run involved.

    ``text`` matches all whitespace-separated terms against the procedure text,
    falling back to any-term matching when nothing matches every term.
    """
    df = emr_index(emr, with_wave=require_wave)
    if df.empty:
        return df

    if require_vent and "has_vent" in df.columns:
        df = df[df["has_vent"]]
    if require_wave and "has_wave" in df.columns:
        df = df[df["has_wave"]]

    if text:
        terms = [t for t in str(text).lower().split() if len(t) > 2]
        if terms:
            proc = df["Procedure"].str.lower()
            all_hit = df[[all(t in p for t in terms) for p in proc]]
            df = all_hit if not all_hit.empty else df[
                [any(t in p for t in terms) for p in proc]
            ]

    if gender:
        g = str(gender).strip().upper()[:1]
        if g in {"M", "F"} and "Gender" in df.columns:
            df = df[df["Gender"].astype(str).str.upper().str[:1] == g]

    if age is not None and "Age" in df.columns:
        near = df[(df["Age"] - float(age)).abs() <= float(age_tolerance)]
        if not near.empty:
            df = near.assign(_age_gap=(near["Age"] - float(age)).abs()).sort_values(
                "_age_gap"
            ).drop(columns="_age_gap")

    return df.head(max(1, int(limit)))


def _run_for(pids: list[str], preset: str, emr: Path | str | None) -> dict[str, pd.DataFrame]:
    """Run the pipeline for *pids* into a scratch dir; return the frames only."""
    from src.pipeline.run import run_pipeline

    root = _emr_path(emr)
    with tempfile.TemporaryDirectory(prefix="mover-fetch-") as tmp:
        import os

        prior = os.environ.get("MOVER_ALLOW_EXTERNAL_OUTPUT")
        os.environ["MOVER_ALLOW_EXTERNAL_OUTPUT"] = "1"
        try:
            return run_pipeline(
                emr_dir=root,
                output_dir=Path(tmp),
                pids=list(pids),
                preset=preset,
                write_sample_csv=False,
                validate=False,
            )
        finally:
            if prior is None:
                os.environ.pop("MOVER_ALLOW_EXTERNAL_OUTPUT", None)
            else:
                os.environ["MOVER_ALLOW_EXTERNAL_OUTPUT"] = prior


def _split_by_pid(frames: dict[str, pd.DataFrame], pid: str) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for key in _FRAME_KEYS:
        df = frames.get(key)
        if df is None or df.empty or "PID" not in df.columns:
            out[key] = pd.DataFrame()
        else:
            out[key] = df[df["PID"].astype(str) == pid].copy()
    return out


def fetch_cases(
    pids: list[str] | tuple[str, ...],
    *,
    preset: str = "default",
    emr: Path | str | None = None,
    use_cache: bool = True,
) -> FetchResult:
    """
    Flag *pids* on demand, reusing a result only for the same EMR, preset, and threshold hash.

    PIDs without ventilator rows are reported in ``skipped`` rather than failing
    the batch — the pipeline raises if a run has no ventilator data at all.
    """
    wanted: list[str] = []
    for p in pids:
        s = str(p).strip()
        if s and s not in wanted:
            wanted.append(s)
    if not wanted:
        return FetchResult()

    result = FetchResult()
    # Pin the source for this call. emr_dir() can change while the pipeline runs.
    root = _emr_path(emr).expanduser().resolve()
    vent = vent_capable_pids(root)
    if vent:
        result.skipped = [p for p in wanted if p not in vent]
        wanted = [p for p in wanted if p in vent]

    from src.rules import thresholds_sha256

    computed_hash = thresholds_sha256()
    todo: list[str] = []
    held: dict[str, tuple[dict[str, pd.DataFrame], str]] = {}
    with cache_lock:
        started = _generation()
        for pid in wanted:
            cache_key = _case_key(root, pid, preset, computed_hash)
            cached = _case_cache.get(cache_key) if use_cache else None
            if cached is not None:
                held[pid] = cached
                result.reused.append(pid)
            else:
                todo.append(pid)

    # Guardrail: never hand the pipeline more than it allows in one call
    if len(todo) > MAX_N_CASES:
        result.skipped.extend(todo[MAX_N_CASES:])
        todo = todo[:MAX_N_CASES]

    if todo:
        frames = _run_for(todo, preset, root)
        produced = set()
        cases = frames.get("cases")
        if cases is not None and not cases.empty and "PID" in cases.columns:
            produced = {str(p) for p in cases["PID"]}
        computed: dict[str, tuple[dict[str, pd.DataFrame], str]] = {}
        for pid in todo:
            if pid in produced:
                computed[pid] = (_split_by_pid(frames, pid), computed_hash)
                result.computed.append(pid)
            else:
                result.skipped.append(pid)
        with cache_lock:
            if _generation() == started:
                for pid, payload in computed.items():
                    _case_cache[_case_key(root, pid, preset, computed_hash)] = payload
        held.update(computed)

    result.thresholds_sha256_by_pid = {pid: held[pid][1] for pid in result.reused + result.computed}
    for frame_name in _FRAME_KEYS:
        parts = [
            held[pid][0][frame_name]
            for pid in result.reused + result.computed
            if not held[pid][0][frame_name].empty
        ]
        setattr(result, frame_name, pd.concat(parts, ignore_index=True) if parts else pd.DataFrame())

    if not result.cases.empty and "anomaly_score" in result.cases.columns:
        result.cases = result.cases.sort_values(
            "anomaly_score", ascending=False
        ).reset_index(drop=True)
    return result


def find_and_fetch(
    *,
    text: str | None = None,
    age: float | None = None,
    gender: str | None = None,
    limit: int = 25,
    preset: str = "default",
    emr: Path | str | None = None,
) -> FetchResult:
    """Index shortlist → on-demand flagging, in one call."""
    shortlist = search_emr(
        text=text, age=age, gender=gender, limit=limit, emr=emr, require_vent=True
    )
    if shortlist.empty:
        return FetchResult()
    return fetch_cases(
        [str(p) for p in shortlist["PID"]], preset=preset, emr=emr
    )
