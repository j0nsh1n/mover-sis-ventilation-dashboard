"""Background retrieval and case loading for the evidence workspace."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from src.llm.ollama_client import OllamaClient
from src.llm.retrieve import answer_from_articles, answer_from_retrieval
from src.services.articles import ArticleCatalog
from src.services.case_fetch import fetch_cases
from src.services.retrieval import RetrievalEngine


class ResearchWorker(QThread):
    progress = Signal(object)
    shortlist = Signal(object)
    answer = Signal(object)
    failed = Signal(str)

    def __init__(self, engine: RetrievalEngine | ArticleCatalog, question: str, model: str | None, session, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.question = question
        self.model = model
        self.session = session

    def run(self) -> None:
        try:
            if isinstance(self.engine, ArticleCatalog):
                article_result = self.engine.search(self.question)
                if self.isInterruptionRequested():
                    return
                self.shortlist.emit(article_result)
                if self.model and article_result.articles:
                    response = answer_from_articles(
                        question=self.question, model=self.model, articles=article_result.articles,
                        client=OllamaClient(timeout_s=300.0),
                    )
                    if not self.isInterruptionRequested():
                        self.answer.emit(response)
                return

            def progress(item) -> None:
                if self.isInterruptionRequested():
                    raise InterruptedError("Search was cancelled.")
                self.progress.emit(item)

            result = self.engine.search(self.question, on_progress=progress)
            self.shortlist.emit(result)
            if not self.model or not result.candidates:
                return
            pids = [candidate.pid for candidate in result.candidates[: self.engine.extract_limit]]
            frames = {
                "cases": self.session.cases,
                "timeseries": self.session.timeseries,
                "flags": self.session.flags,
                "episodes": self.session.episodes,
                "events": self.session.events,
            }
            extracts, fetched = self.engine.fetch_extracts(
                pids, on_progress=progress, session_frames=frames
            )
            candidates = [
                next(candidate for candidate in result.candidates if candidate.pid == pid)
                for pid in fetched
            ]
            response = answer_from_retrieval(
                question=self.question,
                model=self.model,
                candidates=candidates,
                extracts=extracts,
                client=OllamaClient(timeout_s=300.0),
                preset=self.engine.preset,
                on_progress=progress,
            )
            self.answer.emit(response)
        except InterruptedError:
            pass
        except Exception as exc:
            self.failed.emit(str(exc))


class CaseFetchWorker(QThread):
    loaded = Signal(object)
    failed = Signal(str)

    def __init__(self, pid: str, emr: Path, preset: str = "default", parent=None):
        super().__init__(parent)
        self.pid = pid
        self.emr = emr
        self.preset = preset

    def run(self) -> None:
        try:
            self.loaded.emit(fetch_cases([self.pid], emr=self.emr, preset=self.preset))
        except Exception as exc:
            self.failed.emit(str(exc))
