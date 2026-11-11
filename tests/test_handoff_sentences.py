"""A negation, owner, value, minute or drug is read inside its own sentence and never reaches into the next.

Every expected grade here is worked out from the contract: a finding is mentioned when it is named once with a
supported detail, a denial in its own sentence makes it unsupported and missed, and a minute or value counts
only for the finding it is written with.
"""

from __future__ import annotations

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


def test_every_look_back_in_the_grader_is_built_from_the_one_sentence_end():
    for pattern in (grading._SPO2_NUMBER, grading._MAP_NUMBER, grading._SATURATION_UNDER_NINETY):
        assert grading._IN_SENTENCE in pattern.pattern
    # A hand-written class such as "[^\\n.]" or a split on "[.\\n;]" would be a second, older definition.
    source = Path(grading.__file__).read_text(encoding="utf-8")
    assert "[^\\n.]" not in source and "[.\\n;]" not in source
