"""Gate checks that tie each stated detail to its own cited case (live session, suite QA 13).

The case IDs, minutes and flag names are the synthetic ones the live session saw.
"""

from __future__ import annotations

from src.llm.agent import answer_problems, blocking_problems, find_directive_phrases
from src.llm.attribution import causal_claims, incomplete_exclusive_claims, misattributed_details, near_miss_pids
from src.llm.tools import SessionState

C224 = "SYN0010c224c1"
C665 = "SYN0015665602"
C54A = "SYN000454ab19"
SOURCES = [
    f"SOURCE EXTRACT (analyzed case) PID={C224}\nProcedure: Total Hip Arthroplasty. Age: 62.\n"
    "- Top flag rules: etco2_zero_vent(4), spo2_low(4)\n"
    "- etco2_zero_vent [critical]: t=46–49 min, during maintenance (Zero ETCO2 while ventilated "
    "(disconnect / sensor / esophageal))\nPIP median 19.2 cmH2O\nCase facts: PID " + C224,
    f"SOURCE EXTRACT (analyzed case) PID={C665}\nProcedure: Craniotomy. Age: 24.\n"
    "- Top flag rules: etco2_zero_vent(4)\n"
    "- etco2_zero_vent [critical]: t=162–165 min, during maintenance\nPIP median 17.5 cmH2O\nCase facts: PID " + C665,
    f"SOURCE EXTRACT (analyzed case) PID={C54A}\nProcedure: Total Knee Arthroplasty. Age: 22.\n"
    "- Top flag rules: compliance_concern(11), pip_rising(11), pip_high(7), spo2_low(4)\n"
    "- pip_rising [warn]: t=135–145 min, Rising PIP suggesting reduced compliance / obstruction\n"
    "PIP median 23.4 cmH2O\nCase facts: PID " + C54A,
]
LIVE_FLAG_ANSWER = (
    f"The case with an ETCO2 flag is **PID={C54A}** (from the first source extract).\n\n"
    "- **Flag**: `etco2_zero_vent [critical]` occurred during minutes **162–165** (maintenance phase).\n\n"
    "No other cases in the provided extracts have ETCO2 flags."
)


def _session() -> SessionState:
    session = SessionState(preset="default")
    session.cited_pids = [C224, C665, C54A]
    return session


def test_a_flag_and_minutes_moved_to_another_case_are_named():
    problems = misattributed_details(LIVE_FLAG_ANSWER, SOURCES)
    assert any(C54A in p and "etco2_zero_vent" in p for p in problems), problems
    assert any(C54A in p and "162" in p and C665 in p for p in problems), problems


def test_each_flag_stated_for_its_own_case_passes():
    right = (
        f"{C665} had etco2_zero_vent at minutes 162–165. {C224} had etco2_zero_vent at minutes 46–49. "
        f"{C54A} had pip_rising at minutes 135–145 with a PIP median of 23.4 cmH2O."
    )
    assert misattributed_details(right, SOURCES) == []
    listed = f"**PID={C665}**\n- etco2_zero_vent [critical] at minutes 162–165\n- PIP median 17.5 cmH2O"
    assert misattributed_details(listed, SOURCES) == []


def test_a_detail_with_no_case_nearby_is_left_to_the_number_check():
    assert misattributed_details("Cases used: three.\n\nPIP median 23.4 cmH2O was the highest.", SOURCES) == []


def test_a_cut_off_or_one_off_case_id_is_caught_and_the_full_one_passes():
    cited = ["SYN001496548a", "SYN0005ce6209"]
    assert near_miss_pids("PID SYN001496548 reached 18.5 cmH2O.", cited) == ["SYN001496548"]
    assert near_miss_pids("PID SYN001496548b reached 18.5 cmH2O.", cited) == ["SYN001496548b"]
    assert near_miss_pids("SYN001496548a reached 18.5 cmH2O; SYN0005ce6209 had 33.3.", cited) == []
    assert near_miss_pids("aabb0011 resembled SYN0a1b2c3d4e5 in PIP.", cited) == []


def test_a_cut_off_case_id_is_a_blocking_rewrite_problem():
    session = SessionState(preset="default")
    session.cited_pids = ["SYN001496548a", "SYN0005ce6209"]
    problems = answer_problems("PID SYN001496548 had a PIP of 18.5 cmH2O.", session)
    assert any("SYN001496548 is not one of the cited cases" in p for p in problems), problems
    assert blocking_problems(problems)


def test_an_only_one_claim_that_leaves_out_flagged_cases_is_named():
    problems = incomplete_exclusive_claims(LIVE_FLAG_ANSWER, SOURCES)
    assert any(C224 in p and "etco2_zero_vent" in p for p in problems), problems
    assert any(C665 in p for p in problems), problems
    native = f"Source 3 is {C54A}. Thus, no case with a PID explicitly listed in the sources has ETCO2 flags."
    assert len(incomplete_exclusive_claims(native, SOURCES)) == 2


def test_a_complete_list_passes():
    complete = f"{C224} and {C665} have etco2_zero_vent flags; no other cited case has ETCO2 flags."
    assert incomplete_exclusive_claims(complete, SOURCES) == []


def test_a_guessed_cause_or_diagnosis_is_named_and_a_quoted_rule_screen_is_not():
    live = (
        "This indicates that the increase in pressure (PIP) likely occurred due to decreased lung compliance "
        "(e.g., from pulmonary edema, ARDS, or chest trauma) or increased airway resistance (e.g., from bronchospasm)."
    )
    found = {c.lower() for c in causal_claims(live, SOURCES)}
    assert {"likely occurred", "due to", "pulmonary edema", "ards", "bronchospasm"} <= found, found
    quoted = "pip_rising fired at minutes 135–145: Rising PIP suggesting reduced compliance / obstruction."
    assert causal_claims(quoted, SOURCES) == []
    assert causal_claims("Rising PIP was flagged; the cause is not recorded.", SOURCES) == []
    assert causal_claims("The record does not say what the rise was due to.", SOURCES) == []


def test_causal_and_list_problems_reach_the_rewrite_but_do_not_withhold():
    text = f"{C665} had etco2_zero_vent at minutes 162–165, probably due to a disconnect. No other cases have ETCO2 flags."
    problems = answer_problems(text, _session(), sources=SOURCES)
    assert any(p.startswith("- causal or diagnostic claim") for p in problems), problems
    assert any("presents the list as complete" in p and C224 in p for p in problems), problems
    assert blocking_problems(problems) == []


def test_the_footer_paraphrase_is_not_advice_but_advice_still_is():
    assert find_directive_phrases("This extract describes historical data; findings do not imply clinical recommendations.") == []
    assert find_directive_phrases("It does not reflect clinical recommendations or outcomes.") == []
    assert find_directive_phrases("I recommend lowering the tidal volume.") == ["recommend"]
    assert find_directive_phrases("Consider reducing PEEP.") == ["Consider reducing"]
    assert "Recommendations" in find_directive_phrases("Recommendations: reduce PEEP.")
