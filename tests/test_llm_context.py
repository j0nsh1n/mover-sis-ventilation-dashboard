"""Case context builder for local LLM Q&A."""

from __future__ import annotations

import pandas as pd

from src.llm.case_context import build_case_context
from src.llm.prompts import SYSTEM_PROMPT, user_message


def test_system_prompt_has_safety_rules():
    assert "Not for clinical" in SYSTEM_PROMPT or "clinical" in SYSTEM_PROMPT.lower()
    assert "ONLY" in SYSTEM_PROMPT or "only" in SYSTEM_PROMPT


def test_user_message_embeds_context():
    msg = user_message("CASE PID: abc\n- Age: 50", "What agent was used?")
    assert "CASE PID: abc" in msg
    assert "What agent was used?" in msg


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
