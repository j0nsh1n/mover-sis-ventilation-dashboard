"""A number in a handoff is read whole: "86.0" is 86.0, never 0 or 5 or 0.5.

Expected grades are worked out from the practice cases' data and the grading contract, not from what the code
printed. FAKE-DESAT: SpO2 88 at minute 20 and 86 at 21, nothing else out of range. FAKE-CUFF: cuff MAP 60 at
minute 0 and 58 at minute 6 (one hypotension episode, minutes 0 to 6). FAKE-ART-LOW: arterial MAP 60 from minute 10
to 16, one Phenylephrine bolus at minute 12. FAKE-QUIET: MAP 80 and SpO2 98 throughout, nothing to report.
"""

from __future__ import annotations

import pytest

from src.modules.handoff_trainer import grading
from src.modules.handoff_trainer.cases import practice_cases
from src.modules.handoff_trainer.grading import grade

_CASES = {case.case_id: case for case in practice_cases()}


def _graded(case_id: str, text: str) -> list[tuple[str, str]]:
    return [(item.status, item.kind) for item in grade(_CASES[case_id], text)]


@pytest.mark.parametrize(
    ("case_id", "text", "expected"),
    [
        ("FAKE-DESAT", "Desaturation, SpO2 86.0 at minute 20.", [("mentioned", "desaturation")]),
        ("FAKE-CUFF", "Hypotension, MAP 58.0 at minute 3.", [("mentioned", "hypotension")]),
        (
            "FAKE-ART-LOW",
            "Hypotension, MAP 60.0 from minute 10 to 16. Phenylephrine 100 mcg at minute 12.",
            [("mentioned", "hypotension"), ("mentioned", "vasopressor")],
        ),
        ("FAKE-QUIET", "Stable. SpO2 97.5 throughout.", []),
    ],
)
def test_a_decimal_reading_grades_like_the_same_reading_without_a_point(case_id, text, expected):
    assert _graded(case_id, text) == expected


@pytest.mark.parametrize(
    ("case_id", "whole", "decimal"),
    [
        ("FAKE-DESAT", "Desaturation, SpO2 86 at minute 20.", "Desaturation, SpO2 86.0 at minute 20."),
        ("FAKE-CUFF", "Hypotension, MAP 58 at minute 3.", "Hypotension, MAP 58.0 at minute 3."),
        ("FAKE-QUIET", "Stable. SpO2 97 throughout.", "Stable. SpO2 97.5 throughout."),
    ],
)
def test_integers_still_read_as_before(case_id, whole, decimal):
    assert _graded(case_id, whole) == _graded(case_id, decimal)


def test_a_decimal_is_read_whole_by_the_value_reader():
    assert grading._numbers_after("spo2 86.0 at", ("spo2",)) == (86.0,)
    assert grading._numbers_after("map 58.5 mmhg", ("map",)) == (58.5,)
    assert grading._numbers_after("spo2 97.5", ("spo2",)) == (97.5,)
    assert grading._numbers_after("spo2 86 at", ("spo2",)) == (86.0,)


def test_a_decimal_at_the_end_of_a_sentence_is_read_whole_and_its_stop_ends_the_sentence():
    assert grading._numbers_after("spo2 86.0.", ("spo2",)) == (86.0,)
    assert grading._numbers_after("spo2 86.0. 97.5 later", ("spo2",)) == (86.0,)
    assert _graded("FAKE-DESAT", "Desaturation, SpO2 86.0.") == [("mentioned", "desaturation")]


def test_two_values_in_one_sentence_are_each_a_whole_number():
    assert _graded("FAKE-DESAT", "Desaturation, SpO2 88.0 then 86.0 at minute 20.") == [("mentioned", "desaturation")]
    assert _graded("FAKE-DESAT", "Desaturation, SpO2 88.0 then 70.5 at minute 20.") == [
        ("unsupported", "desaturation"),
        ("missed", "desaturation"),
    ]
    read = grading._numbers_after("spo2 88.0 then 86.0", ("spo2",))
    assert read and set(read) <= {88.0, 86.0}


def test_a_value_in_the_next_sentence_is_not_borrowed():
    assert grading._numbers_after("spo2 low. 86.0 at minute 20", ("spo2",)) == ()
    assert grading._numbers_after("spo2 low? 86.0 later", ("spo2",)) == ()
    assert grading._numbers_after("spo2 low\n86.0 later", ("spo2",)) == ()
    assert _graded("FAKE-QUIET", "SpO2 stable. 86.0 later") == []


def test_the_leftover_of_a_decimal_minute_is_not_a_value():
    body = grading._without_minute_claims("map minute 10.5")
    assert grading._numbers_after(body, ("map",)) == ()
    assert grading._minute_numbers("hypotension minute 10.5") == (10,)


def test_a_decimal_minute_and_the_end_of_its_range_are_taken_out_of_the_values():
    assert grading._minute_numbers("map 60 minute 10.5 to 16") == (10, 16)
    assert grading._minute_numbers("minutes 10-16.5") == (10, 16)
    body = grading._without_minute_claims("map 60 from minute 10.5 to 16.5 mmhg")
    assert grading._numbers_after(body, ("map",)) == (60.0,)
    assert "5" not in body and "16" not in body
    assert _graded("FAKE-ART-LOW", "Hypotension, MAP 60.0 from minute 10.5 to 16.") == [
        ("mentioned", "hypotension"),
        ("missed", "vasopressor"),
    ]
    assert _graded("FAKE-ART-LOW", "Hypotension, MAP 60.0 from minute 10.5 to 20.") == [
        ("unsupported", "hypotension"),
        ("missed", "hypotension"),
        ("missed", "vasopressor"),
    ]


# —— a value belongs to the series word before it (the owner's option A, 2026-11-11) ——


@pytest.mark.parametrize(
    ("text", "words", "read"),
    [
        ("spo2 97.5 throughout, map 75", ("spo2",), (97.5,)),  # stops at the next series word
        ("spo2 86 at minute 20", ("spo2",), (86.0,)),  # at a minute
        ("map 60, hr 110", ("map",), (60.0,)),  # at another measurement
        ("spo2 88 with rr 30", ("spo2",), (88.0,)),
        ("spo2 86 then phenylephrine 100 mcg", ("spo2",), (86.0,)),  # at a drug
        ("spo2 fell from 98 to 86", ("spo2",), (86.0,)),  # of the numbers before a stop, the last
        ("no spo2 - no spo2", ("spo2",), ()),  # the 2 of a later "SpO2" is never a value
    ],
)
def test_a_value_is_read_up_to_the_next_measurement_drug_dose_or_minute(text, words, read):
    assert grading._numbers_after(text, words) == read


@pytest.mark.parametrize(
    ("case_id", "text", "expected"),
    [
        # SpO2 stays at 98 and MAP at 80: a normal SpO2 followed by any MAP claims no desaturation
        ("FAKE-QUIET", "Stable. SpO2 97.5 throughout, MAP 75.", []),
        ("FAKE-QUIET", "Stable. SpO2 98, MAP 76.", []),
        # SpO2 86 at minute 21: the reading is the desaturation's own value, not the minute after it
        ("FAKE-DESAT", "SpO2 86 at minute 21.", [("mentioned", "desaturation")]),
        ("FAKE-DESAT", "SpO2 88 with RR 30.", [("mentioned", "desaturation")]),
        # arterial MAP 60 from minute 10 to 16, Phenylephrine 100 mcg at minute 12: a heart rate is not a MAP
        (
            "FAKE-ART-LOW",
            "Hypotension, MAP 60, HR 110. Phenylephrine 100 mcg at minute 12.",
            [("mentioned", "hypotension"), ("mentioned", "vasopressor")],
        ),
    ],
)
def test_a_reading_after_its_value_is_not_taken_for_that_value(case_id, text, expected):
    assert _graded(case_id, text) == expected


def test_where_a_minute_claim_was_taken_out_the_reach_to_a_value_still_stops():
    """A detail is graded with its minute claims taken out; the value must read the same as with them in."""
    text = "spo2 86 at minute 21, 2 readings"
    assert grading._numbers_after(text, ("spo2",)) == (86.0,)
    assert grading._numbers_after(grading._without_minute_claims(text), ("spo2",)) == (86.0,)
