"""
Measure the research-answer gate on recorded model answers over a seeded synthetic EMR.

    PYTHONPATH=. python tools/eval_answers.py --out DIR
    PYTHONPATH=. python tools/eval_answers.py --out DIR --live MODEL

Each entry in tests/fixtures/eval_answers.yaml names a question, how its source
extracts are picked, a recorded draft (and optionally a recorded rewrite), and what the
gate is expected to do. The default mode replays the recorded outputs through the real
path (answer_from_retrieval, run_agent, answer_problems, verify_answer) and needs no
model. ``--live MODEL`` asks a local Ollama model the same questions instead, so the
recorded drafts and the ``expect`` blocks are not used; it never starts or pulls a model
and skips with a message when Ollama is down or the model is not installed. Both modes
write ``metrics.json`` to DIR.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import sys
import time
from collections.abc import Callable, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from tools.desktop_drive import InputError, ScriptedChatClient, load_drafts  # noqa: E402

DEFAULT_ENTRIES = ROOT / "tests" / "fixtures" / "eval_answers.yaml"
PROTOTYPE_DRAFTS = ROOT / "tests" / "fixtures" / "drive_prototype.yaml"
SCRIPTED_MODEL = "recorded-answers"
EXPECT_FLAGS = ("grounded", "directive", "uncited_pid", "rewritten")

_SIGNALS = {
    "pip": "Peak inspiratory pressure",
    "peep": "PEEP",
    "tv": "Tidal volume",
    "etco2": "End-tidal CO2",
    "hr": "Heart rate",
}
_PLACEHOLDER = re.compile(r"\{([a-z0-9_]+)\}")
_WRONG_AGE_OFFSET = 25


@dataclass(frozen=True)
class Entry:
    id: str
    question: str
    search: str
    take: int
    draft: str
    rewrite: str | None
    advice_sentences: tuple[str, ...]
    expect: dict[str, Any]
    prototype: str | None = None

    @property
    def expected_to_fail(self) -> bool:
        e = self.expect
        return not e["grounded"] or e["directive"] or e["uncited_pid"] or e.get("case_fact", False)


@dataclass
class Outcome:
    entry: Entry
    draft: str
    final: str
    observed: dict[str, bool]
    flagged: bool
    notes: list[str]
    draft_grounded: bool
    final_grounded: bool
    latency_s: float
    mismatches: list[str] = field(default_factory=list)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise InputError(message)


def load_entries(path: Path = DEFAULT_ENTRIES) -> tuple[dict[str, Any], list[Entry]]:
    """The corpus header (seed, n_cases) and its validated entries."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise InputError(f"entries file {path} could not be read: {exc}") from exc
    _require(isinstance(data, dict), f"{path} must be a mapping")
    for key in ("seed", "n_cases", "entries"):
        _require(key in data, f"{path} needs '{key}'")
    entries: list[Entry] = []
    for raw in data["entries"]:
        name = raw.get("id", "?")
        prototype = raw.get("prototype")
        if prototype:
            scripted = [d for d in load_drafts(PROTOTYPE_DRAFTS, scenario=prototype) if not _is_labels(d)]
            quoted = yaml.safe_load(PROTOTYPE_DRAFTS.read_text(encoding="utf-8"))[prototype]["question"]
            _require(raw.get("question") == quoted, f"{name}: question differs from the prototype's")
            draft, rewrite = scripted[0], (scripted[1] if len(scripted) > 1 else None)
        else:
            draft, rewrite = raw.get("draft"), raw.get("rewrite")
        _require(isinstance(draft, str) and draft.strip(), f"{name}: needs a draft")
        expect = raw.get("expect") or {}
        for flag in EXPECT_FLAGS:
            _require(isinstance(expect.get(flag), bool), f"{name}: expect.{flag} must be true or false")
        entries.append(
            Entry(
                id=name,
                question=raw["question"],
                search=raw.get("search", raw["question"]),
                take=int(raw.get("take", 3)),
                draft=draft,
                rewrite=rewrite,
                advice_sentences=tuple(raw.get("advice_sentences", ())),
                expect=expect,
                prototype=prototype,
            )
        )
    ids = [e.id for e in entries]
    _require(len(set(ids)) == len(ids), "entry ids must be unique")
    return {"seed": int(data["seed"]), "n_cases": int(data["n_cases"])}, entries


def _is_labels(draft: str) -> bool:
    try:
        return isinstance(json.loads(draft), dict)
    except ValueError:
        return False


def _round_half_up(value: float) -> str:
    return str(math.floor(value + 0.5))


def placeholder_values(candidates: Sequence[Any], extracts: Sequence[str]) -> dict[str, str]:
    """Figures read from the extracts, so a recorded draft quotes what the extract prints."""
    values: dict[str, str] = {}
    for i, (candidate, extract) in enumerate(zip(candidates, extracts, strict=True), start=1):
        values[f"pid_{i}"] = candidate.pid
        if candidate.age is not None:
            values[f"age_{i}"] = _round_half_up(candidate.age)
            values[f"wrong_age_{i}"] = _round_half_up(candidate.age + _WRONG_AGE_OFFSET)
        for key, label in _SIGNALS.items():
            line = next((ln for ln in extract.splitlines() if ln.startswith(f"- {label}:")), "")
            median = re.search(r"median (-?[\d.]+)", line)
            span = re.search(r"min–max \[(-?[\d.]+)–(-?[\d.]+)\]", line)
            if median and span:
                values[f"{key}_median_{i}"] = median.group(1)
                values[f"{key}_min_{i}"], values[f"{key}_max_{i}"] = span.groups()
    return values


def fill(text: str | None, values: dict[str, str], where: str) -> str | None:
    if text is None:
        return None

    def sub(match: re.Match[str]) -> str:
        try:
            return values[match.group(1)]
        except KeyError:
            raise InputError(f"{where}: no value for {{{match.group(1)}}}") from None

    return _PLACEHOLDER.sub(sub, text)


class RecordedClient(ScriptedChatClient):
    """Replays the recorded outputs; answers the advice labeler from the entry's markers."""

    def __init__(self, drafts: Sequence[str], advice_sentences: Sequence[str] = ()) -> None:
        super().__init__(drafts)
        self.advice_sentences = tuple(advice_sentences)

    def chat_turn(self, model: str, messages: Any, **kwargs: Any) -> Any:
        if not kwargs.get("format"):
            return super().chat_turn(model, messages, **kwargs)
        from src.llm.ollama_client import ChatResult

        labels = {}
        for line in str(messages[-1]["content"]).splitlines():
            number, _, sentence = line.partition(". ")
            if number.isdigit():
                advised = any(marker in sentence for marker in self.advice_sentences)
                labels[number] = "advice" if advised else "description"
        return ChatResult(content=json.dumps(labels), tool_calls=[])


@dataclass
class _GateSpy:
    """Records what the gate saw and how long it took, around the real functions."""

    first_problems: list[str] | None = None
    sources: Sequence[str] = ()
    question: str = ""
    first_text: str = ""
    notes: list[str] = field(default_factory=list)
    seconds: float = 0.0

    def timed(self, fn: Callable[..., Any], after: Callable[..., None] | None = None) -> Callable[..., Any]:
        def call(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            try:
                result = fn(*args, **kwargs)
            finally:
                self.seconds += time.perf_counter() - start
            if after is not None:
                after(result, args, kwargs)
            return result

        return call

    def after_problems(self, result: list[str], args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        if self.first_problems is None:
            self.first_problems = list(result)
            self.first_text = args[0]
            self.sources = kwargs.get("sources") or ()
            self.question = kwargs.get("question", "")

    def after_verify(self, result: tuple[str, list[str]], *_: Any) -> None:
        self.notes = list(result[1])


def classify(problems: Sequence[str]) -> dict[str, bool]:
    """Which checks flagged the draft, read from the problems the gate sent back."""
    kinds = {
        "grounded": lambda p: p.startswith("- the number "),
        "directive": lambda p: p.startswith("- prescriptive phrasing"),
        "uncited_pid": lambda p: "is not one of the cited cases" in p,
        "dose": lambda p: p.startswith("- dose does not match"),
    }
    seen = {name: any(test(p) for p in problems) for name, test in kinds.items()}
    known = [p for p in problems if any(test(p) for test in kinds.values())]
    return {
        "grounded": not seen["grounded"],
        "directive": seen["directive"],
        "uncited_pid": seen["uncited_pid"],
        "case_fact": len(known) < len(problems),
    }


def run_entry(
    entry: Entry,
    candidates: Sequence[Any],
    extracts: Sequence[str],
    client: Any,
    model: str,
) -> Outcome:
    import src.llm.agent as agent
    from src.llm.grounding import find_ungrounded_numbers
    from src.llm.retrieve import answer_from_retrieval

    spy = _GateSpy()
    with ExitStack() as stack:
        stack.enter_context(
            mock.patch.object(agent, "answer_problems", spy.timed(agent.answer_problems, spy.after_problems))
        )
        stack.enter_context(
            mock.patch.object(agent, "verify_answer", spy.timed(agent.verify_answer, spy.after_verify))
        )
        stack.enter_context(mock.patch.object(agent, "check_advice", spy.timed(agent.check_advice)))
        result = answer_from_retrieval(
            question=entry.question, model=model, candidates=candidates, extracts=extracts, client=client
        )
    problems = spy.first_problems or []
    observed = classify(problems)
    observed["rewritten"] = any(step.startswith("rewrite:") for step in result.tool_trace)
    sources = list(spy.sources)
    return Outcome(
        entry=entry,
        draft=spy.first_text,
        final=result.answer,
        observed=observed,
        flagged=bool(problems) or bool(spy.notes),
        notes=spy.notes,
        draft_grounded=not find_ungrounded_numbers(spy.first_text, sources, entry.question),
        final_grounded=not find_ungrounded_numbers(result.answer, sources, entry.question),
        latency_s=spy.seconds,
    )


def check_expectations(outcome: Outcome) -> list[str]:
    expect = outcome.entry.expect
    wrong = [
        f"{flag}: expected {expect[flag]}, got {outcome.observed[flag]}"
        for flag in EXPECT_FLAGS
        if outcome.observed[flag] != expect[flag]
    ]
    if outcome.observed["case_fact"] != expect.get("case_fact", False):
        wrong.append(f"case_fact: expected {expect.get('case_fact', False)}, got {outcome.observed['case_fact']}")
    wrong += [f"final lacks {s!r}" for s in expect.get("final_contains", ()) if s not in outcome.final]
    wrong += [f"final still has {s!r}" for s in expect.get("final_excludes", ()) if s in outcome.final]
    return wrong


def _p95(values: Sequence[float]) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1)]


def _rate(hits: int, total: int) -> float | None:
    return round(hits / total, 4) if total else None


def summarize(outcomes: Sequence[Outcome], *, live: bool) -> dict[str, Any]:
    n = len(outcomes)
    latencies = [o.latency_s * 1000 for o in outcomes]
    metrics: dict[str, Any] = {
        "n_entries": n,
        "draft_grounding_rate": _rate(sum(o.draft_grounded for o in outcomes), n),
        "grounding_rate": _rate(sum(o.final_grounded for o in outcomes), n),
        "rewrite_rate": _rate(sum(o.observed["rewritten"] for o in outcomes), n),
        "gate_latency_ms_mean": round(statistics.fmean(latencies), 3) if latencies else None,
        "gate_latency_ms_p95": round(_p95(latencies), 3) if latencies else None,
    }
    if live:
        metrics["flagged_rate"] = _rate(sum(o.flagged for o in outcomes), n)
        return metrics
    flawed = [o for o in outcomes if o.entry.expected_to_fail]
    clean = [o for o in outcomes if not o.entry.expected_to_fail]
    metrics.update(
        n_expected_to_fail=len(flawed),
        n_clean=len(clean),
        gate_catch_rate=_rate(sum(o.flagged for o in flawed), len(flawed)),
        false_flag_rate=_rate(sum(o.flagged for o in clean), len(clean)),
        mismatches={o.entry.id: o.mismatches for o in outcomes if o.mismatches},
    )
    return metrics


def build_corpus(work: Path, seed: int, n_cases: int) -> tuple[Any, dict[str, Any]]:
    """The synthetic EMR, its pipeline output and a keyword retrieval engine over it."""
    from src.pipeline.run import run_pipeline
    from src.pipeline.synthetic import generate_synthetic_emr
    from src.services.retrieval import RetrievalEngine, build_analysis_facts

    emr = generate_synthetic_emr(work / "EMR", n_cases=n_cases, seed=seed)
    data = run_pipeline(
        emr_dir=emr, output_dir=work / "processed", n_cases=n_cases, seed=seed,
        write_sample_csv=False, validate=True, preset="default",
    )
    engine = RetrievalEngine(
        emr=emr,
        cache_path=work / "cache.sqlite",
        analyzed_pids=data["cases"]["PID"].astype(str).tolist(),
        analysis_facts=build_analysis_facts(data["timeseries"], data["flags"]),
        candidate_limit=500,
    )
    frames = {k: data[k] for k in ("cases", "timeseries", "flags", "episodes", "events")}
    return engine, frames


def run_eval(
    entries: Sequence[Entry],
    header: dict[str, Any],
    work: Path,
    *,
    live_client: Any = None,
    live_model: str | None = None,
) -> list[Outcome]:
    """Run every entry; ``live_client`` replaces the recorded outputs."""
    for name in ("config", "data", "tmp"):
        (work / name).mkdir(parents=True, exist_ok=True)
    env = {
        "MOVER_ALLOW_EXTERNAL_OUTPUT": "1",
        "MOVER_EMR_DIR": str(work / "EMR"),
        "XDG_CONFIG_HOME": str(work / "config"),
        "XDG_DATA_HOME": str(work / "data"),
        "TMPDIR": str(work / "tmp"),
    }
    outcomes: list[Outcome] = []
    with mock.patch.dict(os.environ, env):
        engine, frames = build_corpus(work, header["seed"], header["n_cases"])
        engine.extract_limit = max(e.take for e in entries)
        extract_of: dict[str, str] = {}
        for entry in entries:
            picked = list(engine.search(entry.search).candidates[: entry.take])
            missing = [c.pid for c in picked if c.pid not in extract_of]
            if missing:
                texts, fetched = engine.fetch_extracts(missing, session_frames=frames)
                extract_of.update(zip(fetched, texts, strict=True))
            candidates = [c for c in picked if c.pid in extract_of]
            extracts = [extract_of[c.pid] for c in candidates]
            if live_client is not None:
                outcome = run_entry(entry, candidates, extracts, live_client, live_model or "")
            else:
                values = placeholder_values(candidates, extracts)
                filled = replace(
                    entry,
                    draft=fill(entry.draft, values, f"{entry.id} draft"),
                    rewrite=fill(entry.rewrite, values, f"{entry.id} rewrite"),
                    expect={
                        **entry.expect,
                        **{
                            key: [fill(s, values, f"{entry.id} {key}") for s in entry.expect.get(key, ())]
                            for key in ("final_contains", "final_excludes")
                        },
                    },
                )
                recorded = [filled.draft] + ([filled.rewrite] if filled.rewrite else [])
                client = RecordedClient(recorded, filled.advice_sentences)
                outcome = run_entry(filled, candidates, extracts, client, SCRIPTED_MODEL)
                outcome.mismatches = check_expectations(outcome)
            outcomes.append(outcome)
    return outcomes


def _live_client(model: str) -> Any | None:
    from src.llm.ollama_client import OllamaClient

    client = OllamaClient(timeout_s=300.0)
    if not client.is_available():
        print("Ollama is not running on this machine; nothing was measured.")
        return None
    if not any(name == model or name.split(":")[0] == model for name in client.list_models()):
        print(f"Model {model!r} is not installed; nothing was measured.")
        return None
    return client


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True, help="directory for metrics.json and scratch")
    parser.add_argument("--entries", type=Path, default=DEFAULT_ENTRIES)
    parser.add_argument("--live", metavar="MODEL", help="ask this local Ollama model instead of replaying")
    args = parser.parse_args(argv)

    try:
        header, entries = load_entries(args.entries)
    except InputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    client = None
    if args.live:
        client = _live_client(args.live)
        if client is None:
            return 0
    args.out.mkdir(parents=True, exist_ok=True)
    try:
        outcomes = run_eval(entries, header, args.out / "work", live_client=client, live_model=args.live)
    except InputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    metrics = summarize(outcomes, live=client is not None)
    metrics["seed"], metrics["n_cases"] = header["seed"], header["n_cases"]
    if args.live:
        metrics["model"] = args.live
        metrics["timestamp"] = datetime.now(UTC).isoformat(timespec="seconds")
    for o in outcomes:
        status = "flagged" if o.flagged else "passed "
        print(f"{status}  rewritten={o.observed['rewritten']!s:5}  {o.entry.id}" + "".join(f"\n    {m}" for m in o.mismatches))
    (args.out / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metrics, indent=2))
    return 1 if metrics.get("mismatches") else 0


if __name__ == "__main__":
    sys.exit(main())
