"""Hand retrieved source extracts to the existing local Ollama agent.

Vectors and rank scores stay in the retrieval engine. This module only
forwards labeled SIS text, then reuses ``run_agent`` (loopback client,
answer gate, research footer).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import pandas as pd

from src.llm.agent import AgentResult, ProgressCb, run_agent
from src.llm.ollama_client import OllamaClient
from src.llm.prompts import MODE_ANALYZE
from src.llm.tools import SessionState
from src.services.retrieval import (
    RetrievalPhase,
    RetrievalProgress,
    SearchCandidate,
)

RetrieveProgressCb = Callable[[RetrievalProgress], None]


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


def answer_from_retrieval(
    *,
    question: str,
    model: str,
    candidates: Sequence[SearchCandidate],
    extracts: Sequence[str],
    client: OllamaClient,
    mode: str = MODE_ANALYZE,
    on_status: ProgressCb | None = None,
    on_progress: RetrieveProgressCb | None = None,
) -> AgentResult:
    """Answer from at most three source extracts without corpus tools."""
    prefetched = format_grounded_extracts(
        candidates=candidates, extracts=extracts
    )
    cited = candidates[: min(len(candidates), len(extracts), 3)]
    research_session = SessionState(
        cases=pd.DataFrame({"PID": [candidate.pid for candidate in cited]})
    )

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
    )
