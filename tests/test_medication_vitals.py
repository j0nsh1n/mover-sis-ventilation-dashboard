"""Medication and vital display: units, time, bolus versus infusion, and missing values."""

from __future__ import annotations

import ast
from datetime import datetime
from pathlib import Path

import pandas as pd

from src.modules.medication_vitals.align import DISCLAIMER, align_rows, fictional_demo

ROOT = Path(__file__).resolve().parents[1]


def test_fictional_demo_shows_units_bolus_infusion_and_a_missing_saturation():
    medications, timeseries = fictional_demo()
    rows = align_rows(medications, timeseries)
    by_subject = {row.subject: row for row in rows}
    assert by_subject["Phenylephrine"].kind == "bolus"
    assert by_subject["Phenylephrine"].unit == "mcg"
    assert by_subject["Phenylephrine"].value_text == "100 mcg"
    assert by_subject["Phenylephrine"].provenance == "patient_medication.csv"
    assert by_subject["Norepinephrine"].kind == "infusion"
    assert by_subject["Norepinephrine"].time_text == "minute 4 to 20"
    assert by_subject["Ephedrine"].value_text == "not recorded"
    assert by_subject["Ephedrine"].kind == "bolus"
    assert by_subject["Phenylephrine nasal"].kind == "bolus"
    saturation = [row for row in rows if row.subject == "SpO2" and row.time_text == "minute 4"]
    assert saturation[0].value_text == "missing"
    assert saturation[0].unit == "%"
    assert "why a drug was given" in DISCLAIMER
    assert "what to do next" in DISCLAIMER


def test_clock_times_align_when_a_case_start_is_known_and_stay_raw_without_one():
    medications = pd.DataFrame(
        [
            {
                "Drug_name": "Ephedrine",
                "Dose": 5,
                "Drug_units": "mg",
                "Start_time": "2000-01-01 08:10:00",
                "End_time": r"\N",
            }
        ]
    )
    timeseries = pd.DataFrame([{"t_min": 10, "HR": 70, "SPO2": 98, "nMAP": 80}])
    origin = datetime(2000, 1, 1, 8, 0, 0)
    aligned = align_rows(medications, timeseries, origin=origin)
    assert any(row.subject == "Ephedrine" and row.time_text == "minute 10" and row.kind == "bolus" for row in aligned)
    raw = align_rows(medications, timeseries, origin=None)
    drug = next(row for row in raw if row.subject == "Ephedrine")
    assert drug.time_text == "2000-01-01 08:10:00"
    assert drug.kind == "time not recorded"


def test_an_end_before_the_start_is_labeled_instead_of_called_an_infusion():
    medications = pd.DataFrame(
        [{"Drug_name": "Vasopressin", "Dose": 1, "Drug_units": "units", "t_min": 12, "end_min": 9}]
    )
    rows = align_rows(medications, pd.DataFrame())
    assert rows[0].kind == "end time is before the start time"
    assert rows[0].end is None, "the page does not pick which of the two times is right"
    assert rows[0].note == "end time is before the start time (start minute 12, end minute 9)"


def test_medication_package_does_not_open_the_emr_loader():
    forbidden = ("src.services", "src.pipeline", "src.llm", "src.runtime_paths", "data_context")
    for path in (ROOT / "src" / "modules" / "medication_vitals").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            for name in modules:
                assert not any(name == item or name.startswith(item + ".") for item in forbidden), path
