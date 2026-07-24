"""Hard safety limits. Override only via explicit env/constants in tests."""

from __future__ import annotations

import os

# Case sampling
MIN_N_CASES = 1
MAX_N_CASES = 500
DEFAULT_N_CASES = 50

MIN_VENT_ROWS_PER_CASE = 5
MAX_VENT_SCAN_ROWS = 5_000_000
MIN_VENT_SCAN_ROWS = 1_000

# Duration / window
MAX_OR_HOURS = 24.0
MAX_PAD_MINUTES = 120.0
MIN_PAD_MINUTES = 0.0

# Output / memory heuristics (soft)
MAX_TIMESERIES_ROWS_WARN = 2_000_000
MAX_FLAG_ROWS_WARN = 5_000_000

# Allowed threshold presets
ALLOWED_PRESETS = frozenset({"default", "strict", "lenient"})

# Required EMR files for core pipeline
REQUIRED_EMR_FILES = (
    "patient_information.csv",
    "patient_ventilator.csv",
    "patient_vitals.csv",
)

OPTIONAL_EMR_FILES = (
    "patient_procedure_events.csv",
    "patient_observations.csv",
    "patient_a_line.csv",
)

# Required columns after load (pre-clean)
REQUIRED_CASE_COLS = ("PID", "OR_start", "OR_end")
REQUIRED_VENT_COLS = ("PID", "Obs_time")
REQUIRED_VITALS_COLS = ("PID", "Obs_time")

# Required columns after merge+features (core dashboard signals)
REQUIRED_TS_COLS = (
    "PID",
    "Obs_time",
    "t_min",
)

# Flag schema
REQUIRED_FLAG_COLS = (
    "PID",
    "Obs_time",
    "t_min",
    "rule_id",
    "severity",
    "value",
    "message",
)
ALLOWED_SEVERITIES = frozenset({"info", "warn", "critical"})

# Case summary schema
REQUIRED_CASE_SUMMARY_COLS = (
    "PID",
    "anomaly_score",
    "n_warn",
    "n_critical",
)

def allow_external_output() -> bool:
    """Read at call time so tests can toggle MOVER_ALLOW_EXTERNAL_OUTPUT."""
    return os.environ.get("MOVER_ALLOW_EXTERNAL_OUTPUT", "").lower() in {
        "1",
        "true",
        "yes",
    }
