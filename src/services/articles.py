"""Local file article catalog. Import and search never fetch remote sources."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.services.retrieval import EmbeddingCache, Embedder, cosine, source_text_hash, tokenize_search_text

ARTICLE_FIELDS = ("id", "title", "abstract", "year", "authors", "doi_or_url", "source", "rights_note")
NO_CATALOG = "No article catalog yet. Use File > Import articles…"
NO_MATCHES = "No matching articles in the local catalog."
ABSTRACT_LIMIT = 5
# Python's csv default (131,072) rejects long real abstracts; anything past this is not an abstract.
CSV_FIELD_LIMIT = 1_000_000


class ArticleCatalogError(ValueError):
    pass


@dataclass(frozen=True)
class Article:
    id: str
    title: str
    abstract: str
    year: int
    authors: str
    doi_or_url: str
    source: str
    rights_note: str
    imported_at: str

    @property
    def search_text(self) -> str:
        return f"{self.title}\n{self.abstract}"


@dataclass(frozen=True)
class ImportReport:
    imported: int
    errors: tuple[str, ...]


@dataclass(frozen=True)
class ArticleSearchResult:
    articles: tuple[Article, ...] = ()
    catalog_count: int = 0
    rank_method: str = "fts"

    @property
    def message(self) -> str:
        return NO_CATALOG if not self.catalog_count else NO_MATCHES if not self.articles else "Article search complete."


def check_fts5() -> None:
    try:
        with sqlite3.connect(":memory:") as con:
            con.execute("CREATE VIRTUAL TABLE probe USING fts5(text)")
    except sqlite3.OperationalError as exc:
        raise ArticleCatalogError(
            "Article search is unavailable: this SQLite build lacks FTS5. Use a Python build with SQLite FTS5 support."
        ) from exc


def _article(row: object, imported_at: str) -> Article:
    if not isinstance(row, Mapping):
        raise ValueError("expected an article object")
    missing = [name for name in ARTICLE_FIELDS if name not in row]
    if missing:
        raise ValueError("missing fields: " + ", ".join(missing))
    for name in ARTICLE_FIELDS:
        if name != "year" and (not isinstance(row[name], str) or not row[name].strip()):
            raise ValueError(f"{name} must be non-empty text")
    year = str(row["year"]).strip()
    if not re.fullmatch(r"[0-9]{1,4}", year) or int(year) < 1:
        raise ValueError("year must be a positive four-digit year")
    # Hash arbitrary source IDs so SYN tokens and bare hex strings cannot be PIDs.
    identifier = "A-" + hashlib.sha256(row["id"].strip().encode("utf-8")).hexdigest()[:20]
    return Article(
        id=identifier, title=row["title"].strip(), abstract=row["abstract"].strip(),
        year=int(year), authors=row["authors"].strip(), doi_or_url=row["doi_or_url"].strip(),
        source=row["source"].strip(), rights_note=row["rights_note"].strip(), imported_at=imported_at,
    )


class ArticleCatalog:
    def __init__(self, cache_path: Path, embedder: Embedder | None = None):
        check_fts5()
        self.path = Path(cache_path).parent / "articles.sqlite3"
        self.embedder = embedder
        self._cache_path = Path(cache_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.execute("""CREATE TABLE IF NOT EXISTS articles (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, abstract TEXT NOT NULL,
                year INTEGER NOT NULL, authors TEXT NOT NULL, doi_or_url TEXT NOT NULL,
                source TEXT NOT NULL, rights_note TEXT NOT NULL, imported_at TEXT NOT NULL)""")
            con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS article_fts USING fts5(id UNINDEXED, title, abstract, tokenize='porter unicode61')")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    @property
    def count(self) -> int:
        with self._connect() as con:
            return int(con.execute("SELECT count(*) FROM articles").fetchone()[0])

    def import_file(self, path: Path) -> ImportReport:
        path = Path(path)
        if path.suffix.lower() not in {".csv", ".jsonl"}:
            raise ArticleCatalogError("Choose a local CSV or JSONL article file.")
        imported, errors = 0, []
        imported_at = datetime.now(timezone.utc).isoformat()
        is_csv = path.suffix.lower() == ".csv"
        previous_limit = csv.field_size_limit(CSV_FIELD_LIMIT)
        try:
            with path.open(encoding="utf-8-sig", newline="") as stream, self._connect() as con:
                reader = csv.DictReader(stream) if is_csv else None
                rows = iter(reader if reader is not None else stream)
                number = 0
                while True:
                    try:
                        raw = next(rows)
                    except StopIteration:
                        break
                    except (csv.Error, UnicodeDecodeError) as exc:
                        # An unreadable file leaves the rest unparseable, so keep nothing.
                        line = reader.line_num if reader is not None else number + 1
                        reason = (f"a field is larger than {CSV_FIELD_LIMIT:,} characters"
                                  if "field larger than field limit" in str(exc) else str(exc))
                        raise ArticleCatalogError(
                            f"Line {line:,}: {reason}. Nothing was imported."
                        ) from exc
                    number = reader.line_num if reader is not None else number + 1
                    try:
                        row = json.loads(raw) if isinstance(raw, str) else raw
                        article = _article(row, imported_at)
                        con.execute("INSERT INTO articles VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                    tuple(getattr(article, name) for name in (*ARTICLE_FIELDS, "imported_at")))
                        con.execute("INSERT INTO article_fts VALUES (?, ?, ?)", (article.id, article.title, article.abstract))
                        imported += 1
                    except sqlite3.IntegrityError:
                        errors.append(f"Row {number}: duplicate id; skipped.")
                    except (ValueError, TypeError) as exc:
                        errors.append(f"Row {number}: {exc}")
        finally:
            csv.field_size_limit(previous_limit)
        return ImportReport(imported, tuple(errors))

    def search(self, query: str, *, limit: int = 10) -> ArticleSearchResult:
        terms = list(dict.fromkeys(tokenize_search_text(query)))
        count = self.count
        if not terms or not count:
            return ArticleSearchResult(catalog_count=count)
        expression = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)
        with self._connect() as con:
            rows = con.execute("""SELECT articles.* FROM article_fts JOIN articles ON articles.id = article_fts.id
                WHERE article_fts MATCH ? ORDER BY bm25(article_fts), articles.id""", (expression,)).fetchall()
            articles = [Article(*row) for row in rows]
            fts_ranks = {a.id: i for i, a in enumerate(articles, 1)}
            all_articles = [Article(*row) for row in con.execute("SELECT * FROM articles ORDER BY id")]
        semantic: dict[str, float] = {}
        if self.embedder is not None:
            try:
                if self.embedder.is_available():
                    cache = EmbeddingCache(self._cache_path)
                    model = self.embedder.model_id
                    vectors = {a.id: cache.get(source_text_hash(a.search_text), model) for a in all_articles}
                    missing = [a for a in all_articles if vectors[a.id] is None]
                    for start in range(0, len(missing), 32):
                        batch = missing[start:start + 32]
                        embedded = self.embedder.embed([a.search_text for a in batch])
                        if len(embedded) != len(batch):
                            raise ValueError("Embedding batch is incomplete")
                        for a, vector in zip(batch, embedded, strict=True):
                            cache.put(source_text_hash(a.search_text), model, vector)
                            vectors[a.id] = vector
                    query_vector = self.embedder.embed([query])[0]
                    semantic = {a.id: cosine(query_vector, vectors[a.id] or []) for a in all_articles}
            except Exception:
                semantic = {}
        method = "fts"
        if semantic:
            method = "blended"
            semantic_order = sorted((a for a in all_articles if semantic[a.id] > 0),
                                    key=lambda a: (-semantic[a.id], a.id))
            semantic_ranks = {a.id: i for i, a in enumerate(semantic_order, 1)}
            candidates = {a.id: a for a in (*articles, *semantic_order)}
            def score(a: Article) -> float:
                return ((1 / (60 + fts_ranks[a.id]) if a.id in fts_ranks else 0)
                        + (1 / (60 + semantic_ranks[a.id]) if a.id in semantic_ranks else 0))
            articles = sorted(candidates.values(), key=lambda a: (-score(a), a.id))
        return ArticleSearchResult(tuple(articles[:max(1, limit)]), count, method)
