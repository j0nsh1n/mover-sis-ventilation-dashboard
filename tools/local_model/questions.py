"""Question templates for the training prompts, grouped into families.

A family belongs to one side, ``train`` or ``heldout``, and the two sides use different
wording, not only different seeds. Templates are filled from the facts of the seed's own
synthetic cases (procedure, age, sex, flags), so a seed always gives the same
questions. The app reads each question's kind with ``src.llm.intent.classify``; the
tests check that every family's questions come out as the kind it declares.
"""

from __future__ import annotations

import random
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

_FLAG_WORDS = {
    "pip_high": "high PIP",
    "pip_rising": "rising PIP",
    "spo2_low": "low SpO2",
    "etco2_high": "high ETCO2",
    "etco2_zero_vent": "zero ETCO2 while ventilated",
    "map_low": "low MAP",
    "agent_high": "high agent concentration",
    "agent_drift": "agent drift",
    "rr_low": "low respiratory rate",
}


@dataclass(frozen=True)
class CaseFacts:
    pid: str
    procedure: str
    age: int
    sex: str  # "F" or "M"
    flag: str  # the case's leading flag, in words; "" when it has none


@dataclass(frozen=True)
class Family:
    name: str
    side: str  # "train" or "heldout"
    kind: str  # question, scenario or compare
    templates: tuple[str, ...]
    cases: int = 1  # how many distinct cases the template names
    distinct_procedures: bool = False
    needs_flag: bool = False
    # Number of earlier turns shown before the question (a follow-up family).
    earlier: int = 0
    # The signal asked about is not in any extract.
    absent: bool = False


def case_facts(row: Mapping[str, Any]) -> CaseFacts:
    rules = str(row.get("top_rules") or "")
    first = re.match(r"\s*([a-z0-9_]+)", rules)
    rule = first.group(1) if first else ""
    return CaseFacts(
        pid=str(row["PID"]),
        procedure=str(row["Procedure_short"]),
        age=int(float(row["Age"])),
        sex=str(row["Gender"]),
        flag=_FLAG_WORDS.get(rule, rule.replace("_", " ")),
    )


_TRAIN = "train"
_HELD = "heldout"

FAMILIES: tuple[Family, ...] = (
    # Plain questions about a procedure's cases
    Family("train-peak-pressure", _TRAIN, "question", (
        "What was the peak inspiratory pressure in {procedure} cases?",
        "How high did PIP go in the {procedure} cases?",
    )),
    Family("train-flags-by-procedure", _TRAIN, "question", (
        "Which {procedure} cases had flags raised, and which rules fired?",
        "What flags were raised in {procedure} surgeries?",
    )),
    Family("train-vent-summary", _TRAIN, "question", (
        "Summarize the ventilator settings used in {procedure} cases.",
        "What ventilator settings did the {procedure} cases have?",
    )),
    Family("train-peep-volume", _TRAIN, "question", (
        "What PEEP and tidal volume did the {procedure} cases have?",
        "Describe PEEP and tidal volume for the {procedure} cases.",
    )),
    Family("train-flag-specific", _TRAIN, "question", (
        "Which cases had a {flag} flag?",
        "Show me the cases where {flag} was flagged.",
    ), needs_flag=True),
    Family("heldout-heart-rate", _HELD, "question", (
        "What heart rates were seen in {procedure} cases?",
        "Give the heart rate range for the {procedure} patients.",
    )),
    Family("heldout-etco2", _HELD, "question", (
        "How did end-tidal CO2 look in {procedure} cases?",
        "Was ETCO2 steady during the {procedure} surgeries?",
    )),
    Family("heldout-flag-list", _HELD, "question", (
        "List every flag raised for {procedure} patients.",
        "Did any {procedure} case trigger an alert rule?",
    )),
    Family("heldout-duration-agent", _HELD, "question", (
        "How long did the {procedure} cases run, and which agent was used?",
        "State the case length and anesthetic agent for {procedure}.",
    )),
    # Questions about a patient who is described, not named
    Family("train-scenario-age-sex", _TRAIN, "scenario", (
        "{age}-year-old {woman} having a {procedure}",
        "Patient: {age} year old {woman}, {procedure}. What do similar cases show?",
    )),
    Family("train-scenario-short-age", _TRAIN, "scenario", (
        "Similar cases to a {age} y/o {woman} with {procedure}",
        "{woman} {age} y/o, {procedure}: how were matching cases ventilated?",
    )),
    Family("heldout-scenario-aged", _HELD, "scenario", (
        "A {woman} aged {age} scheduled for {procedure}: what did comparable cases show?",
        "What happened in {procedure} for a {woman} aged {age}?",
    )),
    Family("heldout-scenario-letter", _HELD, "scenario", (
        "{sexletter} {age}, {procedure}",
        "{sexletter} {age} having a {procedure}. Anything unusual in similar cases?",
    )),
    Family("heldout-scenario-decade", _HELD, "scenario", (
        "{procedure} in {decade}: how were they ventilated?",
        "Cases of {procedure} in {decade}",
    )),
    # Comparisons
    Family("train-compare-pids", _TRAIN, "compare", (
        "Compare {pid1} and {pid2} on PIP and PEEP.",
        "{pid1} versus {pid2}: how do their ventilator signals differ?",
    ), cases=2),
    Family("train-compare-procedures", _TRAIN, "compare", (
        "Compare {procedure} cases with {procedure2} cases on PIP.",
        "Compare ventilation in {procedure} and {procedure2}.",
    ), cases=2, distinct_procedures=True),
    Family("heldout-compare-pids", _HELD, "compare", (
        "How do {pid1} and {pid2} differ in tidal volume?",
        "Contrast {pid1}, {pid2} and {pid3} on their flags.",
    ), cases=3),
    Family("heldout-compare-procedures", _HELD, "compare", (
        "{procedure} versus {procedure2}: how do the ETCO2 levels compare?",
        "Is heart rate different between {procedure} and {procedure2}? Compare them.",
    ), cases=2, distinct_procedures=True),
    # Follow-ups: one or two earlier turns come first
    Family("train-followup-peep", _TRAIN, "question", (
        "What was the PEEP in those cases?",
        "And their tidal volumes?",
    ), earlier=1),
    Family("train-followup-flags", _TRAIN, "question", (
        "Were any flags raised in them?",
        "Which flags did the first case have?",
    ), earlier=2),
    Family("train-followup-compare", _TRAIN, "compare", (
        "Now compare them on PIP.",
    ), earlier=1),
    Family("heldout-followup-etco2", _HELD, "question", (
        "Same cases: how did ETCO2 behave?",
        "What about oxygen saturation there?",
    ), earlier=1),
    Family("heldout-followup-oldest", _HELD, "question", (
        "Did the oldest of them have anything unusual?",
        "Which of these ran the longest?",
    ), earlier=2),
    Family("heldout-followup-compare", _HELD, "compare", (
        "How do they differ in PEEP? Compare them.",
    ), earlier=1),
    # Asking for a signal the extract does not have
    Family("train-absent-bis", _TRAIN, "question", (
        "What was the BIS value in the {procedure} cases?",
        "How much blood loss was recorded for {procedure} surgeries?",
    ), absent=True),
    Family("train-absent-followup", _TRAIN, "question", (
        "What was the core temperature in those cases?",
    ), earlier=1, absent=True),
    Family("heldout-absent-temperature", _HELD, "question", (
        "Did the {procedure} cases record core temperature?",
        "What urine output did the {procedure} patients have?",
    ), absent=True),
    Family("heldout-absent-followup", _HELD, "question", (
        "What blood glucose values did they have?",
    ), earlier=1, absent=True),
)

# Earlier turns come from plain question families of the same side.
_BASE_FIRST = {
    side: tuple(
        f for f in FAMILIES
        if f.side == side and f.kind == "question" and f.earlier == 0 and not f.absent and not f.needs_flag
    )
    for side in (_TRAIN, _HELD)
}
_SECOND_TURN = {
    _TRAIN: "What was the PEEP in those cases?",
    _HELD: "Same cases: how did ETCO2 behave?",
}


def families(side: str) -> list[Family]:
    return [f for f in FAMILIES if f.side == side]


def pick_cases(family: Family, facts: Sequence[CaseFacts], rng: random.Random) -> list[CaseFacts] | None:
    """The cases a family's question names, or None when this corpus has no fit."""
    pool = [c for c in facts if c.flag or not family.needs_flag]
    if len(pool) < family.cases:
        return None
    first = rng.choice(pool)
    chosen = [first]
    rest = [c for c in pool if c.pid != first.pid]
    while len(chosen) < family.cases:
        options = [
            c for c in rest
            if not family.distinct_procedures or c.procedure not in {x.procedure for x in chosen}
        ]
        if not options:
            return None
        pick = rng.choice(options)
        chosen.append(pick)
        rest = [c for c in rest if c.pid != pick.pid]
    return chosen


def fill(family: Family, cases: Sequence[CaseFacts], rng: random.Random) -> str:
    template = rng.choice(family.templates)
    first = cases[0]
    values = {
        "procedure": first.procedure.lower(),
        "age": str(first.age),
        "woman": "woman" if first.sex == "F" else "man",
        "sexletter": first.sex,
        "decade": f"{'her' if first.sex == 'F' else 'his'} {first.age // 10}0s",
        "flag": first.flag,
    }
    for i, case in enumerate(cases, start=1):
        values[f"pid{i}"] = case.pid
        if i > 1:
            values[f"procedure{i}" if i > 2 else "procedure2"] = case.procedure.lower()
    return template.format(**values)


def earlier_questions(
    family: Family, cases: Sequence[CaseFacts], rng: random.Random
) -> list[str]:
    """The questions asked before a follow-up, oldest first, about the follow-up's first case."""
    if not family.earlier:
        return []
    base = rng.choice(_BASE_FIRST[family.side])
    asked = [fill(base, cases, rng)]
    if family.earlier > 1:
        asked.append(_SECOND_TURN[family.side])
    return asked
