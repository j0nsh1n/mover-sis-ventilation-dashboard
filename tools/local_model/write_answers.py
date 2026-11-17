"""Training answers written by Codex or Kimi, kept only when the app's own checks pass them.

    PYTHONPATH=. .venv/bin/python tools/local_model/write_answers.py \
        --writer codex --seeds 100-119 --out DIR [--per-family 1] [--n-cases 30] [--limit N]

Questions come from the training families on synthetic corpora (the same builders as
``build_prompts`` and ``eval_heldout``). Each one runs through the real
``answer_from_retrieval``: the writer answers the messages the app would send, and the
app's number, case and wording checks judge that first draft. No model checks another
model's answer (owner, 2026-11-17: code is the checker), so the advice check is not run
and a draft the app would send back for a rewrite is dropped, never rewritten.

Owner decisions behind this tool (2026-11-17): Codex writes most answers, Kimi adds
variety; Claude and Grok never write training text. Only training seeds are allowed, and
the output folder must sit under the local-model scratch folder.

Writes into ``--out``: ``answers-<writer>.jsonl`` (every draft with its check results; a
rerun skips ids already there, so a stopped run resumes) and ``train-<writer>.jsonl`` (the
kept drafts as chat examples: the app's messages plus the draft as the assistant turn).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.llm.cloud import CODEX_CLI, CliClient, _split_system, _transcript  # noqa: E402
from src.llm.ollama_client import ChatResult  # noqa: E402
from tools.local_model import build_prompts, questions  # noqa: E402
from tools.local_model.build_prompts import ALLOWED_ROOT, Refused, check_out, parse_seeds, private_env  # noqa: E402
from tools.local_model.eval_heldout import answer_one, has_structure, items_for  # noqa: E402

WRITERS = {"codex": "gpt-6.1-sol", "kimi": "kimi-code/k3.1-pro"}
TIMEOUT_S = 300.0
# Sent to the writer only; the training example keeps the app's messages as they are, so the
# small model learns the exact headings from the app's own prompt.
WRITER_NOTE = (
    "Writer note (not part of the app's message): write the reply the app expects. Use the four "
    "headings exactly as written in the answer shape: **Cases used**, **What the extracts show**, "
    "**Flags & rule meaning**, **Gaps**; then the one-line research disclaimer. Reply with the "
    "answer text only. Do not use tools."
)


class AdviceNotRun(Exception):
    """The app's advice check asks a model to label sentences; this tool lets code be the only checker."""


class KimiCliClient:
    """Kimi Code run once per answer in a new empty folder, printing only its reply."""

    def __init__(self, workdir: Path, *, timeout_s: float = TIMEOUT_S, binary: str | None = None) -> None:
        self.workdir = workdir
        self.timeout_s = timeout_s
        self.binary = binary or shutil.which("kimi")

    def command(self, model: str, prompt: str) -> list[str]:
        if not self.binary:
            raise RuntimeError("the 'kimi' command was not found")
        return [self.binary, "-p", prompt, "-m", model, "--output-format", "stream-json"]

    @staticmethod
    def reply(stream: str) -> str:
        """The last assistant text in Kimi's stream-json output (text mode adds bullets and indents)."""
        replies = []
        for line in stream.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get("role") == "assistant" and isinstance(event.get("content"), str):
                replies.append(event["content"])
        return replies[-1].strip() if replies else ""

    def chat_turn(self, model: str, messages: Sequence[dict[str, Any]], **_ignored: Any) -> ChatResult:
        system, turns = _split_system(messages)
        prompt = f"{system}\n\n{_transcript(turns)}" if system else _transcript(turns)
        self.workdir.mkdir(parents=True, exist_ok=True)
        call_dir = Path(tempfile.mkdtemp(prefix="kimi-", dir=self.workdir))
        try:
            done = subprocess.run(
                self.command(model, prompt), cwd=call_dir, capture_output=True, text=True,
                timeout=self.timeout_s, start_new_session=True, stdin=subprocess.DEVNULL,
            )
        finally:
            shutil.rmtree(call_dir, ignore_errors=True)
        if done.returncode != 0:
            raise RuntimeError(f"kimi exited {done.returncode}: {done.stderr.strip()[-300:]}")
        return ChatResult(content=self.reply(done.stdout))


class FirstDraftOnly:
    """Sends the app's first answer request to the writer and nothing else.

    A later answer request (the app's rewrite) gets the same draft back without a call, so
    a draft with problems costs one call and is dropped. The advice check gets AdviceNotRun,
    which ``check_advice`` turns into "not run".
    """

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.messages: list[dict[str, Any]] | None = None
        self.draft: str | None = None
        self.calls = 0

    def chat_turn(self, model: str, messages: Sequence[dict[str, Any]], **kwargs: Any) -> Any:
        if kwargs.get("format"):
            raise AdviceNotRun
        if self.draft is None:
            self.messages = [dict(m) for m in messages]
            asked = [dict(m) for m in messages]
            last = asked[-1]
            last["content"] = f"{last.get('content', '')}\n\n{WRITER_NOTE}"
            self.calls += 1
            result = self.inner.chat_turn(model, asked, **kwargs)
            self.draft = (result.content or "").strip()
        return ChatResult(content=self.draft)


def drop_reasons(record: dict[str, Any], pids: Sequence[str] = ()) -> list[str]:
    """Why a draft may not become a training example; empty when it is kept.

    ``pids`` are the cases the app put in front of the writer. The app's checks catch wrong
    numbers and wording but not an answer that says nothing, so a kept draft names one of them.
    """
    if record["error"]:
        return [f"error: {record['error']}"]
    if record["empty"]:
        return ["empty reply"]
    reasons = [f"problem: {p}" for p in record["problems"]]
    if not record["draft_grounded"]:
        reasons.append("a number is not in the extracts")
    if not has_structure(record["first_draft"]):
        reasons.append("missing a part of the answer layout")
    if record["not_in_extract"] is False:
        reasons.append("does not say the asked signal is not available")
    if pids and not any(pid in record["first_draft"] for pid in pids):
        reasons.append("names none of the cases it was given")
    return reasons


def write(
    writer: str, seeds: Sequence[int], out: Path, *, n_cases: int = 30, per_family: int = 1,
    limit: int | None = None, client: Any = None, allowed_roots: Sequence[Path] = (ALLOWED_ROOT,),
) -> dict[str, Any]:
    from tools.eval_answers import build_corpus

    if writer not in WRITERS:
        raise Refused(f"writer must be one of {', '.join(WRITERS)}, not {writer!r}")
    resolved = check_out(out, allowed_roots)
    build_prompts.check_seeds("train", seeds)
    if not seeds:
        raise Refused("--seeds names no seed")
    model = WRITERS[writer]
    calls_dir = resolved / "calls"
    if client is None:
        client = CliClient(CODEX_CLI, calls_dir) if writer == "codex" else KimiCliClient(calls_dir)

    resolved.mkdir(parents=True, exist_ok=True)
    answers_path = resolved / f"answers-{writer}.jsonl"
    train_path = resolved / f"train-{writer}.jsonl"
    done_ids = set()
    if answers_path.exists():
        done_ids = {json.loads(line)["id"] for line in answers_path.read_text(encoding="utf-8").splitlines() if line}
    asked = kept = 0
    work = resolved / "work"
    try:
        for seed in seeds:
            seed_work = work / f"seed-{seed}"
            with private_env(seed_work):
                engine, frames = build_corpus(seed_work, seed, n_cases)
                corpus = build_prompts.Corpus(engine, frames)
                rows = frames["cases"].sort_values("PID").to_dict("records")
                corpus.facts = [questions.case_facts(row) for row in rows]
                items, _missed = items_for(corpus, "train", seed, per_family)
                for item in items:
                    if item.id in done_ids:
                        continue
                    if limit is not None and asked >= limit:
                        break
                    probe = FirstDraftOnly(client)
                    started = time.perf_counter()
                    record = answer_one(item, probe, model, 0)
                    pids = [c.pid for c in item.candidates[: min(len(item.candidates), len(item.extracts), 3)]]
                    reasons = drop_reasons(record, pids)
                    record.update(writer=writer, model=model, calls=probe.calls, drop=reasons,
                                  kept=not reasons, seconds=round(time.perf_counter() - started, 2))
                    asked += 1
                    with answers_path.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
                    if not reasons and probe.messages is not None:
                        kept += 1
                        example = {"id": item.id, "seed": seed, "family": item.family, "writer": writer,
                                   "model": model, "pids": pids,
                                   "messages": [*probe.messages, {"role": "assistant", "content": record["first_draft"]}]}
                        with train_path.open("a", encoding="utf-8") as fh:
                            fh.write(json.dumps(example, sort_keys=True, ensure_ascii=False) + "\n")
                    done_ids.add(item.id)
                    print(f"{item.id}: {'kept' if not reasons else 'dropped (' + reasons[0][:80] + ')'}",
                          flush=True)
            if limit is not None and asked >= limit:
                break
    finally:
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(calls_dir, ignore_errors=True)
    report = {"writer": writer, "model": model, "seeds": list(seeds), "asked": asked, "kept": kept,
              "commit": build_prompts._git_commit(), "finished": datetime.now(UTC).isoformat(timespec="seconds")}
    with (resolved / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(report, sort_keys=True) + "\n")
    return report


def main(argv: Sequence[str] | None = None, *, allowed_roots: Sequence[Path] = (ALLOWED_ROOT,)) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--writer", choices=sorted(WRITERS), required=True)
    parser.add_argument("--seeds", required=True, help="training seeds, for example 100-119")
    parser.add_argument("--n-cases", type=int, default=30)
    parser.add_argument("--per-family", type=int, default=1)
    parser.add_argument("--limit", type=int, default=None, help="stop after this many new drafts")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = write(args.writer, parse_seeds(args.seeds), args.out, n_cases=args.n_cases,
                       per_family=args.per_family, limit=args.limit, allowed_roots=allowed_roots)
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    os.environ.setdefault("MOVER_SILENT", "1")
    sys.exit(main())
