"""LLM tool schemas, parsing, tools, and verification."""

from __future__ import annotations

import pandas as pd

from src.llm.agent import parse_tool_calls, verify_answer
from src.llm.prompts import SYSTEM_PROMPT, build_system_prompt, user_message
from src.llm.schemas import AI_TOOL_NAMES, AI_TOOLS, extract_tool_calls, strip_think
from src.llm.tools import (
    SessionState,
    execute_tool,
    tool_corpus_overview,
    tool_find_similar_cases,
    tool_search_cases,
    tool_select_case,
    tool_summarize_management,
    tool_top_anomaly_cases,
    tool_verify_selection,
)


def _sample_session() -> SessionState:
    cases = pd.DataFrame(
        [
            {
                "PID": "aabb0011",
                "Age": 50,
                "Gender": "F",
                "Procedure_short": "Laparoscopic cholecystectomy",
                "primary_agent_name": "sevoflurane",
                "anomaly_score": 40,
                "n_warn": 2,
                "n_critical": 1,
                "median_PIP": 28,
                "top_rules": "pip_high(5)",
            },
            {
                "PID": "ccdd0022",
                "Age": 60,
                "Gender": "M",
                "Procedure_short": "Total knee arthroplasty",
                "primary_agent_name": "desflurane",
                "anomaly_score": 10,
                "n_warn": 0,
                "n_critical": 0,
            },
        ]
    )
    ts = pd.DataFrame(
        {
            "PID": ["aabb0011"] * 5 + ["ccdd0022"] * 5,
            "t_min": list(range(5)) * 2,
            "TV": [500] * 10,
            "PIP": [20] * 10,
            "PEEP": [5] * 10,
            "ETCO2": [38] * 10,
        }
    )
    flags = pd.DataFrame(
        {
            "PID": ["aabb0011", "aabb0011"],
            "rule_id": ["pip_high", "etco2_low"],
            "severity": ["warn", "info"],
            "t_min": [1, 2],
        }
    )
    return SessionState(cases=cases, timeseries=ts, flags=flags)


def test_schemas_cover_core_tools():
    assert "select_case" in AI_TOOL_NAMES
    assert "find_similar_cases" in AI_TOOL_NAMES
    assert "summarize_management" in AI_TOOL_NAMES
    assert "verify_selection" in AI_TOOL_NAMES
    assert all(t["type"] == "function" for t in AI_TOOLS)


def test_system_prompt_has_skills_and_tools():
    p = build_system_prompt(mode="analyze", model="qwen3:14b")
    assert "select_case" in p
    assert "management" in p.lower()
    assert "clinical" in p.lower()
    assert "qwen3" in p.lower() or "Qwen3" in p or "tool" in p.lower()
    assert "select_case" in SYSTEM_PROMPT


def test_user_message_shape():
    from src.llm.prompts import wrap_patient_description

    msg = user_message(
        "55y woman hysterectomy sevoflurane high PIP",
        session_header="Loaded cases: 2",
        mode="analyze",
    )
    assert "hysterectomy" in msg
    assert "Loaded cases: 2" in msg
    assert "workflow" in msg.lower() or "automatically" in msg.lower()
    wrapped = wrap_patient_description("chole sevo", mode="analyze")
    assert "chole sevo" in wrapped


def test_parse_tool_calls_legacy_and_json():
    text = """
TOOL: search_cases
query: chole sevo
limit: 5
END
"""
    calls = parse_tool_calls(text)
    assert len(calls) == 1
    assert calls[0].name == "search_cases"
    assert calls[0].arguments["query"] == "chole sevo"

    j = extract_tool_calls(
        '{"name":"select_case","arguments":{"pid":"aabb0011"}}'
    )
    assert j[0]["name"] == "select_case"
    assert j[0]["args"]["pid"] == "aabb0011"


def test_strip_think():
    assert "hello" in strip_think("<think>secret</think>\nhello")


def test_search_and_select_tools():
    s = _sample_session()
    out = tool_search_cases(s, query="cholecystectomy")
    assert "aabb0011" in out
    assert "aabb0011" in s.focus_pids
    out2 = tool_select_case(s, pid="aabb0011")
    assert "aabb0011" in out2
    assert s.active_pid == "aabb0011"


def test_find_similar_and_management():
    s = _sample_session()
    out = tool_find_similar_cases(
        s, procedure="cholecystectomy", agent="sevoflurane", limit=5
    )
    assert "aabb0011" in out
    mg = tool_summarize_management(s, pid="aabb0011")
    assert "MANAGEMENT" in mg or "sevoflurane" in mg.lower()
    v = tool_verify_selection(s)
    assert "VERIFY" in v


def test_auto_prefetch_patient_description():
    from src.llm.agent import _heuristic_prefetch

    s = _sample_session()
    traces = _heuristic_prefetch(
        "50 year old woman laparoscopic cholecystectomy sevoflurane elevated PIP",
        s,
        mode="analyze",
    )
    assert traces
    assert any("find_similar" in t or "search_cases" in t for t in traces)
    assert s.active_pid or s.focus_pids


def test_top_anomaly_and_overview():
    s = _sample_session()
    top = tool_top_anomaly_cases(s, limit=1)
    assert "aabb0011" in top
    ov = tool_corpus_overview(s)
    assert "2" in ov


def test_execute_unknown_tool():
    s = _sample_session()
    out = execute_tool("not_a_tool", s, {})
    assert "ERROR" in out


def test_verify_unknown_pid_and_footer():
    s = _sample_session()
    s.active_pid = "aabb0011"
    ans, notes = verify_answer(
        "Case deadbeef99 had severe hypoxia requiring dopamine.",
        s,
    )
    assert "Verification note" in ans or "unknown" in ";".join(notes)
    assert "Research extract only" in ans or "not for clinical" in ans.lower()


# --- research-safety: no prescriptive language, no unverified claims ---------------

# Verbatim from a real qwen3:14b answer that the old narrow regex passed.
_REAL_REJECTED_ANSWER = """
**Management Summary**:
- **Ventilatory adjustments**: Address high tidal volume (TV) per ideal body weight.
  Consider reducing TV, optimizing PEEP (median 7.9 cmH2O), and ensuring proper lung compliance.
- **Desaturation monitoring**: Episodes of desaturation with ventilation anomalies
  require immediate intervention (e.g., checking endotracheal tube patency, adjusting FiO2).
**Conclusion**: This case highlights the need for vigilant ventilatory management,
with focus on avoiding high TV, optimizing PIP/PEEP, and promptly addressing desaturation.
"""


def test_detects_the_directive_phrasing_that_shipped():
    from src.llm.agent import find_directive_phrases

    found = find_directive_phrases(_REAL_REJECTED_ANSWER)
    assert found, "the real rejected answer must be caught"
    blob = " ".join(found).lower()
    for expected in ("consider", "require", "focus on"):
        assert expected in blob, f"missed {expected!r}: {found}"


def test_descriptive_past_tense_is_not_flagged():
    """The record legitimately describes changes that were made."""
    from src.llm.agent import find_directive_phrases

    clean = (
        "In this extract, tidal volume was documented at a median of 618 mL "
        "(10.9 mL/kg IBW). PEEP was increased to 8 cmH2O at t=40 min and TV was "
        "reduced later in the case. pip_high fired on 35 minutes; the rule screens "
        "PIP above the configured threshold. The extract does not record why FiO2 "
        "was adjusted. Documented management included vecuronium and fentanyl."
    )
    assert find_directive_phrases(clean) == [], find_directive_phrases(clean)


def test_modal_and_imperative_advice_are_both_caught():
    from src.llm.agent import find_directive_phrases

    for bad in (
        "Tidal volume should be reduced to 6 mL/kg.",
        "- Optimize PEEP for this patient.",
        "PEEP must be increased.",
        "This warrants prompt attention.",
        "Monitor closely for desaturation.",
        "Clinicians need to address the high PIP.",
    ):
        assert find_directive_phrases(bad), f"not caught: {bad!r}"


def test_answer_must_state_the_matched_case_age():
    """Guards the observed 'Age: 55 (aligned)' error on a 60-year-old case."""
    from src.llm.agent import find_unstated_case_facts

    session = _sample_session()
    session.active_pid = "ccdd0022"  # this case is 60
    wrong = "Matched case ccdd0022. Age: 55 years (aligned with the described patient)."
    problems = find_unstated_case_facts(wrong, session)
    assert problems and "60" in problems[0]

    # the real failure: asserts the wrong age while "60" appears incidentally elsewhere
    sneaky = "Matched Case: 55-year-old woman. pip_high fired at t=44-60 min."
    problems = find_unstated_case_facts(sneaky, session)
    assert problems, "presence of the digits 60 must not satisfy the check"
    assert "60" in problems[0] and "55" in problems[0]

    for right in (
        "Closest match is ccdd0022, a 60-year-old (described patient was 55).",
        "PID ccdd0022 (60F, knee arthroplasty) — described patient was 55F.",  # no \b after 60
        "Matched ccdd0022: 60yo male.",
        "The record shows age 60.",
    ):
        assert find_unstated_case_facts(right, session) == [], right
    # a different age must still fail
    assert find_unstated_case_facts("ccdd0022 is 160 years old", session)


def test_unstated_facts_check_is_inert_without_a_selection():
    from src.llm.agent import find_unstated_case_facts

    session = _sample_session()
    assert find_unstated_case_facts("anything at all", session) == []


def test_verify_answer_flags_directives_and_keeps_footer():
    session = _sample_session()
    text, notes = verify_answer(_REAL_REJECTED_ANSWER, session)
    assert any(n.startswith("clinical_directive_language") for n in notes)
    assert "Directive clinical language was detected" in text
    assert "not for clinical care" in text


def test_answer_problems_quotes_offending_text_for_the_rewrite():
    from src.llm.agent import answer_problems

    problems = answer_problems(_REAL_REJECTED_ANSWER, _sample_session())
    assert problems
    assert any("prescriptive phrasing" in p for p in problems)
    # the model is shown its own words, which it corrects far more reliably
    assert any("Consider reducing" in p or "consider reducing" in p.lower() for p in problems)


def test_prompt_states_the_phrasing_rules_explicitly():
    from src.llm.prompts import REWRITE_INSTRUCTION, build_system_prompt

    prompt = build_system_prompt(mode="analyze")
    assert "FORBIDDEN phrasing" in prompt
    assert "PAST TENSE" in prompt
    assert "ACTUAL age" in prompt
    assert "consider" in prompt.lower() and "optimize" in prompt.lower()
    assert "{problems}" in REWRITE_INSTRUCTION


def test_dose_mismatch_against_the_medication_record(monkeypatch):
    """Observed: answer said 'Phenylephrine 50 mcg' for a case documenting 100 mcg."""
    from src.llm import agent as agent_mod

    session = _sample_session()
    session.active_pid = "aabb0011"
    med = pd.DataFrame(
        [
            {"Drug_name": "Phenylephrine", "Dose": "100", "Drug_units": "mcg"},
            {"Drug_name": "Vecuronium", "Dose": "4", "Drug_units": "mg"},
        ]
    )
    monkeypatch.setattr(
        "src.llm.case_context.load_case_medications", lambda pid, emr=None: med
    )

    wrong = "Phenylephrine was administered at 50 mcg."
    problems = agent_mod.find_medication_mismatches(wrong, session)
    assert problems and "100 mcg" in problems[0]

    right = "Phenylephrine 100 mcg was documented."
    assert agent_mod.find_medication_mismatches(right, session) == []


def test_weight_normalised_dose_is_rejected(monkeypatch):
    from src.llm import agent as agent_mod

    session = _sample_session()
    session.active_pid = "aabb0011"
    med = pd.DataFrame([{"Drug_name": "Vecuronium", "Dose": "4", "Drug_units": "mg"}])
    monkeypatch.setattr(
        "src.llm.case_context.load_case_medications", lambda pid, emr=None: med
    )
    problems = agent_mod.find_medication_mismatches(
        "Vecuronium was documented at 0.15 mg/kg.", session
    )
    assert problems and "weight-normalised" in problems[0]


def test_dose_check_is_silent_without_medication_data(monkeypatch):
    from src.llm import agent as agent_mod

    session = _sample_session()
    session.active_pid = "aabb0011"
    monkeypatch.setattr(
        "src.llm.case_context.load_case_medications",
        lambda pid, emr=None: (_ for _ in ()).throw(OSError("no EMR")),
    )
    assert agent_mod.find_medication_mismatches("Fentanyl 100 mcg", session) == []


def test_prompt_forbids_computing_doses():
    from src.llm.prompts import build_system_prompt

    prompt = build_system_prompt(mode="analyze")
    assert "do not compute them" in prompt.lower()
    assert "mg/kg" in prompt
