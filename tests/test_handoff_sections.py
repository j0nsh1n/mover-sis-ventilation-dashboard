"""The five handoff sections and the one text the grader receives: the grading contract still holds through the join."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from src.modules.handoff_trainer import grading
from src.modules.handoff_trainer.cases import practice_cases
from src.modules.handoff_trainer.grading import Dose, PracticeCase, Sample, grade
from src.modules.handoff_trainer.history import attempt_counts, found_counts, load_attempts, record_attempt
from src.modules.handoff_trainer.sections import SECTIONS, join_sections

KEYS = [key for key, _ in SECTIONS]
HEADINGS = [heading for _, heading in SECTIONS]


def _kitchen_sink() -> PracticeCase:
    """Hypotension minutes 10-16 (arterial), desaturation minutes 30-31, a bolus at 12 and an infusion 40-50."""
    samples = tuple(
        Sample(
            minute,
            map_art=60 if 10 <= minute <= 16 else 80,
            spo2=86 if minute in (30, 31) else 98,
        )
        for minute in range(0, 52)
    )
    doses = (
        Dose("Phenylephrine", 12, amount=100, unit="mcg"),
        Dose("Norepinephrine", 40, end_minute=50, amount=5, unit="mcg"),
    )
    return PracticeCase("FAKE-SINK", "Fictional kitchen sink", samples, doses)


def _hypotension_case() -> PracticeCase:
    samples = tuple(Sample(m, map_art=60 if 10 <= m <= 16 else 80, spo2=98) for m in range(0, 25))
    return PracticeCase("FAKE-H", "Fictional hypotension", samples, (Dose("Phenylephrine", 12, amount=100, unit="mcg"),))


def _result(case: PracticeCase, text: str) -> list[tuple[str, str]]:
    return [(item.status, item.kind) for item in grade(case, text)]


def test_the_sections_are_these_five_in_this_order():
    assert HEADINGS == [
        "Illness severity",
        "Patient summary",
        "Action list",
        "Situation awareness and contingency plans",
        "Synthesis by receiver",
    ]


def test_worked_example_joins_in_order_under_headings_and_leaves_blank_sections_out():
    joined = join_sections(
        {
            "synthesis": "Receiver repeats the plan",
            "action_list": "  Phenylephrine 100 mcg at minute 12.  ",
            "patient_summary": "Hypotension from minute 10 to 16",
            "illness_severity": "   ",
            "situation_awareness": "",
        }
    )
    rule = "-" * 44
    assert joined == (
        "Patient summary: Hypotension from minute 10 to 16."
        f"\n{rule}\n"
        "Action list: Phenylephrine 100 mcg at minute 12."
        f"\n{rule}\n"
        "Synthesis by receiver: Receiver repeats the plan."
    )


def test_all_blank_sections_join_to_nothing():
    assert join_sections({}) == ""
    assert join_sections({key: " \n\t " for key in KEYS}) == ""


def test_a_section_that_already_ends_in_a_full_stop_gets_no_second_one():
    assert join_sections({"action_list": "Done."}) == "Action list: Done."


@pytest.mark.parametrize("case", [*practice_cases(), _kitchen_sink()], ids=lambda case: case.case_id)
def test_the_headings_alone_add_no_finding_and_no_detail(case):
    body = "Nothing to add."
    headed = join_sections({key: body for key in KEYS})
    assert all(heading in headed for heading in HEADINGS)
    assert grade(case, headed) == grade(case, body)


def test_no_heading_word_is_in_the_graders_vocabulary():
    """Checked against the grader's own patterns, so a vocabulary change there fails here."""
    concept_phrases = (*grading._HYPO_CONCEPT, *grading._DESAT_CONCEPT)
    series_words = (*grading._SPO2_SERIES, *grading._MAP_SERIES)
    for heading in HEADINGS:
        folded = " ".join(heading.casefold().split())
        assert grading._OWNER_PATTERN.search(folded) is None, heading
        assert grading._MINUTE_CLAIM.search(folded) is None, heading
        assert grading._GAP_WORDS.search(folded) is None, heading
        assert grading._NEGATION.search(folded) is None, heading
        assert not any(phrase in folded for phrase in concept_phrases), heading
        assert not any(re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", folded) for word in series_words), heading
        assert not any(drug.casefold() in folded for drug in grading.TRACKED_DRUGS), heading
        assert not re.search(r"\d", folded), heading


@pytest.mark.parametrize("ending", ["", "?", "!", ":", ",", " 6", "-"])
def test_a_minute_in_one_section_never_counts_for_a_finding_in_another(ending):
    case = _hypotension_case()
    split = join_sections({"patient_summary": "Hypotension" + ending, "action_list": "minute 12 to 14"})
    assert ("mentioned", "hypotension") not in _result(case, split)
    assert ("missed", "hypotension") in _result(case, split)
    # The same words in one section are a mention, so the probe can tell the two apart.
    together = join_sections({"patient_summary": "Hypotension from minute 12 to 14"})
    assert ("mentioned", "hypotension") in _result(case, together)


def test_a_value_in_one_section_never_counts_for_a_finding_in_another():
    case = _hypotension_case()
    split = join_sections({"patient_summary": "Hypotension, MAP", "action_list": "60 mmHg"})
    assert ("mentioned", "hypotension") not in _result(case, split)
    together = join_sections({"patient_summary": "Hypotension, MAP 60 mmHg"})
    assert ("mentioned", "hypotension") in _result(case, together)


def test_a_wrong_minute_in_one_section_is_not_charged_to_a_drug_named_in_another():
    case = _hypotension_case()
    split = _result(case, join_sections({"patient_summary": "Phenylephrine", "action_list": "bolus at minute 99"}))
    assert ("unsupported", "vasopressor") not in split
    assert ("mentioned", "vasopressor") in split
    together = _result(case, join_sections({"patient_summary": "Phenylephrine bolus at minute 99"}))
    assert ("unsupported", "vasopressor") in together


def test_a_negation_ending_one_section_does_not_deny_the_first_finding_of_the_next():
    case = _hypotension_case()
    for ending in ("Stable, no distress", "not sedated", "never intubated", "without pain"):
        joined = join_sections(
            {"illness_severity": ending, "patient_summary": "Hypotension from minute 10 to 16"}
        )
        graded = _result(case, joined)
        assert ("mentioned", "hypotension") in graded, ending
        assert ("unsupported", "hypotension") not in graded, ending


def test_a_denial_inside_a_section_still_counts_as_a_denial():
    case = _hypotension_case()
    graded = _result(case, join_sections({"patient_summary": "There was no hypotension"}))
    assert ("unsupported", "hypotension") in graded and ("missed", "hypotension") in graded


def test_a_handoff_across_sections_matches_the_same_handoff_in_one_box():
    case = _kitchen_sink()
    one_box = (
        "Hypotension from minute 10 to 16. Phenylephrine 100 mcg at minute 12. "
        "Desaturation from minute 30 to 31. Norepinephrine infusion from minute 40 to 50."
    )
    five = join_sections(
        {
            "illness_severity": "Hypotension from minute 10 to 16",
            "patient_summary": "Phenylephrine 100 mcg at minute 12",
            "action_list": "Desaturation from minute 30 to 31",
            "situation_awareness": "Norepinephrine infusion from minute 40 to 50",
        }
    )
    assert grade(case, five) == grade(case, one_box)
    assert [item.status for item in grade(case, five)].count("mentioned") == 4


def test_an_attempt_keeps_the_sections_apart_as_well_as_the_joined_text(tmp_path: Path):
    case = _hypotension_case()
    sections = {key: "" for key in KEYS} | {"patient_summary": "Hypotension from minute 10 to 16"}
    joined = join_sections(sections)
    target = tmp_path / "history.jsonl"
    record_attempt(case.case_id, joined, grade(case, joined), target, sections=sections)
    (row,) = load_attempts(target)
    assert row["handoff"] == joined
    assert row["sections"] == sections
    assert attempt_counts(row) == (1, 2)


def test_an_attempt_saved_before_the_sections_existed_still_loads(tmp_path: Path):
    target = tmp_path / "history.jsonl"
    old = {
        "at": "2026-11-01T10:00:00Z",
        "case_id": "FAKE-ART-LOW",
        "handoff": "hypotension at minute 12",
        "grades": [
            {"status": "mentioned", "kind": "hypotension", "evidence": "e"},
            {"status": "missed", "kind": "vasopressor", "evidence": "e"},
            {"status": "unsupported", "kind": "medication", "evidence": "e"},
        ],
    }
    target.write_text(json.dumps(old) + "\n", encoding="utf-8")
    record_attempt("FAKE-DESAT", "x", (), target, sections={"patient_summary": "x"})
    rows = load_attempts(target)
    assert [row["case_id"] for row in rows] == ["FAKE-ART-LOW", "FAKE-DESAT"]
    assert "sections" not in rows[0]
    assert attempt_counts(rows[0]) == (1, 2)


def test_found_counts_leave_unsupported_claims_out_of_both_numbers():
    assert found_counts(["mentioned", "unsupported", "missed", "mentioned"]) == (2, 3)
    assert found_counts(["unsupported"]) == (0, 0)
    assert found_counts([]) == (0, 0)
