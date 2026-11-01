"""Grade a handoff against a fictional timeline.

The rules come from the handoff trainer spec. Keyword presence alone is not
a completed handoff. Nothing here reads an EMR folder or recommends treatment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

VASOPRESSORS: tuple[str, ...] = (
    "Phenylephrine",
    "Ephedrine",
    "Vasopressin",
    "Epinephrine",
    "Norepinephrine",
)
INOTROPES: tuple[str, ...] = (
    "Dopamine",
    "Dobutamine",
    "Milrinone",
    "Isoproterenol",
)
TRACKED_DRUGS: tuple[str, ...] = VASOPRESSORS + INOTROPES
EXCLUDED_DRUG = "phenylephrine nasal"

MAP_LOW = 65.0
SPO2_LOW = 90.0
MAP_RANGE = (20.0, 200.0)
SPO2_RANGE = (50.0, 100.0)
HYPO_SPAN_MINUTES = 5
ART_MAX_GAP = 1
CUFF_MAX_GAP = 6
SPO2_MAX_GAP = 1

_HYPO_CONCEPT = ("hypotension", "low map", "low blood pressure", "map under 65", "map below 65")
_HYPO_ANCHOR = ("minute", "min", "sustained", "65", "arterial", "noninvasive", "cuff", "mmhg")
_DESAT_CONCEPT = ("desatur", "spo2", "sp02", "oxygen saturation", "low oxygen")
_DESAT_ANCHOR = ("90", "minute", "min", "sustained", "percent", "%")
_NEGATION = re.compile(r"\b(no|not|without|denies|denied|never)\b")
_SPO2_NUMBER = re.compile(r"\b(spo2|sp02|saturation)\b[^\n.]{0,30}\d")
_MAP_NUMBER = re.compile(r"\b(map|blood pressure)\b[^\n.]{0,30}\d")


@dataclass(frozen=True)
class Sample:
    minute: int
    map_art: float | None = None
    nmap: float | None = None
    spo2: float | None = None


@dataclass(frozen=True)
class Dose:
    drug: str
    minute: int
    end_minute: int | None = None
    amount: float | None = None
    unit: str = ""


@dataclass(frozen=True)
class PracticeCase:
    case_id: str
    title: str
    samples: tuple[Sample, ...]
    doses: tuple[Dose, ...]


@dataclass(frozen=True)
class TimelineEvent:
    kind: str
    evidence: str
    drug: str | None = None
    dose_kind: str | None = None


@dataclass(frozen=True)
class Grade:
    status: str
    kind: str
    evidence: str


def detect(case: PracticeCase) -> tuple[TimelineEvent, ...]:
    """Return timeline events a handoff has to account for."""
    events: list[TimelineEvent] = []
    events.extend(_pressure_events(case.samples))
    events.extend(_saturation_events(case.samples))
    events.extend(_drug_events(case.doses))
    return tuple(events)


def grade(case: PracticeCase, handoff: str) -> tuple[Grade, ...]:
    """Score one handoff as mentioned, missed, or unsupported."""
    text = " ".join(handoff.casefold().split())
    events = detect(case)
    items: list[Grade] = []
    for event in events:
        if event.kind == "hypotension":
            items.extend(_grade_episode(event, text, _HYPO_CONCEPT, _HYPO_ANCHOR))
        elif event.kind == "desaturation":
            items.extend(_grade_episode(event, text, _DESAT_CONCEPT, _DESAT_ANCHOR))
        elif event.kind in {"vasopressor", "inotrope"} and event.drug is not None:
            items.extend(_grade_drug(event, text))
        elif event.kind == "missing_map":
            items.append(_grade_gap(event, text, ("map was not recorded", "blood pressure was not recorded", "no map reading"), _map_value_claim))
        elif event.kind == "missing_spo2":
            items.append(_grade_gap(event, text, ("spo2 was not recorded", "sp02 was not recorded", "oxygen saturation was not recorded", "no spo2 reading"), _spo2_value_claim))
    items.extend(_unsupported_absent(text, events))
    return tuple(items)


def timeline_text(case: PracticeCase) -> str:
    """Show the fictional numbers the learner is grading, without a verdict."""
    lines = [case.title, f"Fictional id {case.case_id}."]
    if not case.samples:
        lines.append("No pressure or saturation rows.")
    for sample in sorted(case.samples, key=lambda item: item.minute):
        parts = [f"minute {sample.minute}"]
        parts.append(_cell("arterial MAP", sample.map_art, "mmHg"))
        parts.append(_cell("noninvasive MAP", sample.nmap, "mmHg"))
        parts.append(_cell("SpO2", sample.spo2, "%"))
        lines.append(", ".join(parts))
    if not case.doses:
        lines.append("No medication rows.")
    for dose in case.doses:
        kind = _dose_kind(dose)
        amount = "dose not recorded" if dose.amount is None else f"{_number(dose.amount)} {dose.unit}".strip()
        if kind == "infusion" and dose.end_minute is not None:
            span = f"from minute {dose.minute} to minute {dose.end_minute}"
        else:
            span = f"at minute {dose.minute}"
        lines.append(f"{dose.drug} {amount} {span} ({kind}).")
    return "\n".join(lines)


def _pressure_events(samples: tuple[Sample, ...]) -> tuple[TimelineEvent, ...]:
    arterial = _usable(samples, lambda sample: sample.map_art, MAP_RANGE)
    if arterial:
        runs = _low_runs(arterial, MAP_LOW, ART_MAX_GAP, HYPO_SPAN_MINUTES, minimum_count=2)
        return tuple(
            TimelineEvent(
                "hypotension",
                f"Arterial MAP stayed under 65 mmHg from minute {run[0][0]} to minute {run[-1][0]}.",
            )
            for run in runs
        )
    cuff = _usable(samples, lambda sample: sample.nmap, MAP_RANGE)
    if cuff:
        runs = _low_runs(cuff, MAP_LOW, CUFF_MAX_GAP, HYPO_SPAN_MINUTES, minimum_count=2)
        return tuple(
            TimelineEvent(
                "hypotension",
                f"Noninvasive MAP stayed under 65 mmHg from minute {run[0][0]} to minute {run[-1][0]}.",
            )
            for run in runs
        )
    if samples and not arterial and not cuff:
        return (TimelineEvent("missing_map", "No usable MAP reading is on this timeline."),)
    return ()


def _saturation_events(samples: tuple[Sample, ...]) -> tuple[TimelineEvent, ...]:
    usable = _usable(samples, lambda sample: sample.spo2, SPO2_RANGE)
    if not usable:
        if samples:
            return (TimelineEvent("missing_spo2", "No usable SpO2 reading is on this timeline."),)
        return ()
    runs = _low_runs(usable, SPO2_LOW, SPO2_MAX_GAP, 0, minimum_count=2)
    return tuple(
        TimelineEvent(
            "desaturation",
            f"SpO2 stayed under 90% from minute {run[0][0]} to minute {run[-1][0]}.",
        )
        for run in runs
    )


def _drug_events(doses: tuple[Dose, ...]) -> tuple[TimelineEvent, ...]:
    events: list[TimelineEvent] = []
    for dose in doses:
        canonical = _canonical_drug(dose.drug)
        if canonical is None:
            continue
        kind = "vasopressor" if canonical in VASOPRESSORS else "inotrope"
        dose_kind = _dose_kind(dose)
        if dose.amount is None:
            amount = "dose not recorded"
        else:
            amount = f"{_number(dose.amount)} {dose.unit}".strip()
        if dose_kind == "infusion" and dose.end_minute is not None:
            when = f"from minute {dose.minute} to minute {dose.end_minute}"
        else:
            when = f"at minute {dose.minute}"
        events.append(
            TimelineEvent(
                kind,
                f"{canonical} {amount} {when}.",
                drug=canonical,
                dose_kind=dose_kind,
            )
        )
    return tuple(events)


def _usable(
    samples: tuple[Sample, ...],
    getter,
    bounds: tuple[float, float],
) -> list[tuple[int, float]]:
    low, high = bounds
    points: list[tuple[int, float]] = []
    for sample in samples:
        value = getter(sample)
        if value is None or value < low or value > high:
            continue
        points.append((sample.minute, float(value)))
    points.sort()
    return points


def _low_runs(
    points: list[tuple[int, float]],
    threshold: float,
    max_gap: int,
    span_must_exceed: int,
    *,
    minimum_count: int,
) -> list[list[tuple[int, float]]]:
    runs: list[list[tuple[int, float]]] = []
    current: list[tuple[int, float]] = []
    previous: int | None = None
    for minute, value in points:
        if previous is not None and minute - previous > max_gap and current:
            runs.append(current)
            current = []
        if value < threshold:
            current.append((minute, value))
        elif current:
            runs.append(current)
            current = []
        previous = minute
    if current:
        runs.append(current)
    return [
        run
        for run in runs
        if len(run) >= minimum_count and run[-1][0] - run[0][0] > span_must_exceed
    ]


def _canonical_drug(name: str) -> str | None:
    folded = " ".join(name.split()).casefold()
    if folded == EXCLUDED_DRUG:
        return None
    for drug in TRACKED_DRUGS:
        if folded == drug.casefold():
            return drug
    return None


def _dose_kind(dose: Dose) -> str:
    if dose.end_minute is None or dose.end_minute == dose.minute:
        return "bolus"
    if dose.end_minute > dose.minute:
        return "infusion"
    return "end time is before the start time"


def _grade_episode(
    event: TimelineEvent,
    text: str,
    concepts: tuple[str, ...],
    anchors: tuple[str, ...],
) -> tuple[Grade, ...]:
    if _positive(text, concepts) and _positive(text, anchors):
        return (Grade("mentioned", event.kind, event.evidence),)
    if _negated(text, concepts):
        return (
            Grade("unsupported", event.kind, f"The handoff says this was absent. {event.evidence}"),
            Grade("missed", event.kind, event.evidence),
        )
    return (Grade("missed", event.kind, event.evidence),)


def _grade_drug(event: TimelineEvent, text: str) -> tuple[Grade, ...]:
    drug = event.drug or ""
    if _names_drug(text, drug, negated=True) and not _names_drug(text, drug, negated=False):
        return (
            Grade("unsupported", event.kind, f"The handoff says {drug} was absent. {event.evidence}"),
            Grade("missed", event.kind, event.evidence),
        )
    if _names_drug(text, drug, negated=False):
        return (Grade("mentioned", event.kind, event.evidence),)
    return (Grade("missed", event.kind, event.evidence),)


def _grade_gap(event: TimelineEvent, text: str, acknowledgements: tuple[str, ...], value_claim) -> Grade:
    if value_claim(text):
        return Grade("unsupported", event.kind, f"The handoff states a value. {event.evidence}")
    if any(phrase in text for phrase in acknowledgements):
        return Grade("mentioned", event.kind, event.evidence)
    return Grade("missed", event.kind, event.evidence)


def _unsupported_absent(text: str, events: tuple[TimelineEvent, ...]) -> tuple[Grade, ...]:
    items: list[Grade] = []
    present = {event.drug for event in events if event.drug}
    cleaned = text.replace(EXCLUDED_DRUG, " ")
    for drug in TRACKED_DRUGS:
        if drug in present:
            continue
        if _names_drug(cleaned, drug, negated=False):
            items.append(Grade("unsupported", "medication", f"The handoff names {drug}. This timeline does not."))
    if _positive(text, ("vasopressor",)) and not any(event.kind == "vasopressor" for event in events):
        items.append(Grade("unsupported", "vasopressor", "The handoff says a vasopressor was given. This timeline does not."))
    if _positive(text, ("inotrope",)) and not any(event.kind == "inotrope" for event in events):
        items.append(Grade("unsupported", "inotrope", "The handoff says an inotrope was given. This timeline does not."))
    if not any(event.kind == "hypotension" for event in events) and _positive(text, _HYPO_CONCEPT):
        items.append(Grade("unsupported", "hypotension", "The handoff says hypotension happened. This timeline does not."))
    if not any(event.kind == "desaturation" for event in events) and _claims_desat(text):
        items.append(Grade("unsupported", "desaturation", "The handoff says desaturation happened. This timeline does not."))
    return tuple(items)


def _positive(text: str, phrases: tuple[str, ...]) -> bool:
    return _phrase_at(text, phrases, negated=False)


def _negated(text: str, phrases: tuple[str, ...]) -> bool:
    return _phrase_at(text, phrases, negated=True)


def _phrase_at(text: str, phrases: tuple[str, ...], *, negated: bool) -> bool:
    for phrase in phrases:
        start = 0
        while True:
            found = text.find(phrase, start)
            if found < 0:
                break
            window = text[max(0, found - 40) : found]
            has_negation = _NEGATION.search(window) is not None
            if has_negation == negated:
                return True
            start = found + len(phrase)
    return False


def _names_drug(text: str, drug: str, *, negated: bool) -> bool:
    cleaned = text.replace(EXCLUDED_DRUG, " ")
    pattern = rf"(?<![a-z0-9]){re.escape(drug.casefold())}(?![a-z0-9])"
    for match in re.finditer(pattern, cleaned):
        window = cleaned[max(0, match.start() - 40) : match.start()]
        has_negation = _NEGATION.search(window) is not None
        if has_negation == negated:
            return True
    return False


def _claims_desat(text: str) -> bool:
    acknowledged = any(
        phrase in text
        for phrase in (
            "spo2 was not recorded",
            "sp02 was not recorded",
            "oxygen saturation was not recorded",
            "no spo2 reading",
        )
    )
    if acknowledged and not _positive(text, ("desatur", "low oxygen")) and _SPO2_NUMBER.search(text) is None:
        return False
    return _positive(text, _DESAT_CONCEPT)


def _spo2_value_claim(text: str) -> bool:
    if _SPO2_NUMBER.search(text):
        return True
    return _positive(text, ("normal saturation", "saturation was normal", "oxygenation was normal", "saturation was fine", "spo2 was normal"))


def _map_value_claim(text: str) -> bool:
    if _MAP_NUMBER.search(text):
        return True
    return _positive(text, ("blood pressure was normal", "map was normal", "pressure was stable"))


def _cell(label: str, value: float | None, unit: str) -> str:
    if value is None:
        return f"{label} missing"
    return f"{label} {_number(value)} {unit}"


def _number(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return str(value)
