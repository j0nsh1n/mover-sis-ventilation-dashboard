"""Fictional handoff grading: real timeline hits, unsupported claims, and missing rows."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.modules.handoff_trainer.fixture_csv import load_practice_case
from src.modules.handoff_trainer.grading import Dose, PracticeCase, Sample, detect, grade
from src.modules.handoff_trainer.history import load_attempts, record_attempt

ROOT = Path(__file__).resolve().parents[1]


def _case(samples: tuple[Sample, ...], doses: tuple[Dose, ...] = ()) -> PracticeCase:
    return PracticeCase("FAKE-TEST", "Fictional test", samples, doses)


def _statuses(case: PracticeCase, text: str) -> list[tuple[str, str]]:
    return [(item.status, item.kind) for item in grade(case, text)]


def test_arterial_run_longer_than_five_minutes_and_a_bolus_are_events():
    case = _case(
        tuple(Sample(minute, map_art=60, spo2=98) for minute in range(10, 17)),
        (Dose("Phenylephrine", 12, amount=100, unit="mcg"),),
    )
    events = detect(case)
    assert [event.kind for event in events] == ["hypotension", "vasopressor"]
    assert events[0].evidence.startswith("Arterial MAP stayed under 65 mmHg from minute 10 to minute 16")
    assert events[1].dose_kind == "bolus"
    assert ("mentioned", "hypotension") in _statuses(
        case, "Arterial hypotension was sustained under 65 mmHg for minutes 10 to 16."
    )
    assert ("mentioned", "vasopressor") in _statuses(case, "Phenylephrine 100 mcg at minute 12.")


def test_exactly_five_minutes_or_a_single_low_saturation_does_not_count():
    short = _case(tuple(Sample(minute, map_art=60, spo2=98) for minute in range(0, 6)))
    assert detect(short) == ()
    assert ("unsupported", "hypotension") in _statuses(short, "Hypotension lasted 5 minutes.")
    blip = _case((Sample(4, map_art=80, spo2=88), Sample(5, map_art=80, spo2=97)))
    assert detect(blip) == ()


def test_two_consecutive_low_saturations_count_and_a_gap_does_not():
    run = _case((Sample(20, spo2=88, map_art=80), Sample(21, spo2=86, map_art=80)))
    assert [event.kind for event in detect(run)] == ["desaturation"]
    gap = _case((Sample(20, spo2=88, map_art=80), Sample(22, spo2=86, map_art=80)))
    assert detect(gap) == ()


def test_cuff_pair_must_span_more_than_five_minutes_without_a_long_gap():
    pair = _case((Sample(0, nmap=60, spo2=98), Sample(6, nmap=58, spo2=97)))
    events = detect(pair)
    assert len(events) == 1 and events[0].evidence.startswith("Noninvasive MAP")
    exact = _case((Sample(0, nmap=60, spo2=98), Sample(5, nmap=58, spo2=97)))
    assert detect(exact) == ()
    separated = _case((Sample(0, nmap=60, spo2=98), Sample(12, nmap=58, spo2=97)))
    assert detect(separated) == ()


def test_arterial_readings_win_over_a_low_cuff_and_artifacts_are_ignored():
    mixed = _case((Sample(0, map_art=80, nmap=50, spo2=98), Sample(6, map_art=82, nmap=48, spo2=97)))
    assert detect(mixed) == ()
    artifacts = _case((Sample(0, map_art=10, spo2=40), Sample(1, map_art=300, spo2=101)))
    assert [event.kind for event in detect(artifacts)] == ["missing_map", "missing_spo2"]


def test_keyword_alone_is_missed_and_a_false_drug_is_unsupported():
    case = _case(
        tuple(Sample(minute, map_art=60, spo2=98) for minute in range(0, 8)),
        (Dose("Norepinephrine", 2, end_minute=10, amount=50, unit="mcg"),),
    )
    assert ("missed", "hypotension") in _statuses(case, "hypotension")
    assert ("mentioned", "inotrope") not in _statuses(case, "Norepinephrine infusion from minute 2 to minute 10.")
    assert ("mentioned", "vasopressor") in _statuses(case, "Norepinephrine infusion from minute 2 to minute 10.")
    assert detect(case)[1].dose_kind == "infusion"
    assert ("unsupported", "medication") in _statuses(case, "Vasopressin was given.")
    assert ("unsupported", "hypotension") in _statuses(case, "There was no hypotension.")
    assert ("missed", "hypotension") in _statuses(case, "There was no hypotension.")


def test_nasal_phenylephrine_is_not_a_vasopressor_event():
    case = _case(
        (Sample(0, map_art=80, spo2=98),),
        (Dose("Phenylephrine nasal", 3, amount=2, unit="drops"),),
    )
    assert detect(case) == ()
    assert _statuses(case, "Phenylephrine nasal drops were given.") == []
    assert ("unsupported", "medication") in _statuses(case, "Phenylephrine 100 mcg was given.")


def test_a_wrong_minute_or_a_normal_saturation_does_not_mention_the_episode():
    arterial = _case(tuple(Sample(minute, map_art=60, spo2=98) for minute in range(10, 17)))
    wrong = _statuses(arterial, "hypotension at minute 99")
    assert ("mentioned", "hypotension") not in wrong
    assert ("unsupported", "hypotension") in wrong
    assert ("missed", "hypotension") in wrong
    assert ("mentioned", "hypotension") in _statuses(arterial, "Hypotension at minute 12.")
    desat = _case((Sample(20, spo2=88, map_art=80), Sample(21, spo2=86, map_art=80)))
    normal = _statuses(desat, "SpO2 was 98%")
    assert ("mentioned", "desaturation") not in normal
    assert ("unsupported", "desaturation") in normal
    assert ("missed", "desaturation") in normal
    assert ("mentioned", "desaturation") in _statuses(desat, "Desaturation from minute 20 to minute 21.")


def test_generic_words_do_not_count_as_a_handoff_detail():
    case = _case(tuple(Sample(minute, map_art=60, spo2=98) for minute in range(10, 17)))
    for text in ("hypotension sustained", "arterial hypotension", "hypotension for a minute"):
        assert _statuses(case, text) == [("missed", "hypotension")]


def test_spo2_missing_names_the_gap_and_is_not_a_desaturation_claim():
    case = _case(tuple(Sample(minute, map_art=82) for minute in range(0, 4)))
    assert _statuses(case, "SpO2 missing") == [("mentioned", "missing_spo2")]


def test_an_empty_timeline_misses_both_series():
    case = _case(())
    assert [event.kind for event in detect(case)] == ["missing_map", "missing_spo2"]
    assert _statuses(case, "Nothing to add.") == [("missed", "missing_map"), ("missed", "missing_spo2")]


def test_two_low_saturations_in_the_same_minute_do_not_count():
    same = _case((Sample(20, spo2=88, map_art=80), Sample(20, spo2=86, map_art=80)))
    assert detect(same) == ()


def test_missing_saturation_can_be_named_and_a_normal_claim_is_unsupported():
    case = _case(tuple(Sample(minute, map_art=82) for minute in range(0, 4)))
    assert [event.kind for event in detect(case)] == ["missing_spo2"]
    assert _statuses(case, "SpO2 was not recorded.") == [("mentioned", "missing_spo2")]
    assert ("unsupported", "missing_spo2") in _statuses(case, "Oxygenation was normal.")


def test_a_negative_statement_is_not_an_unsupported_claim_on_a_quiet_timeline():
    case = _case(tuple(Sample(minute, map_art=80, spo2=98) for minute in range(0, 4)))
    assert detect(case) == ()
    assert _statuses(case, "No phenylephrine was given and there was no hypotension.") == []


def test_practice_history_appends_one_local_row(tmp_path: Path):
    case = _case((Sample(0, map_art=80, spo2=98),))
    target = tmp_path / "history.jsonl"
    record_attempt(case.case_id, "nothing happened", grade(case, "nothing happened"), target)
    rows = load_attempts(target)
    assert len(rows) == 1
    assert rows[0]["case_id"] == "FAKE-TEST"


def test_fixture_directory_uses_surgery_start_and_both_clock_formats(tmp_path: Path):
    (tmp_path / "patient_information.csv").write_text(
        "PID,Surgery_start,Procedure\nFAKE9,2000-01-01 08:00:00,FAKE PROCEDURE\n",
        encoding="utf-8",
    )
    (tmp_path / "patient_observations.csv").write_text(
        "PID,Obs_time,MAP_ART\n"
        + "".join(f"FAKE9,2000-01-01 08:{minute:02d}:00,60\n" for minute in range(10, 18)),
        encoding="utf-8",
    )
    (tmp_path / "patient_vitals.csv").write_text(
        "PID,Obs_time,nMAP,SP02\nFAKE9,2000-01-01 08:10:00,\\N,98\n",
        encoding="utf-8",
    )
    (tmp_path / "patient_medication.csv").write_text(
        "PID,Start_time,End_time,Dose,Drug_name,Drug_units\n"
        "FAKE9,1/1/00 8:12,\\N,100,Phenylephrine,mcg\n"
        "FAKE9,1/1/00 8:20,1/1/00 9:00,50,Norepinephrine,mcg\n",
        encoding="utf-8",
    )
    case = load_practice_case(tmp_path, "FAKE9")
    kinds = [(event.kind, event.drug, event.dose_kind) for event in detect(case)]
    assert ("hypotension", None, None) in kinds
    assert ("vasopressor", "Phenylephrine", "bolus") in kinds
    assert ("vasopressor", "Norepinephrine", "infusion") in kinds
    assert not any(event.kind == "desaturation" for event in detect(case))


@pytest.mark.skipif(
    not (ROOT / "imports" / "handoff-trainer" / "tests" / "fixtures").exists(),
    reason="trainer history is not imported yet",
)
def test_imported_fake0001_has_hypotension_and_no_desaturation():
    case = load_practice_case(ROOT / "imports" / "handoff-trainer" / "tests" / "fixtures", "FAKE0001")
    events = detect(case)
    assert any(event.kind == "hypotension" for event in events)
    assert any(event.drug == "Phenylephrine" and event.dose_kind == "bolus" for event in events)
    assert any(event.drug == "Norepinephrine" and event.dose_kind == "infusion" for event in events)
    assert not any(event.kind == "desaturation" for event in events)


def test_handoff_package_does_not_import_the_emr_stack():
    forbidden = ("src.services", "src.pipeline", "src.llm", "src.runtime_paths", "data_context")
    for path in (ROOT / "src" / "modules" / "handoff_trainer").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            for name in modules:
                assert not any(name == item or name.startswith(item + ".") for item in forbidden), path


def _low_map(*spans: tuple[int, int], spo2: dict[int, float] | None = None) -> tuple[Sample, ...]:
    low = {minute for start, end in spans for minute in range(start, end + 1)}
    last = max([end for _, end in spans] + list(spo2 or {})) + 3
    return tuple(
        Sample(minute, map_art=60 if minute in low else 80, spo2=(spo2 or {}).get(minute, 98))
        for minute in range(0, last + 1)
    )


def test_a_minute_counts_only_for_the_finding_it_is_written_with():
    case = _case(_low_map((10, 16)), (Dose("Phenylephrine", 12, amount=100, unit="mcg"),))
    graded = _statuses(
        case, "Hypotension: arterial MAP below 65 from minute 10 to 16. Phenylephrine bolus given at minute 30."
    )
    assert ("mentioned", "hypotension") in graded
    assert ("unsupported", "hypotension") not in graded
    assert ("unsupported", "vasopressor") in graded
    assert ("missed", "vasopressor") in graded
    assert ("mentioned", "vasopressor") not in graded

    drug_only = _statuses(case, "Phenylephrine bolus given at minute 30.")
    assert ("missed", "hypotension") in drug_only
    assert ("unsupported", "hypotension") not in drug_only
    assert ("unsupported", "vasopressor") in drug_only


def test_details_in_one_sentence_go_to_the_finding_they_follow():
    case = _case(_low_map((10, 16)), (Dose("Phenylephrine", 12, amount=100, unit="mcg"),))
    graded = _statuses(case, "Phenylephrine 100 mcg at minute 12 for hypotension from minute 10 to 16.")
    assert ("mentioned", "hypotension") in graded
    assert ("mentioned", "vasopressor") in graded
    assert not any(status == "unsupported" for status, _ in graded)


def test_a_desaturation_minute_is_not_charged_to_the_hypotension_episode():
    case = _case(_low_map((10, 16), spo2={20: 88, 21: 86}))
    graded = _statuses(case, "Hypotension from minute 10 to 16. Desaturation from minute 20 to 21.")
    assert ("mentioned", "hypotension") in graded
    assert ("mentioned", "desaturation") in graded
    assert not any(status == "unsupported" for status, _ in graded)


def test_each_of_two_episodes_keeps_its_own_minutes():
    case = _case(_low_map((10, 16), (31, 38)))
    assert len([event for event in detect(case) if event.kind == "hypotension"]) == 2
    graded = _statuses(case, "Hypotension from minute 10 to 16 and again from minute 31 to 38.")
    assert graded.count(("mentioned", "hypotension")) == 2
    assert ("unsupported", "hypotension") not in graded
    wrong = _statuses(case, "Hypotension from minute 10 to 16 and again at minute 70.")
    assert ("unsupported", "hypotension") in wrong
