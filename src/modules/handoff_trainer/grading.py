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
_DESAT_CONCEPT = ("desatur", "low oxygen")
_NEGATION = re.compile(r"\b(no|not|without|denies|denied|never)\b")
_SPO2_NUMBER = re.compile(r"\b(spo2|sp02|saturation)\b[^\n.]{0,30}\d")
_MAP_NUMBER = re.compile(r"\b(map|blood pressure)\b[^\n.]{0,30}\d")
_MINUTE_CLAIM = re.compile(
    r"\b(?:minutes|minute|mins|min)\s+(\d+)(?:\s*(?:to|through|thru|and|-|–)\s*(\d+))?"
)
_SPO2_SERIES = ("spo2", "sp02", "oxygen saturation", "saturation")
_MAP_SERIES = ("blood pressure", "map")
# A detail belongs to the finding named just before it in the same sentence,
# or to the sentence's first finding when it comes before any of them.
_OWNERS: tuple[tuple[str, str], ...] = (
    ("hypotension", r"hypotensi\w*|low map|low blood pressure|blood pressure|mean arterial pressure|map|bp"),
    ("desaturation", r"desat\w*|low oxygen|oxygen saturation|saturation|o2 sats?|sats?|spo2|sp02"),
) + tuple((drug, re.escape(drug.casefold())) for drug in TRACKED_DRUGS)
_OWNER_PATTERN = re.compile(
    "|".join(rf"(?P<o{index}>(?<![a-z0-9])(?:{pattern})(?![a-z0-9]))" for index, (_, pattern) in enumerate(_OWNERS))
)
_SENTENCE_BREAK = re.compile(r"[;\n]|\.(?!\d)")
_GAP_WORDS = re.compile(
    r"\b(?:missing|unavailable|absent|unrecorded|not recorded|not measured|no reading|none recorded|not available)\b"
)


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
    start_minute: int | None = None
    end_minute: int | None = None
    values: tuple[float, ...] = ()


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
    owned = _details_by_finding(text)
    items: list[Grade] = []
    for event in events:
        if event.kind in {"hypotension", "desaturation"}:
            peers = tuple(other for other in events if other.kind == event.kind)
            items.extend(_grade_episode(event, text, owned.get(event.kind, ""), peers))
        elif event.kind in {"vasopressor", "inotrope"} and event.drug is not None:
            doses = tuple(other for other in events if other.drug == event.drug)
            items.extend(_grade_drug(event, text, owned.get(event.drug, ""), doses))
        elif event.kind == "missing_map":
            items.append(_grade_gap(event, text, _MAP_SERIES, _map_value_claim))
        elif event.kind == "missing_spo2":
            items.append(_grade_gap(event, text, _SPO2_SERIES, _spo2_value_claim))
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
        return tuple(_span_event("hypotension", "Arterial MAP stayed under 65 mmHg", run) for run in runs)
    cuff = _usable(samples, lambda sample: sample.nmap, MAP_RANGE)
    if cuff:
        runs = _low_runs(cuff, MAP_LOW, CUFF_MAX_GAP, HYPO_SPAN_MINUTES, minimum_count=2)
        return tuple(_span_event("hypotension", "Noninvasive MAP stayed under 65 mmHg", run) for run in runs)
    return (TimelineEvent("missing_map", "No usable MAP reading is on this timeline."),)


def _saturation_events(samples: tuple[Sample, ...]) -> tuple[TimelineEvent, ...]:
    usable = _usable(samples, lambda sample: sample.spo2, SPO2_RANGE)
    if not usable:
        return (TimelineEvent("missing_spo2", "No usable SpO2 reading is on this timeline."),)
    runs = _low_runs(usable, SPO2_LOW, SPO2_MAX_GAP, 0, minimum_count=2)
    return tuple(_span_event("desaturation", "SpO2 stayed under 90%", run) for run in runs)


def _span_event(kind: str, prefix: str, run: list[tuple[int, float]]) -> TimelineEvent:
    start = run[0][0]
    end = run[-1][0]
    return TimelineEvent(
        kind,
        f"{prefix} from minute {start} to minute {end}.",
        start_minute=start,
        end_minute=end,
        values=tuple(value for _, value in run),
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
            end = dose.end_minute
        else:
            when = f"at minute {dose.minute}"
            end = dose.minute
        events.append(
            TimelineEvent(
                kind,
                f"{canonical} {amount} {when}.",
                drug=canonical,
                dose_kind=dose_kind,
                start_minute=dose.minute,
                end_minute=end,
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
    event: TimelineEvent, text: str, details: str, peers: tuple[TimelineEvent, ...]
) -> tuple[Grade, ...]:
    if event.kind == "hypotension":
        named = _names_hypotension(text)
        denied = _negated(text, _HYPO_CONCEPT)
        supported, contradicted = _hypotension_detail(details, event, peers)
    else:
        named = _names_desaturation(text, event)
        denied = _negated(text, _DESAT_CONCEPT)
        supported, contradicted = _desaturation_detail(details, event, peers)
    if named and not denied and supported and not contradicted:
        return (Grade("mentioned", event.kind, event.evidence),)
    if contradicted:
        return (
            Grade(
                "unsupported",
                event.kind,
                f"The handoff gives a detail this timeline does not show. {event.evidence}",
            ),
            Grade("missed", event.kind, event.evidence),
        )
    if denied:
        return (
            Grade("unsupported", event.kind, f"The handoff says this was absent. {event.evidence}"),
            Grade("missed", event.kind, event.evidence),
        )
    return (Grade("missed", event.kind, event.evidence),)


def _grade_drug(
    event: TimelineEvent, text: str, details: str, doses: tuple[TimelineEvent, ...]
) -> tuple[Grade, ...]:
    drug = event.drug or ""
    if _names_drug(text, drug, negated=True) and not _names_drug(text, drug, negated=False):
        return (
            Grade("unsupported", event.kind, f"The handoff says {drug} was absent. {event.evidence}"),
            Grade("missed", event.kind, event.evidence),
        )
    if any(not _inside_any(minute, doses) for minute in _minute_numbers(details)):
        return (
            Grade("unsupported", event.kind, f"The handoff gives {drug} a minute this timeline does not show. {event.evidence}"),
            Grade("missed", event.kind, event.evidence),
        )
    if _names_drug(text, drug, negated=False):
        return (Grade("mentioned", event.kind, event.evidence),)
    return (Grade("missed", event.kind, event.evidence),)


def _grade_gap(event: TimelineEvent, text: str, series: tuple[str, ...], value_claim) -> Grade:
    if value_claim(text):
        return Grade("unsupported", event.kind, f"The handoff states a value. {event.evidence}")
    if _acknowledges_gap(text, series):
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
    low_values = [value for value in _numbers_after(text, ("spo2", "sp02", "saturation")) if value < SPO2_LOW]
    if _acknowledges_gap(text, _SPO2_SERIES) and not _positive(text, _DESAT_CONCEPT) and not low_values:
        return False
    if _positive(text, _DESAT_CONCEPT):
        return True
    if re.search(r"\b(?:spo2|sp02|oxygen saturation|saturation)\b[^\n.]{0,20}(?:under|below|<)\s*90\b", text):
        return True
    return bool(low_values)


def _names_hypotension(text: str) -> bool:
    return _positive(text, _HYPO_CONCEPT)


def _names_desaturation(text: str, event: TimelineEvent) -> bool:
    if _positive(text, _DESAT_CONCEPT):
        return True
    if re.search(r"\b(?:spo2|sp02|oxygen saturation|saturation)\b[^\n.]{0,20}(?:under|below|<)\s*90\b", text):
        return True
    return any(
        value < SPO2_LOW and any(abs(value - known) < 0.01 for known in event.values)
        for value in _numbers_after(text, ("spo2", "sp02", "saturation"))
    )


def _hypotension_detail(
    text: str, event: TimelineEvent, peers: tuple[TimelineEvent, ...]
) -> tuple[bool, bool]:
    minutes_ok, minutes_bad = _minute_fit(text, event, peers)
    body = _without_minute_claims(text)
    matched, contradicted = _stated_values(body, ("map", "blood pressure"), event, peers, MAP_LOW)
    threshold = _has_standalone(body, 65)
    return minutes_ok or threshold or matched, minutes_bad or contradicted


def _desaturation_detail(
    text: str, event: TimelineEvent, peers: tuple[TimelineEvent, ...]
) -> tuple[bool, bool]:
    minutes_ok, minutes_bad = _minute_fit(text, event, peers)
    body = _without_minute_claims(text)
    under_ninety = re.search(r"(?:under|below|<)\s*90\b", body) is not None
    stated = [
        value
        for value in _numbers_after(body, ("spo2", "sp02", "saturation"))
        if not (under_ninety and abs(value - 90.0) < 0.01)
    ]
    matched = any(_shows_value(value, (event,)) for value in stated)
    contradicted = any(not _shows_value(value, peers) for value in stated)
    threshold = under_ninety or (_has_standalone(body, 90) and not stated)
    return minutes_ok or threshold or matched, minutes_bad or contradicted


def _stated_values(
    text: str,
    words: tuple[str, ...],
    event: TimelineEvent,
    peers: tuple[TimelineEvent, ...],
    threshold: float | None,
) -> tuple[bool, bool]:
    matched = False
    contradicted = False
    for value in _numbers_after(text, words):
        if threshold is not None and abs(value - threshold) < 0.01:
            matched = True
        elif _shows_value(value, (event,)):
            matched = True
        elif not _shows_value(value, peers):
            contradicted = True
    return matched, contradicted


def _shows_value(value: float, events: tuple[TimelineEvent, ...]) -> bool:
    return any(abs(value - known) < 0.01 for event in events for known in event.values)


def _minute_fit(text: str, event: TimelineEvent, peers: tuple[TimelineEvent, ...]) -> tuple[bool, bool]:
    """A minute inside another episode of the same kind belongs to that episode."""
    claimed = _minute_numbers(text)
    if not claimed or event.start_minute is None or event.end_minute is None:
        return False, False
    outside = [number for number in claimed if not _inside_any(number, peers)]
    inside = [number for number in claimed if _inside_any(number, (event,))]
    return bool(inside) and not outside, bool(outside)


def _inside_any(minute: int, events: tuple[TimelineEvent, ...]) -> bool:
    return any(
        event.start_minute is not None and event.end_minute is not None and event.start_minute <= minute <= event.end_minute
        for event in events
    )


def _details_by_finding(text: str) -> dict[str, str]:
    owned: dict[str, list[str]] = {}
    for sentence in _SENTENCE_BREAK.split(text.replace(EXCLUDED_DRUG, " ")):
        marks = [(match.start(), _OWNERS[int(str(match.lastgroup)[1:])][0]) for match in _OWNER_PATTERN.finditer(sentence)]
        for index, (start, owner) in enumerate(marks):
            begin = 0 if index == 0 else start
            end = marks[index + 1][0] if index + 1 < len(marks) else len(sentence)
            owned.setdefault(owner, []).append(sentence[begin:end])
    return {owner: ". ".join(parts) for owner, parts in owned.items()}


def _minute_numbers(text: str) -> tuple[int, ...]:
    found: list[int] = []
    for match in _MINUTE_CLAIM.finditer(text):
        found.append(int(match.group(1)))
        if match.group(2):
            found.append(int(match.group(2)))
    return tuple(found)


def _without_minute_claims(text: str) -> str:
    return _MINUTE_CLAIM.sub(" ", text)


def _has_standalone(text: str, number: int) -> bool:
    return re.search(rf"(?<!\d){number}(?!\d)", text) is not None


def _numbers_after(text: str, words: tuple[str, ...]) -> tuple[float, ...]:
    pattern = (
        r"\b(?:"
        + "|".join(re.escape(word) for word in words)
        + r")\b[^\n.]{0,24}(?<!\d)(\d+(?:\.\d+)?)(?!\d)"
    )
    return tuple(float(match.group(1)) for match in re.finditer(pattern, text))


def _acknowledges_gap(text: str, series: tuple[str, ...]) -> bool:
    for sentence in re.split(r"[.\n;]", text):
        if not any(re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", sentence) for word in series):
            continue
        if _GAP_WORDS.search(sentence):
            return True
        if any(re.search(rf"\bno\s+{re.escape(word)}\b", sentence) for word in series):
            return True
    return False


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
