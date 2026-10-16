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
        analysis_facts=build_analysis_facts(ts, flags),
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

Article retrieval uses these shared embedding helpers from src.services.articles.
"""

from __future__ import annotations

import hashlib
import math
import re
import sqlite3
import struct
import threading
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

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
    age_min: float | None = None
    age_max: float | None = None
    gender: str | None = None
    procedures: tuple[str, ...] = ()
    agent: str | None = None
    require_vent: bool = False
    analyzed: bool | None = None

    @property
    def describes_cases(self) -> bool:
        """True when the filters name a kind of case, as a scenario question does."""
        return any(
            (self.age is not None, self.age_min is not None, self.age_max is not None,
             self.gender, self.procedures, self.agent)
        )


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
    # Known for analyzed cases and, after a full-EMR scan, for every ventilated surgery
    agent: str | None = None


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
    embedded_count: int = 0
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
    # Cases passing the filters, before the shortlist cap
    filtered_count: int = 0
    # Set for scenario and comparison questions: the board's scope line, and why a scenario matched nothing
    scope_note: str = ""
    empty_reason: str = ""
    compare: bool = False


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
    facts: str = "",
) -> str:
    """Stable, factual search document: surgery metadata, plus analysis facts.

    ``facts`` (see ``build_analysis_facts``) is appended for analyzed cases
    only: the signals the case has, the rules it flagged and the signals with
    missing minutes. Unanalyzed records never carry facts. No scores or vectors.
    """
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
    text = (
        f"Procedure: {proc}. Age: {age_s}. Sex: {sex}. "
        f"Ventilator rows: {vent}. Record scope: {scope}."
    )
    return f"{text} {facts}" if analyzed and facts else text


def build_analysis_facts(
    timeseries: pd.DataFrame | None, flags: pd.DataFrame | None
) -> dict[str, str]:
    """One deterministic facts string per analyzed PID, from loaded processed data.

    Computed once per processed-data load. Each fact is its own sentence so
    ``_keyword_score`` can tell "ETCO2 flag" from a PIP flag on a case that
    merely records ETCO2. "Missing" means a gap inside a signal's recorded
    span; unrecorded minutes before and after ventilation are not gaps.
    """
    from src.llm.case_context import SIGNAL_LABELS
    from src.pipeline.features import OBSERVED_SIGNAL_COLS
    from src.rules import RULE_METADATA

    if timeseries is None or timeseries.empty or "PID" not in timeseries.columns:
        return {}
    names = {col: _signal_name(col, name) for col, name, _ in SIGNAL_LABELS}
    cols = [c for c in OBSERVED_SIGNAL_COLS if c in names and c in timeseries.columns]
    order = ["PID", "t_min"] if "t_min" in timeseries.columns else ["PID"]
    work = timeseries[[*order, *cols]].sort_values(order, kind="stable")
    pids = work["PID"].astype(str)
    seen = work[cols].notna()
    present = seen.groupby(pids).any()
    before = seen.groupby(pids).cummax()
    after = seen.iloc[::-1].groupby(pids.iloc[::-1]).cummax().iloc[::-1]
    missing = (~seen & before & after).groupby(pids).any()
    missing["nMAP"] = False  # cuff readings are intermittent by design, so not gaps

    flagged: dict[str, list[str]] = {}
    if flags is not None and not flags.empty and {"PID", "rule_id"} <= set(flags.columns):
        pairs = flags[["PID", "rule_id"]].astype(str).drop_duplicates()
        for pid, rule_ids in pairs.groupby("PID")["rule_id"]:
            flagged[str(pid)] = sorted(rule_ids)

    out: dict[str, str] = {}
    for pid in present.index:
        has = present.loc[pid]
        gaps = missing.loc[pid]
        parts = []
        if has.any():
            parts.append("Signals: " + ", ".join(names[c] for c in cols if has[c]) + ".")
        for rule_id in flagged.get(pid, []):
            meta = RULE_METADATA.get(rule_id)
            label = f"{meta.label} ({rule_id})" if meta else rule_id
            parts.append(f"Flag raised: {label}.")
        parts.extend(f"Missing minutes: {names[c]}." for c in cols if gaps[c])
        out[str(pid)] = " ".join(parts)
    return out


def _signal_name(col: str, name: str) -> str:
    name = name.replace("conc.", "concentration")
    return name if name.lower() == col.lower() else f"{name} ({col})"


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


def pack_vector(values: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(values)}f", *[float(v) for v in values])


def unpack_vector(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    va = np.asarray(a, dtype=np.float32)
    vb = np.asarray(b, dtype=np.float32)
    if va.size == 0 or vb.size == 0 or va.size != vb.size:
        return 0.0
    na = float(np.linalg.norm(va))
    nb = float(np.linalg.norm(vb))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


_STOPWORDS = frozenset(
    "a an and any are be by can case cases did do does find for from give has have how "
    "i in is it list many me of on or patient patients show tell that the there these this "
    "those to was were what when where which who why will with".split()
)


def tokenize_search_text(text: str) -> list[str]:
    """Lowercase alphanumeric tokens; "CO₂" folds to "co2", a trailing "s" folds away.

    Questions and search documents share this, so matching is on whole tokens
    ("low" does not hit "flow"). Stopwords are dropped.
    """
    tokens = []
    for tok in re.findall(r"[^\W_]+", str(text).replace("₂", "2").lower()):
        if tok in _STOPWORDS:
            continue
        if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
            tok = tok[:-1]
        tokens.append(tok)
    return tokens


def _keyword_score(query: str, text: str) -> float:
    """Share of question terms found in the text, half of it by the best single sentence.

    Facts are one sentence each, so a case flagged for ETCO2 outranks a case
    that only records ETCO2 and has some other flag.
    """
    terms = set(tokenize_search_text(query))
    if not terms:
        return 0.0
    best, found = 0, set()
    for sentence in re.split(r"[.;]", text):
        hit = terms.intersection(tokenize_search_text(sentence))
        found |= hit
        best = max(best, len(hit))
    return (len(found) + best) / (2 * len(terms))


class _KeywordIndex:
    """Token tables for every record, so one question scores all of them in numpy.

    Same score as ``_keyword_score``; the per-question cost stays flat as
    facts make the documents longer. Each document and sentence carries token
    id 0 so no segment is empty (``reduceat`` mishandles empty segments).
    """

    def __init__(self, texts: Sequence[str]):
        self._vocab: dict[str, int] = {}
        doc_ptr, sent_ptr = [0], [0]
        doc_idx: list[int] = []
        sent_idx: list[int] = []
        sent_doc_ptr: list[int] = []
        for text in texts:
            sent_doc_ptr.append(len(sent_ptr) - 1)
            seen: set[int] = {0}
            for sentence in re.split(r"[.;]", text):
                ids = {0, *(self._vocab.setdefault(t, len(self._vocab) + 1) for t in tokenize_search_text(sentence))}
                sent_idx.extend(ids)
                sent_ptr.append(len(sent_idx))
                seen |= ids
            doc_idx.extend(seen)
            doc_ptr.append(len(doc_idx))
        self._doc_ptr = np.array(doc_ptr[:-1])
        self._doc_idx = np.array(doc_idx)
        self._sent_ptr = np.array(sent_ptr[:-1])
        self._sent_idx = np.array(sent_idx)
        self._sent_doc_ptr = np.array(sent_doc_ptr)

    def scores(self, query: str) -> np.ndarray:
        terms = set(tokenize_search_text(query))
        if not terms or not len(self._doc_ptr):
            return np.zeros(len(self._doc_ptr))
        known = np.zeros(len(self._vocab) + 1, dtype=np.int32)
        known[[self._vocab[t] for t in terms if t in self._vocab]] = 1
        found = np.add.reduceat(known[self._doc_idx], self._doc_ptr)
        per_sentence = np.add.reduceat(known[self._sent_idx], self._sent_ptr)
        best = np.maximum.reduceat(per_sentence, self._sent_doc_ptr)
        return (found + best) / (2 * len(terms))


def _normalize_gender(value: str | None) -> str | None:
    if not value:
        return None
    g = str(value).strip().upper()[:1]
    return g if g in {"M", "F"} else None


class EmbeddingCache:
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
        return unpack_vector(row[0])

    def put(
        self, text_hash: str, model_id: str, vector: Sequence[float]
    ) -> None:
        blob = pack_vector(vector)
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
        analysis_facts: Mapping[str, str] | None = None,
        case_agents: Mapping[str, str] | None = None,
        agents_from_scan: bool = False,
    ):
        self.emr = Path(emr)
        self.embedder = embedder
        self.analyzed_pids = {str(p) for p in (analyzed_pids or [])}
        self.candidate_limit = max(1, int(candidate_limit))
        self.extract_limit = max(1, int(extract_limit))
        self.preset = preset
        self.analysis_facts = dict(analysis_facts or {})
        self.case_agents = {str(pid): str(agent) for pid, agent in (case_agents or {}).items()}
        # True when case_agents covers every ventilated surgery, not just the analyzed sample
        self.agents_from_scan = agents_from_scan
        self._cache = EmbeddingCache(Path(cache_path))
        self._records: list[SearchRecord] = []
        self._keyword_index: tuple[list[SearchRecord], _KeywordIndex, dict[str, int]] | None = None
        self._vectors: dict[str, list[float]] = {}
        self._vector_model_id: str | None = None

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
        embedder = self.embedder
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

        if not available or embedder is None:
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

        model_id = embedder.model_id
        self._vector_model_id = model_id
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
                vectors = embedder.embed([r.source_text for r in batch])
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
        if available and self.embedder is not None and self.embedder.model_id != self._vector_model_id:
            self.prepare(on_progress=on_progress)
            available = bool(self._vectors)

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
        # A scenario names the cases it wants, so a case passing every filter stays
        # on the shortlist even when no question word appears in its text.
        describes = filters.describes_cases

        embedder = self.embedder
        if available and embedder is not None:
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
                q_vecs = embedder.embed([question or ""])
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
                ranked = self._rank_embedding(pool, q_vec, cap, question, keep_unscored=describes)
                method = "embedding"
                model_ok = True
                fallback = None
            except Exception:
                ranked = self._rank_keyword(pool, question, cap, keep_unscored=describes)
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
            ranked = self._rank_keyword(pool, question, cap, keep_unscored=describes)
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
            embedded_count=len(self._vectors),
            analyzed_count=analyzed_count,
            rank_method=method,
            model_available=model_ok,
            embedding_model=self.embedder.model_id if self.embedder else None,
            progress=done,
            filtered_count=len(pool),
        )

    def filter_counts(self, filters: CaseFilters) -> list[tuple[str, int]]:
        """Cases left after each filter in turn, so an empty scenario can name the filter that emptied it."""
        steps = (
            ("age", CaseFilters(age=filters.age, age_tolerance=filters.age_tolerance,
                                age_min=filters.age_min, age_max=filters.age_max)),
            ("sex", CaseFilters(gender=filters.gender)),
            ("procedure", CaseFilters(procedures=filters.procedures)),
            ("agent", CaseFilters(agent=filters.agent)),
        )
        pool = list(self._records)
        counts: list[tuple[str, int]] = []
        for name, step in steps:
            if not step.describes_cases:
                continue
            pool = [r for r in pool if _record_matches(r, step)]
            counts.append((name, len(pool)))
        return counts

    def resolve_pids(self, names: Sequence[str]) -> tuple[list[str], list[str]]:
        """Indexed PIDs for the names given (any letter case), and the names not indexed."""
        by_lower = {r.pid.lower(): r.pid for r in self._records}
        found, missing = [], []
        for name in names:
            pid = by_lower.get(str(name).strip().lower())
            if pid is None:
                missing.append(name)
            elif pid not in found:
                found.append(pid)
        return found, missing

    def candidates_for(self, pids: Sequence[str]) -> list[SearchCandidate]:
        """Shortlist rows for named PIDs, in the order given."""
        by_pid = {r.pid: r for r in self._records}
        return [_candidate(by_pid[pid], score=1.0, method="pid") for pid in pids if pid in by_pid]

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
                facts=self.analysis_facts.get(pid, ""),
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
                    agent=self.case_agents.get(pid),
                )
            )
        return out

    def _rank_embedding(
        self,
        pool: Sequence[SearchRecord],
        query: Sequence[float],
        limit: int,
        question: str = "",
        *,
        keep_unscored: bool = False,
    ) -> list[SearchCandidate]:
        scored: list[tuple[float, SearchRecord]] = []
        missing: list[SearchRecord] = []
        direct: list[SearchRecord] = []
        pid_terms = set(re.findall(r"\w+", question.lower()))
        for rec in pool:
            if rec.pid.lower() in pid_terms:
                direct.append(rec)
                continue
            vec = self._vectors.get(rec.pid)
            if vec is None:
                missing.append(rec)
            else:
                scored.append((cosine(query, vec), rec))
        scored.sort(key=_rank_key, reverse=True)
        ranked = self._rank_keyword(direct, question, limit, keep_unscored=keep_unscored)
        remaining = limit - len(ranked)
        keyword = self._rank_keyword(missing, question, remaining, keep_unscored=keep_unscored)
        # Reserve the unembedded share so keyword hits can compete with a full cosine shortlist.
        keyword_slots = min(len(keyword), math.ceil(remaining * len(missing) / len(pool))) if pool else 0
        ranked.extend(
            _candidate(rec, score=score, method="embedding")
            for score, rec in scored[:remaining - keyword_slots]
        )
        ranked.extend(keyword[:limit - len(ranked)])
        return ranked

    def _rank_keyword(
        self, pool: Sequence[SearchRecord], question: str, limit: int, *, keep_unscored: bool = False
    ) -> list[SearchCandidate]:
        pid_terms = set(re.findall(r"\w+", question.lower()))
        values, positions = self._keyword_scores(question)
        scored = []
        for rec in pool:
            pos = positions.get(rec.pid)
            score = float(values[pos]) if pos is not None else _keyword_score(question, rec.source_text)
            scored.append((2.0 if rec.pid.lower() in pid_terms else score, rec))
        if tokenize_search_text(question) and not keep_unscored:
            scored = [(score, rec) for score, rec in scored if score > 0]
        # Short metadata texts tie often; ties must not fall to file order
        scored.sort(key=_rank_key, reverse=True)
        return [
            _candidate(rec, score=score, method="keyword")
            for score, rec in scored[:limit]
        ]

    def _keyword_scores(self, question: str) -> tuple[np.ndarray, dict[str, int]]:
        if self._keyword_index is None or self._keyword_index[0] is not self._records:
            index = _KeywordIndex([r.source_text for r in self._records])
            positions = {r.pid: i for i, r in enumerate(self._records)}
            self._keyword_index = (self._records, index, positions)
        _, index, positions = self._keyword_index
        return index.scores(question), positions

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


def _optional_float(value: Any) -> float | None:
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
    if filters.age_min is not None or filters.age_max is not None:
        if rec.age is None:
            return False
        if filters.age_min is not None and rec.age < filters.age_min:
            return False
        if filters.age_max is not None and rec.age > filters.age_max:
            return False
    procedure = rec.procedure.lower()
    if any(term.lower() not in procedure for term in filters.procedures):
        return False
    if filters.agent and (rec.agent or "").lower() != filters.agent.lower():
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
