"""Case context builder for local LLM Q&A."""

from __future__ import annotations

import pandas as pd

from src.llm.case_context import build_case_context
from src.llm.prompts import SYSTEM_PROMPT, user_message


def test_system_prompt_has_safety_rules():
    assert "Not for clinical" in SYSTEM_PROMPT or "clinical" in SYSTEM_PROMPT.lower()
    assert "ONLY" in SYSTEM_PROMPT or "only" in SYSTEM_PROMPT


def test_user_message_embeds_context():
    msg = user_message(
        "What agent was used?",
        session_header="Loaded cases: 1",
        active_briefing="CASE PID: abc\n- Age: 50",
        mode="chat",
    )
    assert "CASE PID: abc" in msg
    assert "What agent was used?" in msg
    assert "Patient / scenario" in msg or "description" in msg.lower()


def test_build_case_context_from_frames():
    cases = pd.DataFrame(
        [
            {
                "PID": "caseA",
                "Age": 55,
                "Gender": "M",
                "Procedure_short": "Lap chole",
                "primary_agent_name": "sevoflurane",
                "primary_agent": "S",
                "case_duration_min": 120,
                "anomaly_score": 10,
                "n_warn": 2,
                "n_critical": 1,
                "n_info": 0,
                "top_rules": "pip_high(5)",
                "n_minutes": 100,
            }
        ]
    )
    t0 = pd.Timestamp("2016-01-01 08:00:00")
    ts = pd.DataFrame(
        {
            "PID": ["caseA"] * 10,
            "Obs_time": [t0 + pd.Timedelta(minutes=i) for i in range(10)],
            "t_min": list(range(10)),
            "TV": [500] * 10,
            "PIP": list(range(18, 28)),
            "PEEP": [5.0] * 10,
            "ETCO2": [38.0] * 10,
            "Agent_Et": [1.8] * 10,
            "Agent": ["S"] * 10,
            "HR": [75] * 10,
            "SPO2": [99] * 10,
        }
    )
    flags = pd.DataFrame(
        {
            "PID": ["caseA", "caseA"],
            "rule_id": ["pip_high", "pip_high"],
            "severity": ["warn", "critical"],
            "Obs_time": [t0, t0],
            "t_min": [5, 6],
            "value": [30, 32],
            "message": ["hi", "hi"],
        }
    )
    ctx = build_case_context(
        "caseA",
        cases=cases,
        timeseries=ts,
        flags=flags,
        include_emr_extras=False,
    )
    assert "caseA" in ctx
    assert "Lap chole" in ctx
    assert "sevoflurane" in ctx
    assert "pip_high" in ctx
    assert "Tidal volume" in ctx or "TV" in ctx


def test_context_names_the_phase_of_flags_and_episodes():
    t0 = pd.Timestamp("2016-01-01 08:00:00")
    cases = pd.DataFrame([{"PID": "caseA", "Age": 55, "Gender": "M"}])
    ts = pd.DataFrame(
        {"PID": ["caseA"] * 4, "Obs_time": [t0 + pd.Timedelta(minutes=i) for i in range(4)],
         "t_min": [-4.0, -3.0, -2.0, -1.0], "TV": [500.0] * 4}
    )
    flags = pd.DataFrame(
        {
            "PID": ["caseA"] * 3,
            "rule_id": ["spo2_low"] * 3,
            "severity": ["warn"] * 3,
            "Obs_time": [t0, t0 + pd.Timedelta(minutes=1), t0 + pd.Timedelta(minutes=2)],
            "t_min": [-4.0, -3.0, -2.0],
            "value": [90.0] * 3,
            "message": ["Low SpO2"] * 3,
            "phase": ["induction", "induction", "maintenance"],
        }
    )
    episodes = pd.DataFrame(
        [
            {"PID": "caseA", "rule_id": "spo2_low", "severity": "warn", "t_start_min": -4.0,
             "t_end_min": -3.0, "message": "Low SpO2", "phase": "induction", "phase_end": "induction"},
            {"PID": "caseA", "rule_id": "etco2_high", "severity": "warn", "t_start_min": -2.0,
             "t_end_min": 8.0, "message": "High ETCO2", "phase": "induction", "phase_end": "maintenance"},
            {"PID": "caseA", "rule_id": "pip_high", "severity": "warn", "t_start_min": 9.0,
             "t_end_min": 9.0, "message": "High PIP", "phase": None, "phase_end": None},
        ]
    )
    ctx = build_case_context(
        "caseA", cases=cases, timeseries=ts, flags=flags, episodes=episodes,
        include_emr_extras=False,
    )
    assert "Flagged minutes by phase: induction=2, maintenance=1" in ctx
    assert "spo2_low [warn]: t=-4–-3 min, during induction" in ctx
    assert "from induction to maintenance" in ctx
    pip_line = next(line for line in ctx.splitlines() if "pip_high [warn]: t=" in line)
    assert "during" not in pip_line


def test_list_case_flags_tool_reports_minutes_by_phase():
    from src.llm.tools import SessionState, tool_list_case_flags

    t0 = pd.Timestamp("2016-01-01 08:00:00")
    flags = pd.DataFrame(
        {
            "PID": ["caseA"] * 2,
            "rule_id": ["spo2_low"] * 2,
            "severity": ["warn"] * 2,
            "Obs_time": [t0, t0 + pd.Timedelta(minutes=1)],
            "t_min": [-4.0, -3.0],
            "value": [90.0] * 2,
            "message": ["Low SpO2"] * 2,
            "phase": ["induction", "induction"],
        }
    )
    session = SessionState(flags=flags)
    out = tool_list_case_flags(session, pid="caseA")
    assert "spo2_low [warn]: 2" in out
    assert "Flagged minutes by phase: induction=2" in out
