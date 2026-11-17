"""Held-out evaluation of a local Ollama model on the app's research-answer path.

    PYTHONPATH=. .venv/bin/python tools/local_model/eval_heldout.py \
        --split test --seeds 900-901 --n-cases 30 --model MODEL --repeats 3 --out DIR

Questions come from the held-out families on synthetic corpora (the same builders as
``build_prompts``). Each one runs through the real ``answer_from_retrieval`` with a gate
spy, so the app's own number, advice and case checks run unchanged. Every repeat asks
Ollama with its own ``options.seed``. When Ollama is down or the model is not installed
the run exits with a message and writes nothing; it never starts or pulls a model.
Writes ``metrics.json`` and ``answers.jsonl`` into ``--out``.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import statistics
import sys
import time
from collections.abc import Callable, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.llm.ollama_client import OllamaClient, OllamaError  # noqa: E402
from tools.eval_answers import _GateSpy, classify  # noqa: E402
from tools.local_model import build_prompts, questions, splits  # noqa: E402
from tools.local_model.build_prompts import (  # noqa: E402
    ALLOWED_ROOT,
    Refused,
    check_out,
    parse_seeds,
    private_env,
)

HELDOUT_SPLITS = ("valid", "test")
# Repeat i asks Ollama with seed REPEAT_SEED_BASE + i, so a rerun repeats the same draws.
REPEAT_SEED_BASE = 1000
KINDS = ("question", "scenario", "compare")
# The five parts of EXTRACT_SHAPE (src/llm/prompts.py); the last is the research disclaimer.
HEADINGS: dict[str, re.Pattern[str]] = {
    "cases_used": re.compile(r"cases used", re.I),
    "extracts_show": re.compile(r"what the extracts show", re.I),
    "flags": re.compile(r"flags\s*(?:&|and)\s*rule meaning", re.I),
    "gaps": re.compile(r"\bgaps\b", re.I),
    "disclaimer": re.compile(r"research\s*(?:and|&|/)\s*education|research use only|research only", re.I),
}
# What a held-out "absent" question asks for. Each group is one signal and its names.
ABSENT_SIGNALS: tuple[tuple[str, ...], ...] = (
    ("bis", "bispectral"),
    ("blood loss",),
    ("core temperature", "temperature"),
    ("urine output", "urine"),
    ("blood glucose", "glucose"),
)
NOT_AVAILABLE = re.compile(
    r"\bnot (?:available|recorded|reported|documented|included|present|provided|captured|measured|found|stated)\b"
    r"|\bno (?:\w+ ){0,3}(?:data|values?|records?|information|measurements?|readings?)\b"
    r"|\b(?:does|do|did) not (?:record|include|contain|report|have|show|list|provide|capture|document)\b"
    r"|\b(?:is|are|was|were)n't (?:available|recorded|reported|included)\b"
    r"|\bcannot be (?:determined|answered|stated)\b",
    re.I,
)
_NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?")
_SENTENCES = re.compile(r"(?<=[.!?])\s+|\n+")


class Unavailable(Exception):
    """Ollama is down or does not have the model; nothing was measured."""


class SeededOllamaClient(OllamaClient):
    """OllamaClient that sends ``options.seed`` on every chat call (the base class sends none)."""

    def __init__(self, seed: int, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.seed = seed

    def _request_json(
        self, method: str, path: str, body: dict[str, Any] | None = None, *, timeout_s: float | None = None
    ) -> dict[str, Any]:
        if path == "/api/chat" and body is not None:
            body = {**body, "options": {**body.get("options", {}), "seed": self.seed}}
        return super()._request_json(method, path, body, timeout_s=timeout_s)


ClientFactory = Callable[[str, int], Any]


def ollama_factory(model: str, seed: int) -> Any:
    return SeededOllamaClient(seed, timeout_s=300.0)


def repeat_seed(repeat: int) -> int:
    return REPEAT_SEED_BASE + repeat


def check_installed(client: Any, model: str) -> None:
    """Raise Unavailable unless the server answers and lists ``model``."""
    try:
        names = client.list_models()
    except OllamaError as exc:
        raise Unavailable(f"Ollama is not reachable ({exc}); nothing was measured.") from exc
    if not any(name == model or name.split(":")[0] == model for name in names):
        raise Unavailable(f"Model {model!r} is not installed; nothing was measured.")


# ---- questions --------------------------------------------------------------------------


@dataclass
class Item:
    id: str
    seed: int
    family: str
    kind: str
    question: str
    absent: bool
    history: tuple[Any, ...]
    candidates: list[Any]
    extracts: list[str]


def signal_terms(question: str) -> tuple[str, ...]:
    """The names of the absent signal a question asks about; ValueError when it names none."""
    low = question.lower()
    terms = tuple(t for names in ABSENT_SIGNALS if any(re.search(rf"\b{re.escape(n)}\b", low) for n in names) for t in names)
    if not terms:
        raise ValueError(f"no absent signal is named in {question!r}")
    return terms


def _draw(
    corpus: build_prompts.Corpus, seed: int, family: questions.Family, index: int,
    seen: set[tuple[str, ...]], split: str,
) -> Item | None:
    """One question from a family, retrying a draw that repeats an earlier one (as build_prompts does)."""
    from src.llm.history import PriorTurn

    for attempt in range(8):
        rng = random.Random(f"{seed}:{family.name}:{index}:{attempt}")
        cases = questions.pick_cases(family, corpus.facts, rng)
        if cases is None:
            return None
        question = questions.fill(family, cases, rng)
        earlier = questions.earlier_questions(family, cases, rng)
        key = (question, *earlier)
        if key in seen:
            continue
        history: list[PriorTurn] = []
        for asked in earlier:
            _, cited, _ = build_prompts.retrieve(corpus, asked, history)
            if not cited:
                return None
            history.append(PriorTurn(
                question=asked, answer=build_prompts.templated_answer(cited), pids=tuple(c.pid for c in cited[:3]),
            ))
        kind, candidates, extracts = build_prompts.retrieve(corpus, question, history)
        if not candidates:
            return None
        if family.absent:
            signal_terms(question)
        seen.add(key)
        return Item(
            id=f"{split}-{seed}-{family.name}-{index:02d}", seed=seed, family=family.name, kind=kind,
            question=question, absent=family.absent, history=tuple(history),
            candidates=candidates, extracts=extracts,
        )
    return None


def items_for(corpus: build_prompts.Corpus, split: str, seed: int, per_family: int) -> tuple[list[Item], int]:
    items: list[Item] = []
    skipped = 0
    seen: set[tuple[str, ...]] = set()
    for family in questions.families(splits.SPLIT_SIDE[split]):
        for i in range(per_family):
            item = _draw(corpus, seed, family, i, seen, split)
            if item is None:
                skipped += 1
            else:
                items.append(item)
    return items, skipped


# ---- one answer -------------------------------------------------------------------------


class _Probe:
    """Passes calls to the real client and keeps the first draft reply, which the app may rewrite."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.first_reply: str | None = None

    def chat_turn(self, model: str, messages: Any, **kwargs: Any) -> Any:
        result = self.inner.chat_turn(model, messages, **kwargs)
        if not kwargs.get("format") and self.first_reply is None:
            self.first_reply = result.content or ""
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


def has_structure(answer: str) -> bool:
    return all(pattern.search(answer) for pattern in HEADINGS.values())


def not_in_extract_ok(answer: str, question: str) -> bool:
    """The answer says the value is not available and gives no number for the asked signal.

    A number counts when it sits in a sentence (or line) that names the signal; digits inside a PID do not.
    """
    if not NOT_AVAILABLE.search(answer):
        return False
    terms = signal_terms(question)
    for sentence in _SENTENCES.split(answer):
        if any(re.search(rf"\b{re.escape(t)}\b", sentence, re.I) for t in terms) and _NUMBER.search(sentence):
            return False
    return True


def answer_one(item: Item, client: Any, model: str, repeat: int) -> dict[str, Any]:
    import src.llm.agent as agent
    from src.llm.grounding import find_ungrounded_numbers
    from src.llm.retrieve import answer_from_retrieval

    spy = _GateSpy()
    probe = _Probe(client)
    verified_text: list[str] = []  # what the app held after rewrite or withholding, before it strips numbers
    error: str | None = None
    final = ""
    trace: list[str] = []
    started = time.perf_counter()
    with ExitStack() as stack:
        stack.enter_context(
            mock.patch.object(agent, "answer_problems", spy.timed(agent.answer_problems, spy.after_problems))
        )
        timed_verify = spy.timed(agent.verify_answer, spy.after_verify)

        def verify(text: str, *args: Any, **kwargs: Any) -> Any:
            verified_text.append(text)
            return timed_verify(text, *args, **kwargs)

        stack.enter_context(mock.patch.object(agent, "verify_answer", verify))
        stack.enter_context(mock.patch.object(agent, "check_advice", spy.timed(agent.check_advice)))
        try:
            result = answer_from_retrieval(
                question=item.question, model=model, candidates=item.candidates, extracts=item.extracts,
                client=probe, kind=item.kind, history=item.history,  # type: ignore[arg-type]
            )
            final, trace = result.answer, list(result.tool_trace)
        except Exception as exc:  # a failed call is a result for the model, not a crash of the run
            error = f"{type(exc).__name__}: {exc}"
    latency = time.perf_counter() - started
    empty = error is None and not (probe.first_reply or "").strip()
    answered = error is None and not empty
    problems = list(spy.first_problems or [])
    sources = list(spy.sources)
    settled = verified_text[0] if verified_text else final
    kinds = classify(problems) if answered else {}
    rewrite = [s for s in trace if s.startswith("rewrite:")]
    return {
        "id": item.id,
        "repeat": repeat,
        "seed": item.seed,
        "family": item.family,
        "kind": item.kind,
        "absent": item.absent,
        "question": item.question,
        "history": [t.question for t in item.history],
        "final": final,
        "after_rewrite": settled,
        "first_draft": spy.first_text,
        "problems": problems,
        "trace": trace,
        "error": error,
        "empty": empty,
        "latency_s": round(latency, 4),
        "gate_s": round(spy.seconds, 4),
        "draft_grounded": answered and not find_ungrounded_numbers(spy.first_text, sources, item.question),
        "rewrite_grounded": answered and not find_ungrounded_numbers(settled, sources, item.question),
        "final_grounded": answered and not find_ungrounded_numbers(final, sources, item.question),
        "gate_pass": answered and spy.first_problems == [],
        "problem_kinds": {
            "ungrounded_number": answered and not kinds["grounded"],
            "directive": bool(kinds.get("directive")),
            "uncited_pid": bool(kinds.get("uncited_pid")),
            "misattributed": bool(kinds.get("misattributed")),
            "causal": bool(kinds.get("causal")),
            "incomplete_list": bool(kinds.get("incomplete_list")),
            "case_fact": bool(kinds.get("case_fact")),
        },
        "withheld": any(s.startswith("withheld:") for s in trace),
        "rewritten": bool(rewrite),
        "rewrite_accepted": "rewrite:accepted" in rewrite,
        "rewrite_still_flagged": "rewrite:still_flagged" in rewrite,
        "structure": answered and has_structure(settled),
        "not_in_extract": (answered and not_in_extract_ok(settled, item.question)) if item.absent else None,
    }


# ---- metrics ----------------------------------------------------------------------------

PROBLEM_KEYS = ("ungrounded_number", "directive", "uncited_pid", "misattributed", "causal", "incomplete_list", "case_fact")


def _rate(flags: Sequence[bool]) -> float | None:
    return sum(flags) / len(flags) if flags else None


def _p95(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, -(-95 * len(ordered) // 100) - 1)]


def group_metrics(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Metrics for one group of answers. Every rate is over all answers asked unless noted."""
    rewritten = [r for r in records if r["rewritten"]]
    absent = [r for r in records if r["absent"]]
    latency = [r["latency_s"] for r in records]
    return {
        "n": len(records),
        "draft_grounding_rate": _rate([r["draft_grounded"] for r in records]),
        # after the rewrite, before the app strips numbers it cannot ground
        "grounding_rate": _rate([r["rewrite_grounded"] for r in records]),
        # the answer the user would see, after the app has replaced ungrounded numbers
        "shipped_grounding_rate": _rate([r["final_grounded"] for r in records]),
        "gate_pass_rate": _rate([r["gate_pass"] for r in records]),
        "problem_rates": {k: _rate([r["problem_kinds"][k] for r in records]) for k in PROBLEM_KEYS},
        "withheld_rate": _rate([r["withheld"] for r in records]),
        "rewrite_rate": _rate([r["rewritten"] for r in records]),
        # over answers that were rewritten; None when there were none
        "rewrite_success_rate": _rate([r["rewrite_accepted"] for r in rewritten]),
        "structure_rate": _rate([r["structure"] for r in records]),
        "not_in_extract_n": len(absent),
        # over questions about a signal the extract lacks; None when there were none
        "not_in_extract_rate": _rate([bool(r["not_in_extract"]) for r in absent]),
        "empty_rate": _rate([r["empty"] for r in records]),
        "error_rate": _rate([r["error"] is not None for r in records]),
        "latency_mean_s": statistics.fmean(latency) if latency else None,
        "latency_p95_s": _p95(latency),
    }


def _flat(metrics: dict[str, Any]) -> dict[str, float]:
    flat: dict[str, float] = {}
    for key, value in metrics.items():
        if isinstance(value, dict):
            flat.update({f"{key}.{k}": v for k, v in value.items() if v is not None})
        elif value is not None and key != "n":
            flat[key] = value
    return flat


def across_repeats(per_repeat: Sequence[dict[str, Any]]) -> tuple[dict[str, float], dict[str, float]]:
    """Mean and spread (max minus min) of each metric over the repeats that have it."""
    columns: dict[str, list[float]] = {}
    for metrics in per_repeat:
        for key, value in _flat(metrics).items():
            columns.setdefault(key, []).append(value)
    return (
        {k: statistics.fmean(v) for k, v in columns.items()},
        {k: max(v) - min(v) for k, v in columns.items()},
    )


def summarize(records: Sequence[dict[str, Any]], repeats: int) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in ("overall", *KINDS):
        per_repeat = []
        for rep in range(repeats):
            group = [r for r in records if r["repeat"] == rep and (name == "overall" or r["kind"] == name)]
            per_repeat.append(group_metrics(group))
        mean, spread = across_repeats(per_repeat)
        out[name] = {"per_repeat": per_repeat, "mean": mean, "spread": spread}
    return out


# ---- the run ----------------------------------------------------------------------------


def evaluate(
    split: str, seeds: Sequence[int], n_cases: int, model: str, repeats: int, out: Path,
    *, client_factory: ClientFactory = ollama_factory, allowed_roots: Sequence[Path] = (ALLOWED_ROOT,),
    per_family: int = 3,
) -> dict[str, Any]:
    """Check every argument and Ollama first, then ask every question and write the two files."""
    from tools.eval_answers import build_corpus

    if split not in HELDOUT_SPLITS:
        raise Refused(f"only the {' and '.join(HELDOUT_SPLITS)} splits may be evaluated, not {split!r}")
    resolved = check_out(out, allowed_roots)
    build_prompts.check_seeds(split, seeds)
    if n_cases < 8:
        raise Refused("--n-cases below 8 leaves too few cases to ask about")
    if repeats < 1:
        raise Refused("--repeats must be at least 1")
    if not seeds:
        raise Refused("--seeds names no seed")
    clients = [client_factory(model, repeat_seed(i)) for i in range(repeats)]
    check_installed(clients[0], model)

    made = not resolved.exists()
    work = resolved / "work"
    records: list[dict[str, Any]] = []
    skipped = 0
    try:
        for seed in seeds:
            seed_work = work / f"seed-{seed}"
            with private_env(seed_work):
                engine, frames = build_corpus(seed_work, seed, n_cases)
                corpus = build_prompts.Corpus(engine, frames)
                rows = frames["cases"].sort_values("PID").to_dict("records")
                corpus.facts = [questions.case_facts(row) for row in rows]
                items, missed = items_for(corpus, split, seed, per_family)
                skipped += missed
                for repeat, client in enumerate(clients):
                    records += [answer_one(item, client, model, repeat) for item in items]
        resolved.mkdir(parents=True, exist_ok=True)
        (resolved / "answers.jsonl").write_text(
            "".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in records), encoding="utf-8"
        )
        report = {
            "model": model,
            "split": split,
            "seeds": list(seeds),
            "n_cases": n_cases,
            "repeats": repeats,
            "ollama_seeds": [repeat_seed(i) for i in range(repeats)],
            "per_family": per_family,
            "questions_per_repeat": len(records) // repeats,
            "skipped_questions": skipped,
            "commit": build_prompts._git_commit(),
            "created": datetime.now(UTC).isoformat(timespec="seconds"),
            "metrics": summarize(records, repeats),
        }
        (resolved / "metrics.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    finally:
        shutil.rmtree(work, ignore_errors=True)
        if made and resolved.is_dir() and not any(resolved.iterdir()):
            resolved.rmdir()
    return report


def main(
    argv: Sequence[str] | None = None, *, client_factory: ClientFactory = ollama_factory,
    allowed_roots: Sequence[Path] = (ALLOWED_ROOT,),
) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--split", choices=HELDOUT_SPLITS, required=True)
    parser.add_argument("--seeds", required=True, help="for example 900-901 or 900,902")
    parser.add_argument("--n-cases", type=int, default=30)
    parser.add_argument("--per-family", type=int, default=3, help="questions drawn from each family per seed")
    parser.add_argument("--model", required=True, help="the Ollama model name to ask")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = evaluate(
            args.split, parse_seeds(args.seeds), args.n_cases, args.model, args.repeats, args.out,
            client_factory=client_factory, allowed_roots=allowed_roots, per_family=args.per_family,
        )
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except Unavailable as exc:
        print(str(exc), file=sys.stderr)
        return 3
    overall = report["metrics"]["overall"]["mean"]
    print(json.dumps({"model": report["model"], "questions_per_repeat": report["questions_per_repeat"],
                      "overall_mean": overall}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
