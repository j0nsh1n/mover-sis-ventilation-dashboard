"""Capture the prompts the app sends for research questions, for fine-tuning data.

    PYTHONPATH=. .venv/bin/python tools/local_model/build_prompts.py \
        --split train --seeds 100-104 --n-cases 30 --out DIR [--model-tag TAG]

Each question goes through the app's own answer path (``answer_from_retrieval``) with a
client that records the messages instead of calling a model, so a prompt is exactly what
the app would send. Only synthetic corpora are built; no model runs. The output folder
must sit under the local-model scratch folder, and the real settings folder, the real
EMR and the wave folders are always refused.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import random
import subprocess
import sys
from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.local_model import questions, splits  # noqa: E402

ALLOWED_ROOT = Path("/home/jonathans/Projects/medical/mover/_scratch/local-model")
FORBIDDEN = (
    Path.home() / ".config" / "mover-suite",
    Path("/var/mnt/games/EMR"),
    Path("/var/mnt/games/sis_wave_v2"),
)
DEFAULT_MODEL_TAG = "olmo-3-7b-instruct"
DRAFT_PLACEHOLDER = "Placeholder reply: no model has run."


class Refused(Exception):
    """The request would reach a place the build must not touch."""


def _inside(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def _refuse_banned(path: Path, shown: Path) -> None:
    for banned in FORBIDDEN:
        # abspath is string-only, so a banned folder is never touched; on Windows it adds the drive the written path has.
        if _inside(path, Path(os.path.abspath(banned))):
            raise Refused(f"{shown} is inside {banned}, which this tool never touches")


def check_out(out: Path, allowed_roots: Sequence[Path] = (ALLOWED_ROOT,)) -> Path:
    """The resolved output folder, or Refused.

    The banned folders are compared by their written path first, so nothing in them is
    touched even to resolve a link; the resolved path is compared again for symlinks.
    """
    written = Path(os.path.abspath(out.expanduser()))
    _refuse_banned(written, out)
    resolved = written.resolve()
    _refuse_banned(resolved, out)
    if not any(_inside(resolved, root.resolve()) for root in allowed_roots):
        shown = ", ".join(str(r) for r in allowed_roots)
        raise Refused(f"{out} is outside the allowed folder ({shown})")
    return resolved


def parse_seeds(text: str) -> list[int]:
    seeds: list[int] = []
    for part in text.split(","):
        low, dash, high = part.strip().partition("-")
        try:
            seeds.extend(range(int(low), int(high) + 1) if dash else [int(low)])
        except ValueError:
            raise Refused(f"cannot read seeds {text!r}; write 100-104 or 100,102") from None
    return sorted(set(seeds))


def check_seeds(split: str, seeds: Sequence[int]) -> None:
    allowed = splits.SPLIT_SEEDS[split]
    for seed in seeds:
        if seed in splits.NEVER_TRAIN:
            raise Refused(f"seed {seed} is never used for training or held-out sets")
        if seed not in allowed:
            raise Refused(f"seed {seed} does not belong to the {split} split ({allowed})")


class CapturingClient:
    """Records every chat call. Structured calls (the advice check) get an empty-label reply."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def chat_turn(self, model: str, messages: Any, **kwargs: Any) -> Any:
        from src.llm.ollama_client import ChatResult

        self.calls.append({"model": model, "messages": copy.deepcopy(list(messages)), "kwargs": dict(kwargs)})
        if kwargs.get("format"):
            labels = dict.fromkeys(kwargs["format"]["required"], "description")
            return ChatResult(content=json.dumps(labels))
        return ChatResult(content=DRAFT_PLACEHOLDER)

    def first_draft(self) -> list[dict[str, Any]]:
        for call in self.calls:
            if not call["kwargs"].get("format"):
                return list(call["messages"])
        raise RuntimeError("the answer path made no draft call")


def templated_answer(candidates: Sequence[Any]) -> str:
    """A stand-in for an earlier answer, from case facts only. Real answers replace it later."""
    lines = ["**Cases used**"]
    lines += [
        f"- {c.pid}: age {c.age:g}, {c.gender}, {c.procedure}" if c.age is not None else f"- {c.pid}: {c.procedure}"
        for c in candidates[:3]
    ]
    lines += [
        "**What the extracts show** - the ventilator ranges and flags are in the source extracts.",
        "**Gaps** - anything the extracts do not record is not stated here.",
        "Research and education only.",
    ]
    return "\n".join(lines)


@dataclass
class Corpus:
    engine: Any
    frames: dict[str, Any]
    facts: list[questions.CaseFacts] = field(default_factory=list)


def retrieve(corpus: Corpus, question: str, history: Sequence[Any]) -> tuple[str, list[Any], list[str]]:
    """Kind, candidates and extracts the desktop worker would pick (ResearchWorker.run)."""
    from src.desktop.research_workers import _compare_search, _scenario_search
    from src.llm.history import cited_then_ranked, follow_up_query
    from src.llm.intent import classify

    engine = corpus.engine

    def quiet(_: Any) -> None:
        return None

    sent = tuple(history)
    intent = classify(question)
    if intent.kind == "scenario":
        result, chosen = _scenario_search(engine, intent, question, quiet)
        ranked = list(result.candidates)
    elif intent.kind == "compare":
        result, chosen = _compare_search(engine, intent, question, quiet)
        ranked = list(chosen)
    else:
        result = engine.search(question)
        chosen = list(result.candidates[: engine.extract_limit])
        ranked = list(result.candidates)
    if sent and intent.kind == "question":
        ranked = list(engine.search(follow_up_query(question, sent)).candidates)
    if not (chosen or sent):
        return intent.kind, [], []
    pool = ranked if sent and intent.kind != "compare" else list(chosen)
    if sent:
        ordered = cited_then_ranked(sent, [c.pid for c in pool])
    else:
        ordered = [c.pid for c in pool][: engine.extract_limit]
    extracts, fetched = engine.fetch_extracts(ordered, session_frames=corpus.frames)
    by_pid = {c.pid: c for c in pool}
    missing = [str(p) for p in fetched if str(p) not in by_pid]
    if missing:
        for candidate in engine.candidates_for(missing):
            by_pid[candidate.pid] = candidate
    extract_for = dict(zip((str(p) for p in fetched), extracts, strict=True))
    candidates = [by_pid[str(p)] for p in fetched if str(p) in by_pid]
    return intent.kind, candidates, [extract_for[c.pid] for c in candidates]


def capture(
    corpus: Corpus, question: str, history: Sequence[Any], model_tag: str
) -> tuple[str, list[Any], list[dict[str, Any]]] | None:
    """The draft prompt for one question, or None when the search finds no case."""
    from src.llm.retrieve import answer_from_retrieval

    kind, candidates, extracts = retrieve(corpus, question, history)
    if not candidates:
        return None
    client = CapturingClient()
    answer_from_retrieval(
        question=question, model=model_tag, candidates=candidates, extracts=extracts,
        client=client, kind=kind, history=tuple(history),  # type: ignore[arg-type]
    )
    return kind, candidates[:3], client.first_draft()


@contextmanager
def private_env(work: Path):
    """Point every settings, data, EMR and temp folder at this seed's own work folder."""
    dirs = {name: work / name for name in ("config", "data", "tmp")}
    for folder in dirs.values():
        folder.mkdir(parents=True, exist_ok=True)
    env = {
        "MOVER_CONFIG_DIR": str(dirs["config"]),
        "XDG_CONFIG_HOME": str(dirs["config"]),
        "XDG_DATA_HOME": str(dirs["data"]),
        "MOVER_EMR_DIR": str(work / "EMR"),
        "TMPDIR": str(dirs["tmp"]),
        "MOVER_ALLOW_EXTERNAL_OUTPUT": "1",
    }
    with mock.patch.dict(os.environ, env):
        yield


def _one_record(
    corpus: Corpus, split: str, seed: int, family: questions.Family, index: int,
    seen: set[tuple[str, ...]], model_tag: str,
) -> dict[str, Any] | None:
    """The record for one draw from a family, retrying a draw that repeats an earlier question."""
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
            shot = capture(corpus, asked, history, model_tag)
            if shot is None:
                return None
            history.append(PriorTurn(question=asked, answer=templated_answer(shot[1]),
                                     pids=tuple(c.pid for c in shot[1])))
        shot = capture(corpus, question, history, model_tag)
        if shot is None:
            return None
        seen.add(key)
        kind, cited, messages = shot
        return {
            "id": f"{split}-{seed}-{family.name}-{index:02d}",
            "split": split,
            "seed": seed,
            "kind": kind,
            "family": family.name,
            "question": question,
            "pids": [c.pid for c in cited],
            "history": [{"question": t.question, "answer": t.answer} for t in history],
            "messages": messages,
            "model_tag": model_tag,
        }
    return None


def records_for_seed(
    split: str, seed: int, n_cases: int, work: Path, model_tag: str, per_family: int
) -> tuple[list[dict[str, Any]], int]:
    """Records for one seed, and how many questions found no case and were skipped."""
    from tools.eval_answers import build_corpus

    side = splits.SPLIT_SIDE[split]
    records: list[dict[str, Any]] = []
    skipped = 0
    with private_env(work):
        engine, frames = build_corpus(work, seed, n_cases)
        corpus = Corpus(engine, frames)
        rows = frames["cases"].sort_values("PID").to_dict("records")
        corpus.facts = [questions.case_facts(row) for row in rows]
        seen: set[tuple[str, ...]] = set()
        for family in questions.families(side):
            for i in range(per_family):
                record = _one_record(corpus, split, seed, family, i, seen, model_tag)
                if record is None:
                    skipped += 1
                else:
                    records.append(record)
    return records, skipped


def _git_commit() -> str:
    try:
        done = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=True, timeout=20
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return done.stdout.strip()


def build(
    split: str, seeds: Sequence[int], n_cases: int, out: Path, model_tag: str,
    *, allowed_roots: Sequence[Path] = (ALLOWED_ROOT,), per_family: int = 3,
) -> dict[str, Any]:
    """Validate every argument, then write ``<out>/<split>.jsonl`` and ``manifest.json``."""
    resolved = check_out(out, allowed_roots)
    check_seeds(split, seeds)
    if n_cases < 8:
        raise Refused("--n-cases below 8 leaves too few cases to ask about")
    records: list[dict[str, Any]] = []
    skipped = 0
    for seed in seeds:
        # Each seed's scratch lives beside the output, so nothing outside it is written.
        got, missed = records_for_seed(split, seed, n_cases, resolved / "work" / f"seed-{seed}", model_tag, per_family)
        records += got
        skipped += missed
    resolved.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in records)
    (resolved / f"{split}.jsonl").write_text(body, encoding="utf-8")
    counts: dict[str, dict[str, int]] = {"kind": {}, "family": {}}
    for r in records:
        for key in counts:
            counts[key][r[key]] = counts[key].get(r[key], 0) + 1
    manifest = {
        "commit": _git_commit(),
        "split": split,
        "seeds": list(seeds),
        "n_cases": n_cases,
        "per_family": per_family,
        "records": len(records),
        "skipped_questions": skipped,
        "counts": counts,
        "model_tag": model_tag,
        "history_answers": "templated stand-ins built from case facts, not model output",
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    (resolved / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--split", choices=sorted(splits.SPLIT_SEEDS), required=True)
    parser.add_argument("--seeds", required=True, help="for example 100-104 or 100,102")
    parser.add_argument("--n-cases", type=int, default=30)
    parser.add_argument("--per-family", type=int, default=3, help="questions drawn from each family per seed")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model-tag", default=DEFAULT_MODEL_TAG)
    args = parser.parse_args(argv)
    try:
        manifest = build(
            args.split, parse_seeds(args.seeds), args.n_cases, args.out, args.model_tag,
            per_family=args.per_family,
        )
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({k: manifest[k] for k in ("records", "skipped_questions", "counts")}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
