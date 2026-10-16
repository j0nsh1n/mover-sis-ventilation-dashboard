"""Hand retrieved source extracts to the existing local Ollama agent.

Vectors and rank scores stay in the retrieval engine. This module only
forwards labeled SIS text, then reuses ``run_agent`` (loopback client,
answer gate, research footer).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Literal

import pandas as pd

from src.guardrails.exceptions import PipelineError
from src.llm.agent import AgentResult, ProgressCb, run_agent
from src.llm.case_context import load_medications
from src.llm.ollama_client import OllamaClient
from src.llm.prompts import MODE_ANALYZE
from src.llm.tools import SessionState
from src.services.retrieval import (
    RetrievalPhase,
    RetrievalProgress,
    SearchCandidate,
)
from src.services.articles import ABSTRACT_LIMIT, Article, NO_MATCHES

RetrieveProgressCb = Callable[[RetrievalProgress], None]


def answer_from_articles(
    *, question: str, model: str, articles: Sequence[Article], client: OllamaClient,
    on_status: ProgressCb | None = None,
) -> AgentResult:
    cited = articles[:ABSTRACT_LIMIT]
    if not cited:
        return AgentResult(answer=NO_MATCHES)
    blocks = [
        f"[A{i}] {a.title} ({a.year}). {a.authors}.\n"
        f"DOI/URL: {a.doi_or_url}\nSource: {a.source}\nRights: {a.rights_note}\nAbstract: {a.abstract}"
        for i, a in enumerate(cited, 1)
    ]
    extracts = "\n\n".join(blocks)
    return run_agent(
        model=model, question=question, session=SessionState(), client=client,
        mode=MODE_ANALYZE, skip_prefetch=True, extra_prefetched=extracts,
        allow_tools=False, source_scope="articles", sources=blocks, on_status=on_status,
    )


def format_grounded_extracts(
    *,
    candidates: Sequence[SearchCandidate],
    extracts: Sequence[str],
) -> str:
    """Build the prefetched block for the chat model. No vectors, no scores."""
    lines = [
        "Retrieved source extracts. Cite only this text.",
        "Numeric rank scores are omitted. Cite the extract text only.",
        "Each block is labeled indexed surgery or analyzed case.",
    ]
    n = min(len(candidates), len(extracts), 3)
    for i in range(n):
        cand = candidates[i]
        lines.append(
            f"\n--- source {i + 1}: {cand.source_label} PID={cand.pid} ---\n"
            f"{extracts[i]}"
        )
    if n == 0:
        lines.append("\nNo source extracts were loaded for this question.")
    return "\n".join(lines)


def _case_facts(candidate: SearchCandidate) -> str:
    """The cited case's own age, which the model may restate even when the extract omits it."""
    if candidate.age is None:
        return f"Case facts: PID {candidate.pid}"
    return f"Case facts: PID {candidate.pid}, age {candidate.age:g}"


def _research_session(cited: Sequence[SearchCandidate], preset: str) -> SessionState:
    """A session holding only the cited cases, so the answer gate checks exactly them."""
    pids = [candidate.pid for candidate in cited]
    unreadable = None
    try:
        medications = load_medications(pids)
    except (PipelineError, OSError) as exc:
        medications, unreadable = pd.DataFrame(), str(exc)
    return SessionState(
        preset=preset,
        cases=pd.DataFrame(
            {
                "PID": pids,
                "Age": [candidate.age for candidate in cited],
                "Gender": [candidate.gender for candidate in cited],
                "Procedure_short": [candidate.procedure for candidate in cited],
            }
        ),
        cited_pids=pids,
        medications=medications,
        medications_unreadable=unreadable,
    )


def answer_from_retrieval(
    *,
    question: str,
    model: str,
    candidates: Sequence[SearchCandidate],
    extracts: Sequence[str],
    client: OllamaClient,
    preset: str = "default",
    mode: str = MODE_ANALYZE,
    kind: Literal["question", "scenario", "compare"] = "question",
    on_status: ProgressCb | None = None,
    on_progress: RetrieveProgressCb | None = None,
) -> AgentResult:
    """Answer from at most three source extracts without corpus tools."""
    prefetched = format_grounded_extracts(
        candidates=candidates, extracts=extracts
    )
    cited = candidates[: min(len(candidates), len(extracts), 3)]
    sources = [
        f"{extract}\n{_case_facts(candidate)}"
        for candidate, extract in zip(cited, extracts[: len(cited)], strict=True)
        if extract.strip()
    ]
    research_session = _research_session(cited, preset)

    def status(msg: str) -> None:
        if on_status is not None:
            on_status(msg)
        if on_progress is not None:
            on_progress(
                RetrievalProgress(
                    phase=RetrievalPhase.GENERATING,
                    message=msg,
                    model_available=True,
                )
            )

    if on_progress is not None:
        on_progress(
            RetrievalProgress(
                phase=RetrievalPhase.GENERATING,
                message="Generating a research answer from source extracts…",
                model_available=True,
            )
        )
    return run_agent(
        model=model,
        question=question,
        session=research_session,
        client=client,
        mode=mode,
        on_status=status,
        skip_prefetch=True,
        extra_prefetched=prefetched,
        allow_tools=False,
        sources=sources,
        answer_kind=kind,
    )


def answer_from_minute(
    *,
    question: str,
    model: str,
    candidate: SearchCandidate,
    extract: str,
    client: OllamaClient,
    preset: str = "default",
    on_status: ProgressCb | None = None,
) -> AgentResult:
    """Answer a question about one minute of one case from that minute's extract alone."""
    return run_agent(
        model=model,
        question=question,
        session=_research_session([candidate], preset),
        client=client,
        mode=MODE_ANALYZE,
        on_status=on_status,
        skip_prefetch=True,
        extra_prefetched=format_grounded_extracts(candidates=[candidate], extracts=[extract]),
        allow_tools=False,
        sources=[f"{extract}\n{_case_facts(candidate)}"],
    )
