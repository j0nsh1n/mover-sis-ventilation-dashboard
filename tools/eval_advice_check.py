"""
Measure the combined advice check (word patterns + local model) on labeled sentences.

    PYTHONPATH=. python tools/eval_advice_check.py --model holo9b-16k:latest \
        --corpus tests/fixtures/gate_drafts.yaml

The corpus is a YAML file with ``descriptive`` and ``directive`` sentence lists. The
sentences are shuffled and sent in answer-sized groups, one model call per group, so
the latency is the added cost for one answer of that many sentences. Needs a running
local Ollama; not part of pytest.
"""

from __future__ import annotations

import argparse
import random
import statistics
import sys
import time
from pathlib import Path

import yaml

from src.llm.advice_check import TIMEOUT_S, Labeled, label_sentences
from src.llm.agent import find_directive_phrases
from src.llm.ollama_client import OllamaClient


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model", required=True)
    parser.add_argument("--corpus", type=Path, default=Path("tests/fixtures/gate_drafts.yaml"))
    parser.add_argument("--group-size", type=int, default=6)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--timeout", type=float, default=TIMEOUT_S)
    args = parser.parse_args(argv)

    corpus = yaml.safe_load(args.corpus.read_text(encoding="utf-8"))
    items = [(s, False) for s in corpus["descriptive"]] + [(s, True) for s in corpus["directive"]]
    random.Random(args.seed).shuffle(items)
    client = OllamaClient(base_url=args.base_url, timeout_s=args.timeout)

    start = time.perf_counter()
    label_sentences(["The model is loading."], client, args.model, timeout_s=300)
    print(f"model: {args.model}  corpus: {args.corpus}  warm-up call: {time.perf_counter() - start:.1f} s")

    by_model: dict[str, bool] = {}
    latencies: list[float] = []
    not_run: list[str] = []
    for i in range(0, len(items), args.group_size):
        group = [s for s, _ in items[i : i + args.group_size]]
        start = time.perf_counter()
        check = label_sentences(group, client, args.model, timeout_s=args.timeout)
        latencies.append(time.perf_counter() - start)
        if not isinstance(check, Labeled):
            not_run.append(check.reason)
        by_model.update({s: s in check.advice for s in group})

    directive = [s for s, d in items if d]
    descriptive = [s for s, d in items if not d]
    by_pattern = {s: bool(find_directive_phrases(s)) for s, _ in items}

    def caught(s: str) -> bool:
        return by_pattern[s] or by_model[s]

    for name, flagged in (("patterns", by_pattern.get), ("model", by_model.get), ("combined", caught)):
        hits = sum(bool(flagged(s)) for s in directive)
        false_pos = sum(bool(flagged(s)) for s in descriptive)
        print(
            f"{name:>9}: directive caught {hits}/{len(directive)}  "
            f"descriptive flagged {false_pos}/{len(descriptive)}"
        )
    print(
        f"latency per call ({args.group_size} sentences): median {statistics.median(latencies):.2f} s  "
        f"max {max(latencies):.2f} s  calls {len(latencies)}  not run {len(not_run)}"
    )
    for reason in not_run:
        print(f"  not run: {reason}")
    for s in directive:
        if not caught(s):
            print(f"  missed directive: {s}")
    for s in descriptive:
        if caught(s):
            source = "patterns" if by_pattern[s] else "model"
            print(f"  flagged descriptive ({source}): {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
