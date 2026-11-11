"""A negation, owner, value, minute or drug is read inside its own sentence and never reaches into the next.

Every expected grade here is worked out from the contract: a finding is mentioned when it is named once with a
supported detail, a denial in its own sentence makes it unsupported and missed, and a minute or value counts
only for the finding it is written with.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.modules.handoff_trainer import grading
from src.modules.handoff_trainer.grading import Dose, PracticeCase, Sample, grade


def _case() -> PracticeCase:
    """Hypotension from minute 10 to 16 (arterial MAP 60), one Phenylephrine bolus at minute 12, SpO2 98."""
    samples = tuple(Sample(m, map_art=60 if 10 <= m <= 16 else 80, spo2=98) for m in range(0, 25))
    return PracticeCase("FAKE-S", "Fictional sentences", samples, (Dose("Phenylephrine", 12, amount=100, unit="mcg"),))


def _graded(text: str, case: PracticeCase | None = None) -> list[tuple[str, str]]:
    return [(item.status, item.kind) for item in grade(case or _case(), text)]


MENTIONED_HYPOTENSION = [("mentioned", "hypotension"), ("missed", "vasopressor")]
DENIED_HYPOTENSION = [("unsupported", "hypotension"), ("missed", "hypotension"), ("missed", "vasopressor")]


def test_the_sentence_end_is_one_rule_for_stops_marks_line_breaks_and_semicolons_but_not_decimals():
    pieces = grading._SENTENCE_END.split("no distress. map 0.5? yes! a\nb; c v2.1 d")
    assert pieces == ["no distress", " map 0.5", " yes", " a", "b", " c v2.1 d"]
    assert grading._SENTENCE_END.split("minute 6.") == ["minute 6", ""]


@pytest.mark.parametrize(
    "text",
    [
        "Stable, no distress. Hypotension from minute 10 to 16",
        "Not sedated? Hypotension from minute 10 to 16",
        "Without pain! Hypotension from minute 10 to 16",
        "Stable, no distress\nHypotension from minute 10 to 16",
        "Never intubated; hypotension from minute 10 to 16",
        "Stable, no distress.\n\nHypotension from minute 10 to 16.",
    ],
)
def test_a_negation_in_an_earlier_sentence_does_not_deny_a_later_finding(text):
    assert _graded(text) == MENTIONED_HYPOTENSION


@pytest.mark.parametrize(
    "text",
    [
        "Stable, no distress and no hypotension from minute 10 to 16",
        "Without hypotension, minutes 10 to 16 were stable",
        "Never any hypotension from minute 10 to 16",
        "Not sedated, no hypotension, minute 12",
    ],
)
def test_a_negation_in_the_same_sentence_still_denies_the_finding(text):
    assert _graded(text) == DENIED_HYPOTENSION


def test_a_full_stop_inside_a_number_does_not_end_the_negated_sentence():
    # "0.5" is one number, so "not" still reaches "hypotension" 22 characters later.
    assert _graded("Not on 0.5 mg infusion, hypotension from minute 10 to 16") == DENIED_HYPOTENSION
    # A real full stop after the number does end it.
    assert _graded("Not on 0.5 mg. Hypotension from minute 10 to 16") == MENTIONED_HYPOTENSION
    assert _graded("Not on 0.5. Hypotension from minute 10 to 16") == MENTIONED_HYPOTENSION


def test_several_negations_each_deny_only_their_own_sentence():
    text = "No distress. Not sedated. Hypotension from minute 10 to 16. No phenylephrine."
    assert _graded(text) == [("mentioned", "hypotension"), ("unsupported", "vasopressor"), ("missed", "vasopressor")]
    text = "No hypotension. Phenylephrine 100 mcg at minute 12."
    assert _graded(text) == [
        ("unsupported", "hypotension"), ("missed", "hypotension"), ("mentioned", "vasopressor")]
    # The same words in one sentence deny both.
    both = _graded("No hypotension, phenylephrine 100 mcg at minute 12.")
    assert ("unsupported", "vasopressor") in both and ("mentioned", "vasopressor") not in both


@pytest.mark.parametrize("separator", [". ", "? ", "! ", "\n", "; "])
def test_a_minute_in_the_next_sentence_is_not_a_detail_of_the_finding_before_it(separator):
    assert _graded(f"Hypotension{separator}minute 10 to 16") == [("missed", "hypotension"), ("missed", "vasopressor")]
    # In one sentence the same words are a mention, so the probe can tell the two apart.
    assert _graded("Hypotension minute 10 to 16") == MENTIONED_HYPOTENSION


def test_a_value_after_a_question_mark_is_not_a_value_for_the_missing_series():
    no_spo2 = PracticeCase("FAKE-N", "n", tuple(Sample(m, map_art=82) for m in range(9)), ())
    assert _graded("SpO2 missing? 98", no_spo2) == [("mentioned", "missing_spo2")]
    assert _graded("SpO2 missing 98", no_spo2) == [("unsupported", "missing_spo2")]
    assert _graded("SpO2 98", no_spo2) == [("unsupported", "missing_spo2")]


def test_a_value_for_saturation_in_the_next_sentence_is_not_a_desaturation_claim():
    quiet = PracticeCase("FAKE-Q", "q", tuple(Sample(m, map_art=80, spo2=98) for m in range(13)), ())
    assert _graded("SpO2 fine? 86 at the start", quiet) == []
    assert _graded("SpO2 fine. 86 at the start", quiet) == []
    assert _graded("SpO2 86 at the start", quiet) == [("unsupported", "desaturation")]


def test_the_reach_from_a_series_word_to_its_value_stops_at_every_full_stop_and_sentence_end():
    for pattern in (grading._SPO2_NUMBER, grading._MAP_NUMBER, grading._SATURATION_UNDER_NINETY):
        assert grading._TO_VALUE in pattern.pattern
    for mark in ".?!;\n":
        assert re.fullmatch(grading._TO_VALUE, mark) is None, mark
    assert re.fullmatch(grading._TO_VALUE, ",") and re.fullmatch(grading._TO_VALUE, " ")
    # A hand-written class such as "[^\\n.]" or a split on "[.\\n;]" would be a second, older definition.
    source = Path(grading.__file__).read_text(encoding="utf-8")
    assert "[^\\n.]" not in source and "[.\\n;]" not in source


def test_a_decimal_value_is_read_for_its_own_series_and_not_replaced_by_a_later_number():
    """The reach to a value cannot pass the value's own decimal point, so a number further on is not taken for it."""
    spo2 = ("spo2", "sp02", "saturation")
    assert grading._numbers_after("spo2 97.5 throughout, map 75", spo2) == (97.5,)
    assert grading._numbers_after("spo2 86.0 at minute 20", spo2) == (86.0,)
    assert grading._numbers_after("map 60.0 with dose 0.5 mg", ("map",)) == (60.0,)
    # SpO2 stays at 98 and MAP at 80: a normal saturation with a normal MAP after it claims nothing
    quiet = PracticeCase("FAKE-Q", "q", tuple(Sample(m, map_art=80, spo2=98) for m in range(13)), ())
    assert _graded("Stable. SpO2 97.5 throughout, MAP 75.", quiet) == []
    assert _graded("Stable. SpO2 97.5, MAP 72.5.", quiet) == []
    # SpO2 is 86 at minutes 20 and 21 only: the reading is the desaturation's own value, the minute its own minute
    desat = PracticeCase("FAKE-D", "d", tuple(Sample(m, map_art=80, spo2=86 if m in (20, 21) else 98) for m in range(25)), ())
    assert _graded("SpO2 86.0 at minute 20.", desat) == [("mentioned", "desaturation")]
    # a dose written with a decimal after a MAP reading is not read as a second MAP value
    assert ("unsupported", "hypotension") not in _graded("Hypotension, MAP 60.0 from minute 10 to 16, dose 0.5 mg phenylephrine")
