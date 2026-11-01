"""Local practice history for fictional handoffs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from src.modules.handoff_trainer.grading import Grade
from src.user_settings import settings_path


def history_path() -> Path:
    return settings_path().parent / "handoff-practice.jsonl"


def record_attempt(case_id: str, handoff: str, grades: tuple[Grade, ...], path: Path | None = None) -> Path:
    target = path or history_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "case_id": case_id,
        "handoff": handoff[:8000],
        "grades": [{"status": item.status, "kind": item.kind, "evidence": item.evidence} for item in grades],
    }
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
