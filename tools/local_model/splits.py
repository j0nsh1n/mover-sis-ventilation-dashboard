"""Which seeds and question families may feed training, and the check that keeps them apart."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

TRAIN_SEEDS = range(100, 200)
VALID_SEEDS = range(800, 810)
TEST_SEEDS = range(900, 910)
# 7 is the demo seed (src/desktop/demo_data.py), 8 the eval seed (tests/fixtures/eval_answers.yaml).
NEVER_TRAIN = frozenset({7, 8})

SPLIT_SEEDS = {"train": TRAIN_SEEDS, "valid": VALID_SEEDS, "test": TEST_SEEDS}
# Validation and test questions use the held-out wording, so neither measures memorised phrasing.
SPLIT_SIDE = {"train": "train", "valid": "heldout", "test": "heldout"}


def leaks(train: Sequence[dict[str, Any]], heldout: Sequence[dict[str, Any]]) -> list[str]:
    """Problems that would let held-out results be inflated by training data.

    Records carry ``seed``, ``pids`` (a list) and ``family``.
    """
    problems: list[str] = []
    held_seeds = {r["seed"] for r in heldout}
    held_pids = {p for r in heldout for p in r.get("pids", ())}
    held_families = {r["family"] for r in heldout}
    barred = NEVER_TRAIN | set(VALID_SEEDS) | set(TEST_SEEDS) | held_seeds
    for seed in sorted({r["seed"] for r in train} & barred):
        why = "never trained on" if seed in NEVER_TRAIN else "held out"
        problems.append(f"train uses seed {seed}, which is {why}")
    for seed in sorted({r["seed"] for r in train} & held_seeds):
        problems.append(f"seed {seed} is on both sides")
    for pid in sorted({p for r in train for p in r.get("pids", ())} & held_pids):
        problems.append(f"PID {pid} is on both sides")
    for family in sorted({r["family"] for r in train} & held_families):
        problems.append(f"question family {family!r} is on both sides")
    return problems
