"""Background retrieval and case loading for the evidence workspace."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from src.llm.intent import Intent, classify
from src.llm.ollama_client import OllamaClient
from src.llm.retrieve import answer_from_articles, answer_from_minute, answer_from_retrieval
from src.services.articles import ArticleCatalog
from src.services.case_fetch import fetch_cases
from src.services.retrieval import (
    RetrievalEngine,
    RetrievalProgress,
    RetrievalResult,
    SearchCandidate,
    prefer_ventilated,
)

COMPARE_MINIMUM = 2


def _plural(count: int, singular: str, plural: str) -> str:
    return f"{count:,} {singular if count == 1 else plural}"


def _names(pids: list[str]) -> str:
    return pids[0] if len(pids) == 1 else ", ".join(pids[:-1]) + f" and {pids[-1]}"


def _agent_coverage(engine: RetrievalEngine) -> str:
    if engine.agents_from_scan:
        return " The agent comes from ventilator data, so surgeries without it never match."
    return (
        " The agent is known only for analyzed cases;"
        " a full-EMR scan adds it for every surgery with ventilator data."
    )


def _scenario_search(
    engine: RetrievalEngine, intent: Intent, question: str,
    progress: Callable[[RetrievalProgress], None],
) -> tuple[RetrievalResult, list[SearchCandidate]]:
    filters = intent.case_filters()
    result = engine.search(question, filters=filters, on_progress=progress)
    # Extracts without ventilator data have no signals to describe
    candidates = prefer_ventilated(result.candidates)
    head = intent.scenario_text()
    if not candidates:
        previous, reason = result.indexed_count, ""
        wording = dict(intent.parts())
        for name, left in engine.filter_counts(filters):
            if left == 0:
                reason = (
                    f"No indexed surgery matched the {name} filter ({wording[name]}); "
                    f"{_plural(previous, 'surgery', 'surgeries')} passed the filters before it."
                    + (_agent_coverage(engine) if name == "agent" else "")
                )
                break
            previous = left
        reason = reason or "No indexed surgery matched this scenario."
        return replace(
            result, candidates=(), scope_note=f"{head} · no matching cases", empty_reason=reason
        ), []
    matched = result.filtered_count or len(candidates)
    note = f"{head} · {_plural(matched, 'matching case', 'matching cases')}"
    if matched > engine.extract_limit:
        note += f" · answering from the top {engine.extract_limit}"
    if filters.agent and not engine.agents_from_scan:
        note += " · agent known for analyzed cases only"
    return replace(result, candidates=tuple(candidates), scope_note=note), candidates[: engine.extract_limit]


def _compare_search(
    engine: RetrievalEngine, intent: Intent, question: str,
    progress: Callable[[RetrievalProgress], None],
) -> tuple[RetrievalResult, list[SearchCandidate]]:
    result = engine.search(question, on_progress=progress)
    named, unknown = engine.resolve_pids(intent.pids)
    chosen = named[: engine.extract_limit]
    if len(intent.pids) < COMPARE_MINIMUM:
        for candidate in prefer_ventilated(result.candidates):
            if len(chosen) >= COMPARE_MINIMUM:
                break
            if candidate.pid not in chosen:
                chosen.append(candidate.pid)
    candidates = engine.candidates_for(chosen)
    note = f"Comparing {_names(chosen)}" if chosen else "Comparing: no cases found"
    if len(named) > len(chosen):
        note += f" · the first {len(chosen)} of {len(named)} named"
    if unknown:
        note += f" · not in the indexed surgeries: {', '.join(unknown)}"
    reason = ""
    if len(candidates) < COMPARE_MINIMUM:
        reason = "A comparison needs two cases in the indexed surgeries."
        if unknown:
            reason += f" Not found: {', '.join(unknown)}."
    return replace(
        result, candidates=tuple(candidates), scope_note=note, empty_reason=reason,
        filtered_count=len(candidates), compare=True,
    ), [] if reason else candidates


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

            intent = classify(self.question)
            if intent.kind == "scenario":
                result, chosen = _scenario_search(self.engine, intent, self.question, progress)
            elif intent.kind == "compare":
                result, chosen = _compare_search(self.engine, intent, self.question, progress)
            else:
                result = self.engine.search(self.question, on_progress=progress)
                chosen = list(result.candidates[: self.engine.extract_limit])
            self.shortlist.emit(result)
            if not self.model or not chosen:
                return
            pids = [candidate.pid for candidate in chosen]
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
                next(candidate for candidate in chosen if candidate.pid == pid)
                for pid in fetched
            ]
            response = answer_from_retrieval(
                question=self.question,
                model=self.model,
                candidates=candidates,
                extracts=extracts,
                client=OllamaClient(timeout_s=300.0),
                preset=self.engine.preset,
                kind=intent.kind,
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


class MinuteQuestionWorker(QThread):
    answer = Signal(object)
    failed = Signal(str)

    def __init__(self, candidate: SearchCandidate, extract: str, question: str, model: str,
                 preset: str = "default", parent=None):
        super().__init__(parent)
        self.candidate = candidate
        self.extract = extract
        self.question = question
        self.model = model
        self.preset = preset

    def run(self) -> None:
        try:
            response = answer_from_minute(
                question=self.question, model=self.model, candidate=self.candidate,
                extract=self.extract, client=OllamaClient(timeout_s=300.0), preset=self.preset,
            )
            if not self.isInterruptionRequested():
                self.answer.emit(response)
        except Exception as exc:
            if not self.isInterruptionRequested():
                self.failed.emit(str(exc))
