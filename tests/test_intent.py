"""The question classifier reads scenario and comparison questions by rules alone."""

from __future__ import annotations

import pytest

from src.llm.intent import AGE_TOLERANCE, classify
from src.services.retrieval import CaseFilters

PID_A, PID_B = "SYN0000085e40", "SYN0001690ad3"

# (question, kind, age, age_range, sex, procedures, agent, pids)
CASES = [
    ("55y woman, hysterectomy, sevoflurane, elevated PIP", "scenario", 55, None, "F", ("hysterectomy",), "sevoflurane", ()),
    ("58-year-old", "scenario", 58, None, None, (), None, ()),
    ("a 58 year old man", "scenario", 58, None, "M", (), None, ()),
    ("aged 58", "scenario", 58, None, None, (), None, ()),
    ("patients aged 50-60 with desflurane", "scenario", None, (50, 60), None, (), "desflurane", ()),
    ("aged between 60 and 50", "scenario", None, (50, 60), None, (), None, ()),
    ("F 55", "scenario", 55, None, "F", (), None, ()),
    ("55M", "scenario", 55, None, "M", (), None, ()),
    ("female 50-60 hysterectomy sevoflurane", "scenario", None, (50, 60), "F", ("hysterectomy",), "sevoflurane", ()),
    ("a woman in her 50s having a cholecystectomy", "scenario", None, (50, 59), "F", ("cholecystectomy",), None, ()),
    ("47 yo male, laparoscopic cholecystectomies", "scenario", 47, None, "M", ("laparoscopic", "cholecystectomy"), None, ()),
    ("female patients on isoflurane", "scenario", None, None, "F", (), "isoflurane", ()),
    ("compare SYN0000085e40 and SYN0001690ad3", "compare", None, None, None, (), None, (PID_A, PID_B)),
    ("syn0000085e40 vs syn0001690ad3", "compare", None, None, None, (), None, ("syn0000085e40", "syn0001690ad3")),
    ("SYN0000085e40, SYN0001690ad3 and SYN0002218390", "compare", None, None, None, (), None, (PID_A, PID_B, "SYN0002218390")),
    ("Compare high PIP cases", "compare", None, None, None, (), None, ()),
    ("sevoflurane versus desflurane pressures", "compare", None, None, None, (), None, ()),
    ("What was PIP versus ETCO2 at minute 30 in SYN00016867e6?", "question", None, None, None, (), None, ("SYN00016867e6",)),
    ("PIP vs ETCO2 during maintenance", "question", None, None, None, (), None, ()),
    ("heart rate compared with SpO2 in these cases", "question", None, None, None, (), None, ()),
    ("Compare PIP in SYN0000085e40", "question", None, None, None, (), None, (PID_A,)),
    ("What happened in SYN0000085e40?", "question", None, None, None, (), None, (PID_A,)),
    ("SYN0000085e40 and SYN0000085E40 again", "question", None, None, None, (), None, (PID_A,)),
    ("tell me about SYN0000085e40, a 55F", "question", None, None, None, (), None, (PID_A,)),
    ("Where does pressure peak?", "question", None, None, None, (), None, ()),
    ("Where does pressure peak in laparoscopic cases?", "question", None, None, None, (), None, ()),
    ("male patients", "question", None, None, None, (), None, ()),
    ("a woman and a man", "question", None, None, None, (), None, ()),
    ("How did PIP trend over 5 years of data?", "question", None, None, None, (), None, ()),
    ("Which cases have missing ETCO2 after 30 minutes?", "question", None, None, None, (), None, ()),
    ("", "question", None, None, None, (), None, ()),
]


@pytest.mark.parametrize(("question", "kind", "age", "age_range", "sex", "procedures", "agent", "pids"), CASES)
def test_classify_table(question, kind, age, age_range, sex, procedures, agent, pids):
    intent = classify(question)
    assert intent.kind == kind
    if kind == "scenario":
        assert intent.age == (None if age is None else float(age))
        assert intent.age_range == (None if age_range is None else (float(age_range[0]), float(age_range[1])))
        assert (intent.sex, intent.procedures, intent.agent) == (sex, procedures, agent)
    assert intent.pids == pids


def test_scenario_becomes_case_filters_and_a_scope_phrase():
    intent = classify("female 50-60 hysterectomy sevoflurane")
    assert intent.case_filters() == CaseFilters(
        age_tolerance=AGE_TOLERANCE, age_min=50.0, age_max=60.0, gender="F", procedures=("hysterectomy",), agent="sevoflurane",
    )
    assert intent.scenario_text() == "Scenario: female, 50-60, hysterectomy, sevoflurane"
    single = classify("58-year-old man")
    assert single.case_filters() == CaseFilters(age=58.0, age_tolerance=AGE_TOLERANCE, gender="M")
    assert single.scenario_text() == "Scenario: male, 58 ±10"


def test_an_age_outside_human_range_is_not_an_age():
    assert classify("a 400-year-old woman") == classify("hello")
