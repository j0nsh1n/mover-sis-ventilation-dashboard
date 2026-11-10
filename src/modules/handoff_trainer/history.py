"""Local practice history for fictional handoffs."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path

from src.modules.handoff_trainer.grading import Grade
from src.user_settings import settings_path

_TEXT_LIMIT = 8000


def history_path() -> Path:
    return settings_path().parent / "handoff-practice.jsonl"


def record_attempt(
    case_id: str,
    handoff: str,
    grades: tuple[Grade, ...],
    path: Path | None = None,
    sections: Mapping[str, str] | None = None,
) -> Path:
    """Append one attempt. ``sections`` keeps the five boxes apart; attempts saved without it hold only ``handoff``."""
    target = path or history_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload: dict = {
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "case_id": case_id,
        "handoff": handoff[:_TEXT_LIMIT],
    }
    if sections is not None:
        payload["sections"] = {key: text[:_TEXT_LIMIT] for key, text in sections.items()}
    payload["grades"] = [{"status": item.status, "kind": item.kind, "evidence": item.evidence} for item in grades]
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload) + "\n")
    return target


def load_attempts(path: Path | None = None) -> tuple[dict, ...]:
    target = path or history_path()
    if not target.is_file():
        return ()
    rows: list[dict] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return tuple(rows)


def found_counts(statuses: Iterable[str]) -> tuple[int, int]:
    """(found, of how many): the timeline items a handoff matched, out of those it matched or left out.

    A claim the timeline does not show is not one of the timeline's items, so it counts in neither number.
    """
    listed = list(statuses)
    found = listed.count("mentioned")
    return found, found + listed.count("missed")


def attempt_counts(row: Mapping) -> tuple[int, int]:
    """``found_counts`` of a saved attempt, from either format."""
    return found_counts(str(item.get("status", "")) for item in row.get("grades", []))
