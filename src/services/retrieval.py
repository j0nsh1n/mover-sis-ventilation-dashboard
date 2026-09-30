"""Case-retrieval engine for the desktop Ask / G workspace.

Public API (import these from the UI, not embedding internals)::

    from src.services.retrieval import (
        CaseFilters,
        RetrievalEngine,
        RetrievalPhase,
        RetrievalProgress,
        RetrievalResult,
        SearchCandidate,
        DEFAULT_CANDIDATE_LIMIT,
        DEFAULT_EXTRACT_LIMIT,
    )

Typical desktop flow::

    engine = RetrievalEngine(
        emr=emr_dir,
        cache_path=cache_file,
        embedder=OllamaEmbedder(model) if model else None,
        analyzed_pids=loaded_processed_pids,
    )
    engine.prepare(on_progress=set_status)
    hits = engine.search(question, filters=CaseFilters(gender="F"), on_progress=set_status)
    # hits.candidates are source-labeled shortlist rows. Do not show vectors.
    detailed = engine.fetch_extracts(
        [c.pid for c in hits.candidates[:DEFAULT_EXTRACT_LIMIT]],
        on_progress=set_status,
    )

``prepare`` is idempotent: a second call embeds only new or changed source
text, or a different embedding-model id. Indexed surgeries (EMR metadata)
stay distinct from analyzed cases (processed parquet / on-demand fetch).

Vectors choose records. They are never returned on ``SearchCandidate`` and
must not be passed to the chat model. Use ``src.llm.retrieve.answer_from_retrieval``
to hand source extracts to the existing Ollama agent.

This module does not index articles. That source is undecided.
"""

from __future__ import annotations

import hashlib
import math
import sqlite3
import struct
import threading
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Callable, Protocol, Sequence

import numpy as np
import pandas as pd

from src.llm.case_context import build_case_context
from src.services import case_fetch

DEFAULT_CANDIDATE_LIMIT = 10
DEFAULT_EXTRACT_LIMIT = 3
DEFAULT_EMBED_BATCH = 32

ProgressCb = Callable[["RetrievalProgress"], None]


class RetrievalPhase(StrEnum):
    IDLE = "idle"
    PREPARING = "preparing"
    EMBEDDING_QUERY = "embedding_query"
    FILTERING = "filtering"
    RANKING = "ranking"
    FETCHING = "fetching"
    GENERATING = "generating"
    MODEL_UNAVAILABLE = "model_unavailable"
    KEYWORD_FALLBACK = "keyword_fallback"
    DONE = "done"
    ERROR = "error"


class Embedder(Protocol):
    """Local embedding model. Tests inject a fake; production uses Ollama."""

    model_id: str

    def is_available(self) -> bool: ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


@dataclass(frozen=True)
class CaseFilters:
    """Exact metadata filters applied before ranking. Not embedded."""

    age: float | None = None
    age_tolerance: float = 10.0
    gender: str | None = None
    require_vent: bool = False
    analyzed: bool | None = None


@dataclass(frozen=True)
class SearchRecord:
    """One short factual row per indexed surgery."""

    pid: str
    source_text: str
    source_text_hash: str
    procedure: str
    age: float | None
    gender: str | None
    has_vent: bool
    analyzed: bool


@dataclass(frozen=True)
class SearchCandidate:
    """Shortlist row for the UI. Ranking score is not evidence."""

    pid: str
    source_label: str
    analyzed: bool
    procedure: str
    age: float | None
    gender: str | None
    source_text: str
    rank_method: str
    score: float
    # Surgeries without ventilator rows cannot be analyzed or plotted
    has_vent: bool = True


@dataclass(frozen=True)
class RetrievalProgress:
    phase: RetrievalPhase
    message: str
    current: int = 0
    total: int = 0
    model_available: bool = True
    fallback: str | None = None


@dataclass
class RetrievalResult:
    candidates: tuple[SearchCandidate, ...] = ()
    indexed_count: int = 0
    analyzed_count: int = 0
    rank_method: str = "keyword"
    model_available: bool = False
    embedding_model: str | None = None
    progress: RetrievalProgress = field(
        default_factory=lambda: RetrievalProgress(
            phase=RetrievalPhase.IDLE, message=""
        )
    )
    extracts: tuple[str, ...] = ()
    fetched_pids: tuple[str, ...] = ()


def source_text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_case_search_text(
    *,
    pid: str,
    procedure: str,
    age: float | None,
    gender: str | None,
    has_vent: bool,
    analyzed: bool,
) -> str:
    """Stable, factual search document. No flags, scores, or vectors."""
    if age is None or (isinstance(age, float) and (math.isnan(age) or math.isinf(age))):
        age_s = "unknown"
    else:
        age_f = float(age)
        age_s = str(int(age_f)) if age_f.is_integer() else str(age_f)
    sex = (gender or "unknown").strip() or "unknown"
    proc = (procedure or "").strip() or "unspecified"
    vent = "yes" if has_vent else "no"
    scope = (
        "analyzed case"
        if analyzed
        else "indexed surgery, not yet analyzed"
    )
    return (
        f"PID {pid}. Procedure: {proc}. Age: {age_s}. Sex: {sex}. "
        f"Ventilator rows: {vent}. Record scope: {scope}."
    )


def source_label(analyzed: bool, has_vent: bool = True) -> str:
    if analyzed:
        return "analyzed case"
    return "indexed surgery" if has_vent else "indexed surgery · no ventilator data"


def _rank_key(item: tuple[float, "SearchRecord"]) -> tuple[float, bool, bool]:
    """Relevance first; equal scores prefer cases the app can open and plot."""
    score, rec = item
    return (score, rec.has_vent, rec.analyzed)


def prefer_ventilated(candidates: Sequence["SearchCandidate"]) -> list["SearchCandidate"]:
    """Stable reorder putting candidates with ventilator data first."""
    return [c for c in candidates if c.has_vent] + [c for c in candidates if not c.has_vent]


def _emit(cb: ProgressCb | None, progress: RetrievalProgress) -> RetrievalProgress:
    if cb is not None:
        cb(progress)
    return progress


def _pack_vector(values: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(values)}f", *[float(v) for v in values])


def _unpack_vector(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    va = np.asarray(a, dtype=np.float32)
    vb = np.asarray(b, dtype=np.float32)
    if va.size == 0 or vb.size == 0 or va.size != vb.size:
        return 0.0
    na = float(np.linalg.norm(va))
    nb = float(np.linalg.norm(vb))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


def _keyword_score(query: str, text: str) -> float:
    terms = [t for t in str(query).lower().split() if len(t) > 2]
    if not terms:
        return 0.0
    blob = text.lower()
    hits = sum(1 for t in terms if t in blob)
    return hits / len(terms)


def _normalize_gender(value: str | None) -> str | None:
    if not value:
        return None
    g = str(value).strip().upper()[:1]
    return g if g in {"M", "F"} else None


class _EmbeddingCache:
    """SQLite store keyed by source-text hash and embedding-model identity."""

    def __init__(self, path: Path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS embeddings (
                    text_hash TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    dimensions INTEGER NOT NULL,
                    vector BLOB NOT NULL,
                    PRIMARY KEY (text_hash, model_id)
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self._path)
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def get(self, text_hash: str, model_id: str) -> list[float] | None:
        with self._lock, self._connect() as con:
            row = con.execute(
                "SELECT vector FROM embeddings WHERE text_hash = ? AND model_id = ?",
                (text_hash, model_id),
            ).fetchone()
        if row is None:
            return None
        return _unpack_vector(row[0])

    def put(
        self, text_hash: str, model_id: str, vector: Sequence[float]
    ) -> None:
        blob = _pack_vector(vector)
        with self._lock, self._connect() as con:
            con.execute(
                """
                INSERT OR REPLACE INTO embeddings
                    (text_hash, model_id, dimensions, vector)
                VALUES (?, ?, ?, ?)
                """,
                (text_hash, model_id, len(vector), blob),
            )
            con.commit()


class RetrievalEngine:
    """Prepare, search, and fetch bounded case extracts for the desktop UI."""

    def __init__(
        self,
        *,
        emr: Path | str,
        cache_path: Path | str,
        embedder: Embedder | None = None,
        analyzed_pids: Sequence[str] | None = None,
        candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
        extract_limit: int = DEFAULT_EXTRACT_LIMIT,
        preset: str = "default",
    ):
        self.emr = Path(emr)
        self.embedder = embedder
        self.analyzed_pids = {str(p) for p in (analyzed_pids or [])}
        self.candidate_limit = max(1, int(candidate_limit))
        self.extract_limit = max(1, int(extract_limit))
        self.preset = preset
        self._cache = _EmbeddingCache(Path(cache_path))
        self._records: list[SearchRecord] = []
        self._vectors: dict[str, list[float]] = {}

    def model_available(self) -> bool:
        if self.embedder is None:
            return False
        try:
            return bool(self.embedder.is_available())
        except Exception:
            return False

    def prepare(self, on_progress: ProgressCb | None = None) -> int:
        """Build one search record per indexed surgery and cache new embeddings.

        Returns the number of vectors written this call (0 on a cache hit).
        """
        available = self.model_available()
        index = case_fetch.emr_index(self.emr)
        records = self._records_from_index(index)
        self._records = records
        self._vectors = {}
        _emit(
            on_progress,
            RetrievalProgress(
                phase=RetrievalPhase.PREPARING,
                message=f"Indexing {len(records)} surgeries…",
                current=0,
                total=len(records),
                model_available=available,
            ),
        )
        if not records:
            _emit(
                on_progress,
                RetrievalProgress(
                    phase=RetrievalPhase.DONE,
                    message="No indexed surgeries.",
                    model_available=available,
                ),
            )
            return 0

        if not available:
            _emit(
                on_progress,
                RetrievalProgress(
                    phase=RetrievalPhase.MODEL_UNAVAILABLE,
                    message=(
                        "Embedding model is unavailable. Keyword search will be used."
                    ),
                    current=0,
                    total=len(records),
                    model_available=False,
                    fallback="keyword",
                ),
            )
            _emit(
                on_progress,
                RetrievalProgress(
                    phase=RetrievalPhase.DONE,
                    message=f"{len(records)} surgeries indexed (keyword only).",
                    current=len(records),
                    total=len(records),
                    model_available=False,
                    fallback="keyword",
                ),
            )
            return 0

        model_id = self.embedder.model_id
        missing: list[SearchRecord] = []
        for rec in records:
            cached = self._cache.get(rec.source_text_hash, model_id)
            if cached is None:
                missing.append(rec)
            else:
                self._vectors[rec.pid] = cached

        written = 0
        for start in range(0, len(missing), DEFAULT_EMBED_BATCH):
            batch = missing[start : start + DEFAULT_EMBED_BATCH]
            _emit(
                on_progress,
                RetrievalProgress(
                    phase=RetrievalPhase.PREPARING,
                    message=f"Embedding {start + len(batch)} of {len(missing)} new records…",
                    current=start + len(batch),
                    total=len(missing),
                    model_available=True,
                ),
            )
            try:
                vectors = self.embedder.embed([r.source_text for r in batch])
            except Exception as exc:
                _emit(
                    on_progress,
                    RetrievalProgress(
                        phase=RetrievalPhase.MODEL_UNAVAILABLE,
                        message=f"Embedding model failed ({exc}). Keyword search will be used.",
                        current=written,
                        total=len(missing),
                        model_available=False,
                        fallback="keyword",
                    ),
                )
                return written
            if len(vectors) != len(batch):
                raise RuntimeError("Embedder returned the wrong number of vectors.")
            for rec, vec in zip(batch, vectors, strict=True):
                self._cache.put(rec.source_text_hash, model_id, vec)
                self._vectors[rec.pid] = [float(x) for x in vec]
                written += 1

        _emit(
            on_progress,
            RetrievalProgress(
                phase=RetrievalPhase.DONE,
                message=f"{len(records)} surgeries indexed.",
                current=len(records),
                total=len(records),
                model_available=True,
            ),
        )
        return written

    def search(
        self,
        question: str,
        *,
        filters: CaseFilters | None = None,
        limit: int | None = None,
        on_progress: ProgressCb | None = None,
    ) -> RetrievalResult:
        """Embed the question (or keyword-fallback) and return a bounded shortlist."""
        if not self._records:
            self.prepare(on_progress=on_progress)

        filters = filters or CaseFilters()
        cap = max(1, int(limit or self.candidate_limit))
        indexed_count = len(self._records)
        analyzed_count = sum(1 for r in self._records if r.analyzed)
        available = self.model_available() and bool(self._vectors)

        _emit(
            on_progress,
            RetrievalProgress(
                phase=RetrievalPhase.FILTERING,
                message="Applying field filters…",
                total=indexed_count,
                model_available=available,
            ),
        )
        pool = [r for r in self._records if _record_matches(r, filters)]

        if available:
            try:
                _emit(
                    on_progress,
                    RetrievalProgress(
                        phase=RetrievalPhase.EMBEDDING_QUERY,
                        message="Embedding the question…",
                        total=len(pool),
                        model_available=True,
                    ),
                )
                q_vecs = self.embedder.embed([question or ""])
                q_vec = q_vecs[0] if q_vecs else []
                _emit(
                    on_progress,
                    RetrievalProgress(
                        phase=RetrievalPhase.RANKING,
                        message="Ranking indexed surgeries…",
                        total=len(pool),
                        model_available=True,
                    ),
                )
                ranked = self._rank_embedding(pool, q_vec, cap)
                method = "embedding"
                model_ok = True
                fallback = None
            except Exception:
                ranked = self._rank_keyword(pool, question, cap)
                method = "keyword"
                model_ok = False
                fallback = "keyword"
                _emit(
                    on_progress,
                    RetrievalProgress(
                        phase=RetrievalPhase.MODEL_UNAVAILABLE,
                        message=(
                            "Embedding failed. Using keyword search over indexed surgeries."
                        ),
                        model_available=False,
                        fallback="keyword",
                    ),
                )
        else:
            _emit(
                on_progress,
                RetrievalProgress(
                    phase=RetrievalPhase.KEYWORD_FALLBACK
                    if not self.model_available()
                    else RetrievalPhase.RANKING,
                    message=(
                        "Embedding model is unavailable. Using keyword search."
                        if not self.model_available()
                        else "No cached vectors. Using keyword search."
                    ),
                    total=len(pool),
                    model_available=False,
                    fallback="keyword",
                ),
            )
            ranked = self._rank_keyword(pool, question, cap)
            method = "keyword"
            model_ok = False
            fallback = "keyword"

        done = RetrievalProgress(
            phase=RetrievalPhase.DONE,
            message=(
                f"{len(ranked)} candidate(s) from {len(pool)} filtered "
                f"of {indexed_count} indexed."
            ),
            current=len(ranked),
            total=indexed_count,
            model_available=model_ok,
            fallback=fallback,
        )
        _emit(on_progress, done)
        return RetrievalResult(
            candidates=tuple(ranked),
            indexed_count=indexed_count,
            analyzed_count=analyzed_count,
            rank_method=method,
            model_available=model_ok,
            embedding_model=self.embedder.model_id if self.embedder else None,
            progress=done,
        )

    def fetch_extracts(
        self,
        pids: Sequence[str],
        *,
        on_progress: ProgressCb | None = None,
        session_frames: dict[str, pd.DataFrame] | None = None,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Load detailed extracts only for the selected or top bounded PIDs.

        Returns ``(extracts, fetched_pids)``. Pipeline work is skipped for PIDs
        already present in ``session_frames``.
        """
        wanted: list[str] = []
        known = {r.pid for r in self._records}
        for pid in pids:
            s = str(pid).strip()
            if s and s in known and s not in wanted:
                wanted.append(s)
            if len(wanted) >= self.extract_limit:
                break

        _emit(
            on_progress,
            RetrievalProgress(
                phase=RetrievalPhase.FETCHING,
                message=f"Loading {len(wanted)} case extract(s)…",
                current=0,
                total=len(wanted),
                model_available=self.model_available(),
            ),
        )
        extracts: list[str] = []
        fetched: list[str] = []
        for i, pid in enumerate(wanted, start=1):
            rec = next(r for r in self._records if r.pid == pid)
            extract = self._extract_for(rec, session_frames)
            extracts.append(extract)
            fetched.append(pid)
            _emit(
                on_progress,
                RetrievalProgress(
                    phase=RetrievalPhase.FETCHING,
                    message=f"Loaded {source_label(rec.analyzed, rec.has_vent)} {pid}.",
                    current=i,
                    total=len(wanted),
                    model_available=self.model_available(),
                ),
            )
        return tuple(extracts), tuple(fetched)

    def retrieve(
        self,
        question: str,
        *,
        filters: CaseFilters | None = None,
        selected_pids: Sequence[str] | None = None,
        on_progress: ProgressCb | None = None,
        session_frames: dict[str, pd.DataFrame] | None = None,
    ) -> RetrievalResult:
        """Search, then fetch extracts for selected PIDs or the top bounded hits."""
        result = self.search(question, filters=filters, on_progress=on_progress)
        if selected_pids:
            pids = list(selected_pids)
        else:
            # Extracts without ventilator data have no signals to ground an answer
            pids = [c.pid for c in prefer_ventilated(result.candidates)[: self.extract_limit]]
        extracts, fetched = self.fetch_extracts(
            pids, on_progress=on_progress, session_frames=session_frames
        )
        result.extracts = extracts
        result.fetched_pids = fetched
        return result

    def _records_from_index(self, index: pd.DataFrame) -> list[SearchRecord]:
        if index is None or index.empty or "PID" not in index.columns:
            return []
        out: list[SearchRecord] = []
        for _, row in index.iterrows():
            pid = str(row.get("PID", "")).strip()
            if not pid:
                continue
            procedure = str(row.get("Procedure", "") or "")
            age = _optional_float(row.get("Age"))
            gender = str(row.get("Gender", "") or "") or None
            has_vent = bool(row.get("has_vent", False))
            analyzed = pid in self.analyzed_pids
            text = build_case_search_text(
                pid=pid,
                procedure=procedure,
                age=age,
                gender=gender,
                has_vent=has_vent,
                analyzed=analyzed,
            )
            out.append(
                SearchRecord(
                    pid=pid,
                    source_text=text,
                    source_text_hash=source_text_hash(text),
                    procedure=procedure,
                    age=age,
                    gender=gender,
                    has_vent=has_vent,
                    analyzed=analyzed,
                )
            )
        return out

    def _rank_embedding(
        self,
        pool: Sequence[SearchRecord],
        query: Sequence[float],
        limit: int,
    ) -> list[SearchCandidate]:
        scored: list[tuple[float, SearchRecord]] = []
        for rec in pool:
            vec = self._vectors.get(rec.pid)
            if vec is None:
                continue
            scored.append((_cosine(query, vec), rec))
        scored.sort(key=_rank_key, reverse=True)
        return [
            _candidate(rec, score=score, method="embedding")
            for score, rec in scored[:limit]
        ]

    def _rank_keyword(
        self, pool: Sequence[SearchRecord], question: str, limit: int
    ) -> list[SearchCandidate]:
        scored = [(_keyword_score(question, rec.source_text), rec) for rec in pool]
        has_terms = bool([t for t in str(question).lower().split() if len(t) > 2])
        if has_terms:
            scored = [(s, r) for s, r in scored if s > 0]
        # Short metadata texts tie often; ties must not fall to file order
        scored.sort(key=_rank_key, reverse=True)
        return [
            _candidate(rec, score=score, method="keyword")
            for score, rec in scored[:limit]
        ]

    def _extract_for(
        self,
        rec: SearchRecord,
        session_frames: dict[str, pd.DataFrame] | None,
    ) -> str:
        header = (
            f"SOURCE EXTRACT ({source_label(rec.analyzed, rec.has_vent)}) PID={rec.pid}\n"
            f"{rec.source_text}\n"
        )
        frames = _frames_for_pid(rec.pid, session_frames)
        if frames is None:
            try:
                fetched = case_fetch.fetch_cases(
                    [rec.pid], preset=self.preset, emr=self.emr
                )
            except Exception as exc:
                return header + f"(detailed fetch failed: {exc})"
            frames = {
                "cases": fetched.cases,
                "timeseries": fetched.timeseries,
                "flags": fetched.flags,
                "episodes": fetched.episodes,
                "events": fetched.events,
            }
        try:
            brief = build_case_context(
                rec.pid,
                cases=frames.get("cases", pd.DataFrame()),
                timeseries=frames.get("timeseries", pd.DataFrame()),
                flags=frames.get("flags"),
                episodes=frames.get("episodes"),
                events=frames.get("events"),
                include_emr_extras=False,
            )
        except Exception as exc:
            return header + f"(briefing unavailable: {exc})"
        return header + brief


def _optional_float(value: object) -> float | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _record_matches(rec: SearchRecord, filters: CaseFilters) -> bool:
    if filters.require_vent and not rec.has_vent:
        return False
    if filters.analyzed is True and not rec.analyzed:
        return False
    if filters.analyzed is False and rec.analyzed:
        return False
    want_g = _normalize_gender(filters.gender)
    if want_g:
        have = _normalize_gender(rec.gender)
        if have != want_g:
            return False
    if filters.age is not None:
        if rec.age is None:
            return False
        if abs(rec.age - float(filters.age)) > float(filters.age_tolerance):
            return False
    return True


def _candidate(rec: SearchRecord, *, score: float, method: str) -> SearchCandidate:
    return SearchCandidate(
        pid=rec.pid,
        source_label=source_label(rec.analyzed, rec.has_vent),
        analyzed=rec.analyzed,
        procedure=rec.procedure,
        age=rec.age,
        gender=rec.gender,
        source_text=rec.source_text,
        rank_method=method,
        score=float(score),
        has_vent=rec.has_vent,
    )


def _frames_for_pid(
    pid: str, session_frames: dict[str, pd.DataFrame] | None
) -> dict[str, pd.DataFrame] | None:
    if not session_frames:
        return None
    cases = session_frames.get("cases")
    if cases is None or cases.empty or "PID" not in cases.columns:
        return None
    hit = cases[cases["PID"].astype(str) == str(pid)]
    if hit.empty:
        return None
    out: dict[str, pd.DataFrame] = {"cases": hit}
    for key in ("timeseries", "flags", "episodes", "events"):
        df = session_frames.get(key)
        if df is None or df.empty or "PID" not in df.columns:
            out[key] = pd.DataFrame()
        else:
            out[key] = df[df["PID"].astype(str) == str(pid)].copy()
    return out
