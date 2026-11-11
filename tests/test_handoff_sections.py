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
from src.modules.handoff_trainer.sections import NOT_GRADED, SECTIONS, has_text, join_sections

KEYS = [key for key, _ in SECTIONS]
HEADINGS = [heading for _, heading in SECTIONS]
GRADED_HEADINGS = [heading for key, heading in SECTIONS if key not in NOT_GRADED]


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


def test_worked_example_joins_in_order_under_headings_and_leaves_blank_and_ungraded_sections_out():
    joined = join_sections(
        {
            "synthesis": "Receiver repeats the plan",
            "action_list": "  Phenylephrine 100 mcg at minute 12.  ",
            "patient_summary": "Hypotension from minute 10 to 16",
            "illness_severity": "   ",
            "situation_awareness": "If MAP below 65, call senior",
        }
    )
    assert joined == (
        "Patient summary: Hypotension from minute 10 to 16.\n"
        "Action list: Phenylephrine 100 mcg at minute 12.\n"
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
    assert all(heading in headed for heading in GRADED_HEADINGS)
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
            "synthesis": "Norepinephrine infusion from minute 40 to 50",
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


def test_found_counts_leave_unsupported_claims_about_a_finding_or_drug_out_of_both_numbers():
    pairs = [("mentioned", "hypotension"), ("unsupported", "medication"), ("missed", "vasopressor"),
             ("mentioned", "desaturation"), ("unsupported", "hypotension")]
    assert found_counts(pairs) == (2, 3)
    assert found_counts([("unsupported", "vasopressor"), ("unsupported", "desaturation")]) == (0, 0)
    assert found_counts([]) == (0, 0)


def test_a_missing_series_is_its_own_item_so_a_wrong_claim_about_it_counts_in_the_total():
    """The contract: a missing MAP or SpO2 series is one item; stating a value for it is unsupported, not skipped."""
    assert found_counts([("unsupported", "missing_spo2")]) == (0, 1)
    assert found_counts([("unsupported", "missing_map")]) == (0, 1)
    assert found_counts([("mentioned", "missing_spo2"), ("unsupported", "missing_map")]) == (1, 2)
    assert found_counts([("missed", "missing_spo2")]) == (0, 1)
    no_spo2 = PracticeCase("FAKE-NO-SPO2", "t", tuple(Sample(m, map_art=82) for m in range(9)), ())
    graded = grade(no_spo2, join_sections({"patient_summary": "SpO2 98"}))
    assert [(item.status, item.kind) for item in graded] == [("unsupported", "missing_spo2")]
    row = {"grades": [{"status": item.status, "kind": item.kind, "evidence": item.evidence} for item in graded]}
    assert attempt_counts(row) == (0, 1)


def test_a_saved_attempt_with_odd_grades_still_counts_without_failing():
    assert attempt_counts({"grades": "none"}) == (0, 0)
    assert attempt_counts({"grades": [None, 5, {"status": "mentioned"}]}) == (1, 1)
    assert attempt_counts({}) == (0, 0)


# —— the section that is not graded ——


def test_the_situation_awareness_box_is_the_only_one_not_graded_and_says_so_in_plain_words():
    assert list(NOT_GRADED) == ["situation_awareness"]
    assert NOT_GRADED["situation_awareness"] == (
        "Not graded: plans and if-then statements are not checked against the timeline.")


def test_text_only_in_the_ungraded_section_joins_to_nothing_for_the_grader():
    assert join_sections({"situation_awareness": "If MAP below 65, call senior"}) == ""
    assert has_text({"situation_awareness": "If MAP below 65, call senior"})
    assert not has_text({key: " \n " for key in KEYS}) and not has_text({})


def test_a_plan_in_the_ungraded_box_gives_no_not_on_the_timeline_line():
    quiet = next(case for case in practice_cases() if case.case_id == "FAKE-QUIET")
    for plan in ("If MAP below 65, call senior. Watch for desaturation", "If hypotensive, phenylephrine ready"):
        sections = {"patient_summary": "Stable throughout", "situation_awareness": plan}
        assert grade(quiet, join_sections(sections)) == ()
        assert plan not in join_sections(sections)
        # The same words in a graded box are read as claims, so the check above can tell the two apart.
        claimed = grade(quiet, join_sections({"patient_summary": plan}))
        assert any(item.status == "unsupported" for item in claimed), plan


# Words that end a section and could, if the grader let them, change how the next one is read.
LEAKS = ["no", "not", "never", "without", "denied", "minute", "minutes 10 to", "between minutes 99 and", "no 0.5"]
PROBES = [
    ("Hypotension from minute 10 to 16", "FAKE-H"),
    ("Phenylephrine 100 mcg at minute 12", "FAKE-H"),
    ("Hypotension, MAP 60 mmHg", "FAKE-H"),
    ("SpO2 missing", "FAKE-N"),
]
GRADED_KEYS = [key for key in KEYS if key not in NOT_GRADED]


def _probe_case(name: str) -> PracticeCase:
    if name == "FAKE-H":
        return _hypotension_case()
    return PracticeCase("FAKE-N", "n", tuple(Sample(m, map_art=82) for m in range(9)), ())


@pytest.mark.parametrize("leak", LEAKS)
@pytest.mark.parametrize("probe,case_name", PROBES)
def test_nothing_written_in_one_section_changes_the_grade_of_another(leak, probe, case_name):
    case = _probe_case(case_name)
    alone = {"patient_summary": probe}
    expected = grade(case, join_sections(alone))
    for leaking in GRADED_KEYS:
        for reading in GRADED_KEYS:
            if leaking == reading:
                continue
            assert grade(case, join_sections({leaking: leak, reading: probe})) == expected, (leaking, reading, leak)


def test_attempts_saved_with_the_old_dash_separator_still_load_list_and_count(tmp_path: Path):
    from src.modules.handoff_trainer.history_view import history_rows

    old_text = ("Patient summary: Hypotension from minute 10 to 16.\n" + "-" * 44 + "\nAction list: Phenylephrine at minute 12.")
    old = {
        "at": "2026-11-05T09:30:00Z",
        "case_id": "FAKE-ART-LOW",
        "handoff": old_text,
        "sections": {key: "" for key in KEYS} | {"patient_summary": "Hypotension from minute 10 to 16"},
        "grades": [{"status": "mentioned", "kind": "hypotension", "evidence": "e"},
                   {"status": "mentioned", "kind": "vasopressor", "evidence": "e"},
                   {"status": "unsupported", "kind": "medication", "evidence": "e"}],
    }
    target = tmp_path / "history.jsonl"
    target.write_text(json.dumps(old) + "\n", encoding="utf-8")
    (row,) = load_attempts(target)
    assert row["handoff"] == old_text
    assert attempt_counts(row) == (2, 2)
    assert history_rows([row], {"FAKE-ART-LOW": "Run"})[0][1:] == ("Run (FAKE-ART-LOW)", "2 of 2")


# —— unreadable history lines ——


def test_a_line_that_is_not_an_attempt_is_skipped_counted_and_the_file_is_left_alone(tmp_path: Path):
    good = {"at": "2026-11-05T09:30:00Z", "case_id": "FAKE-ART-LOW", "handoff": "x", "grades": []}
    target = tmp_path / "history.jsonl"
    content = (json.dumps(good).encode() + b"\n{not json\n\n[1, 2]\n\"text\"\n\xff\xfe broken bytes\n"
               + json.dumps(good | {"case_id": "FAKE-DESAT"}).encode() + b"\n")
    target.write_bytes(content)
    rows = load_attempts(target)
    assert [row["case_id"] for row in rows] == ["FAKE-ART-LOW", "FAKE-DESAT"]
    assert rows.unreadable == 4
    assert target.read_bytes() == content, "the history file is never rewritten to repair it"
    # A new attempt is appended after the unreadable lines, which stay where they were.
    record_attempt("FAKE-QUIET", "y", (), target)
    assert target.read_bytes().startswith(content)
    assert load_attempts(target).unreadable == 4 and len(load_attempts(target)) == 3


def test_an_attempt_saved_after_a_cut_off_last_line_starts_on_its_own_line(tmp_path: Path):
    target = tmp_path / "attempts.jsonl"
    record_attempt("FAKE-QUIET", "first", (), target)
    whole = target.read_bytes()
    cut_off = b'{"at": "2026-01-01T00:00:00Z", "case_id": "FAKE-QUIET", "hando'
    target.write_bytes(whole + cut_off)
    record_attempt("FAKE-DESAT", "second", (), target)
    # what was in the file is untouched; the cut-off line is the only one that cannot be read
    assert target.read_bytes().startswith(whole + cut_off + b"\n")
    rows = load_attempts(target)
    assert [row["handoff"] for row in rows] == ["first", "second"] and rows.unreadable == 1
    # a file that already ends in a line break gets no blank line added
    record_attempt("FAKE-DESAT", "third", (), target)
    assert b"\n\n" not in target.read_bytes() and load_attempts(target).unreadable == 1


def test_a_missing_or_clean_history_file_reports_none_unreadable(tmp_path: Path):
    assert load_attempts(tmp_path / "none.jsonl") == () and load_attempts(tmp_path / "none.jsonl").unreadable == 0
    target = tmp_path / "history.jsonl"
    record_attempt("FAKE-QUIET", "y", (), target)
    assert load_attempts(target).unreadable == 0
