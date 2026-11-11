"""Local practice history for fictional handoffs."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path

from src.modules.handoff_trainer.grading import SERIES_GAP_KINDS, Grade
from src.user_settings import settings_path

_TEXT_LIMIT = 8000
UNREADABLE = "Some saved attempts could not be read."
READ_FAILED = "Saved attempts could not be read."


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


class Attempts(tuple[dict, ...]):
    """The saved attempts, oldest first. ``unreadable`` counts the lines that were skipped."""

    unreadable: int

    def __new__(cls, rows: Iterable[dict] = (), unreadable: int = 0) -> Attempts:
        made = super().__new__(cls, rows)
        made.unreadable = unreadable
        return made


def load_attempts(path: Path | None = None) -> Attempts:
    """Every readable attempt. A line that is not an attempt is skipped and counted; the file is never changed."""
    target = path or history_path()
    if not target.is_file():
        return Attempts()
    rows: list[dict] = []
    skipped = 0
    for line in target.read_bytes().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line.decode("utf-8"))
        except ValueError:
            row = None
        if isinstance(row, dict):
            rows.append(row)
        else:
            skipped += 1
    return Attempts(rows, skipped)


def found_counts(items: Iterable[tuple[str, str]]) -> tuple[int, int]:
    """(found, of how many) from (status, kind) pairs: the timeline items a handoff matched, out of those it
    matched or left out, and a missing series it stated a value for.

    A claim about a finding or drug the timeline does not show is not one of the timeline's items, so it
    counts in neither number. A missing MAP or SpO2 series is its own item, so a wrong claim about it still
    counts in the total.
    """
    listed = list(items)
    found = sum(1 for status, _ in listed if status == "mentioned")
    total = sum(1 for status, kind in listed
                if status in ("mentioned", "missed") or (status == "unsupported" and kind in SERIES_GAP_KINDS))
    return found, total


def attempt_counts(row: Mapping) -> tuple[int, int]:
    """``found_counts`` of a saved attempt, from either format."""
    grades = row.get("grades", [])
    if not isinstance(grades, list):
        grades = []
    return found_counts((str(item.get("status", "")), str(item.get("kind", "")))
                        for item in grades if isinstance(item, dict))
