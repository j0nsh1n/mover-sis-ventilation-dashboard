"""Stage-by-stage practice on authored fictional checklist cards.

The stages follow the public Sign in, Time out, and Sign out order. The item
wording is practice text for these cards, not a copy of a published checklist
and not a patient record.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from src.user_settings import settings_path

STAGES: tuple[str, ...] = ("Sign in", "Time out", "Sign out")
MARK_DONE = "done"
MARK_NOT_ON_CARD = "not_on_card"


@dataclass(frozen=True)
class CheckItem:
    item_id: str
    stage: str
    prompt: str
    required: bool


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    title: str
    briefing: str
    items: tuple[CheckItem, ...]


@dataclass(frozen=True)
class Debrief:
    complete: bool
    lines: tuple[str, ...]


def scenarios() -> tuple[Scenario, ...]:
    return (
        Scenario(
            "FAKE-WHO-1",
            "Fictional card A",
            "Fiction only. Card FAKE-WHO-1 says: left knee, fictional knee procedure, "
            "consent recorded, pulse oximeter on, no allergy listed, airway routine, "
            "blood-loss note low, people named, antibiotic given, imaging displayed, "
            "counts correct, no specimen, no equipment problem, recovery note is to watch the dressing.",
            _items(imaging_required=True, specimen_required=False),
        ),
        Scenario(
            "FAKE-WHO-2",
            "Fictional card B",
            "Fiction only. Card FAKE-WHO-2 says: right forearm, fictional soft-tissue procedure, "
            "consent recorded, pulse oximeter on, allergy listed as fictional latex, airway routine, "
            "blood-loss note low, people named, antibiotic given, imaging is not used, "
            "counts correct, a fictional tissue sample is listed, no equipment problem, "
            "recovery note is to label the sample.",
            _items(imaging_required=False, specimen_required=True),
        ),
    )


def open_stage(scenario: Scenario, marks: dict[str, str]) -> str | None:
    """Return the stage that still needs marks, or None when every stage is done."""
    for stage in STAGES:
        if not _stage_complete(scenario, stage, marks):
            return stage
    return None


def apply_mark(scenario: Scenario, marks: dict[str, str], item_id: str, mark: str) -> dict[str, str]:
    """Record a mark for the open stage. Later stages stay unchanged until earlier ones are done."""
    item = _item(scenario, item_id)
    if item is None or mark not in {MARK_DONE, MARK_NOT_ON_CARD}:
        return dict(marks)
    stage = open_stage(scenario, marks)
    if stage is not None and item.stage != stage:
        return dict(marks)
    updated = dict(marks)
    updated[item_id] = mark
    return updated


def debrief(scenario: Scenario, marks: dict[str, str]) -> Debrief:
    lines: list[str] = []
    for item in scenario.items:
        mark = marks.get(item.item_id, "")
        if item.required and mark == MARK_NOT_ON_CARD:
            lines.append(f"This item is on the card: {item.prompt}")
        elif item.required and mark != MARK_DONE:
            lines.append(f"Still open: {item.prompt}")
        elif not item.required and mark != MARK_NOT_ON_CARD:
            lines.append(f"Mark as not on this card: {item.prompt}")
    return Debrief(not lines, tuple(lines))


def save_practice(scenario: Scenario, marks: dict[str, str], path: Path | None = None) -> Path:
    target = path or settings_path().parent / "who-practice.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    result = debrief(scenario, marks)
    payload = {
        "scenario_id": scenario.scenario_id,
        "stage": open_stage(scenario, marks),
        "marks": marks,
        "complete": result.complete,
    }
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return target


def load_practice(path: Path | None = None) -> dict:
    target = path or settings_path().parent / "who-practice.json"
    if not target.is_file():
        return {}
    loaded = json.loads(target.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _items(*, imaging_required: bool, specimen_required: bool) -> tuple[CheckItem, ...]:
    return (
        CheckItem("identity", "Sign in", "Say the identity, site, and procedure written on the card.", True),
        CheckItem("consent", "Sign in", "Confirm the card says consent is recorded.", True),
        CheckItem("oximeter", "Sign in", "Confirm the card says the pulse oximeter is on.", True),
        CheckItem("allergy", "Sign in", "State the allergy on the card, or that the card lists none.", True),
        CheckItem("airway", "Sign in", "State the airway note on the card.", True),
        CheckItem("blood", "Sign in", "State the blood-loss note on the card.", True),
        CheckItem("team", "Time out", "Say the people named on the card.", True),
        CheckItem("confirm", "Time out", "Repeat the card's patient, site, and procedure.", True),
        CheckItem("antibiotic", "Time out", "State the antibiotic note on the card.", True),
        CheckItem("concerns", "Time out", "State the surgeon, anesthesia, and nursing notes on the card.", True),
        CheckItem("imaging", "Time out", "State whether imaging is used on this card.", imaging_required),
        CheckItem("procedure", "Sign out", "Record the procedure name on the card.", True),
        CheckItem("counts", "Sign out", "State the count note on the card.", True),
        CheckItem("specimen", "Sign out", "State the specimen note on the card.", specimen_required),
        CheckItem("equipment", "Sign out", "State the equipment note on the card.", True),
        CheckItem("recovery", "Sign out", "State the recovery note on the card.", True),
    )


def _stage_complete(scenario: Scenario, stage: str, marks: dict[str, str]) -> bool:
    for item in scenario.items:
        if item.stage != stage:
            continue
        mark = marks.get(item.item_id, "")
        if item.required and mark != MARK_DONE:
            return False
        if not item.required and mark != MARK_NOT_ON_CARD:
            return False
    return True


def _item(scenario: Scenario, item_id: str) -> CheckItem | None:
    for item in scenario.items:
        if item.item_id == item_id:
            return item
    return None
