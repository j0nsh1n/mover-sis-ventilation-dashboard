"""Line up drug rows and measured vitals for display.

This does not say why a drug was given and does not recommend treatment.
Missing measurements stay missing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

DISCLAIMER = (
    "These rows line up recorded drugs and measurements. "
    "They do not say why a drug was given or what to do next."
)
PROVENANCE_MEDICATION = "patient_medication.csv"
PROVENANCE_VITALS = "case timeseries"
_FORMATS = ("%Y-%m-%d %H:%M:%S", "%m/%d/%y %H:%M", "%m/%d/%y %H:%M:%S")
_VITALS = (
    ("HR", "Heart rate", "bpm"),
    ("SPO2", "SpO2", "%"),
    ("nMAP", "Noninvasive MAP", "mmHg"),
    ("MAP_ART", "Arterial MAP", "mmHg"),
)


@dataclass(frozen=True)
class DisplayRow:
    time_text: str
    subject: str
    value_text: str
    unit: str
    kind: str
    provenance: str


def align_rows(
    medications: pd.DataFrame | None,
    timeseries: pd.DataFrame | None,
    *,
    origin: datetime | None = None,
) -> tuple[DisplayRow, ...]:
    """Return display rows. A shared minute axis is used only when both sides have one."""
    meds = _medication_rows(medications, origin)
    vitals = _vital_rows(timeseries)
    shared = any(row.time_text.startswith("minute ") for row in meds) and any(
        row.time_text.startswith("minute ") for row in vitals
    )
    if not shared:
        return tuple(vitals + meds)
    return tuple(sorted([*vitals, *meds], key=lambda row: (_minute_key(row.time_text), row.subject)))


def fictional_demo() -> tuple[pd.DataFrame, pd.DataFrame]:
    """A synthetic pair with a bolus, an infusion, a topical row, and a missing saturation."""
    medications = pd.DataFrame(
        [
            {"Drug_name": "Phenylephrine", "Dose": 100, "Drug_units": "mcg", "t_min": 0, "end_min": 0},
            {"Drug_name": "Norepinephrine", "Dose": 50, "Drug_units": "mcg", "t_min": 4, "end_min": 20},
            {"Drug_name": "Phenylephrine nasal", "Dose": 2, "Drug_units": "drops", "t_min": 6, "end_min": 6},
            {"Drug_name": "Ephedrine", "Dose": None, "Drug_units": "", "t_min": 8, "end_min": None},
        ]
    )
    timeseries = pd.DataFrame(
        [
            {"t_min": 0, "HR": 70, "SPO2": 98, "nMAP": 80},
            {"t_min": 4, "HR": 72, "SPO2": None, "nMAP": 78},
            {"t_min": 8, "HR": None, "SPO2": 97, "nMAP": 75},
        ]
    )
    return medications, timeseries


def _medication_rows(frame: pd.DataFrame | None, origin: datetime | None) -> list[DisplayRow]:
    if frame is None or frame.empty:
        return []
    rows: list[DisplayRow] = []
    for record in frame.to_dict(orient="records"):
        drug = str(record.get("Drug_name") or "").strip() or "drug name not recorded"
        amount = _amount(record.get("Dose"), record.get("Drug_units"))
        start_minute, end_minute, clock = _medication_time(record, origin)
        kind = _kind(start_minute, end_minute)
        if start_minute is None:
            time_text = clock or "time not recorded"
        elif kind == "infusion" and end_minute is not None:
            time_text = f"minute {start_minute} to {end_minute}"
        else:
            time_text = f"minute {start_minute}"
        rows.append(DisplayRow(time_text, drug, amount, _unit(record.get("Drug_units")), kind, PROVENANCE_MEDICATION))
    return rows


def _vital_rows(frame: pd.DataFrame | None) -> list[DisplayRow]:
    if frame is None or frame.empty or "t_min" not in frame.columns:
        return []
    rows: list[DisplayRow] = []
    ordered = frame.sort_values("t_min", kind="stable")
    for record in ordered.to_dict(orient="records"):
        minute = record.get("t_min")
        if _missing(minute):
            continue
        label = f"minute {_as_int(minute)}"
        for column, name, unit in _VITALS:
            if column not in frame.columns:
                continue
            value = record.get(column)
            shown = "missing" if _missing(value) else _number(_as_float(value))
            rows.append(DisplayRow(label, name, shown, unit, "measurement", PROVENANCE_VITALS))
    return rows


def _medication_time(record: dict, origin: datetime | None) -> tuple[int | None, int | None, str]:
    if "t_min" in record and not _missing(record.get("t_min")):
        start = _as_int(record["t_min"])
        end_value = record.get("end_min")
        end = None if _missing(end_value) else _as_int(end_value)
        return start, end, ""
    start_clock = _parse_clock(record.get("Start_time"))
    end_clock = _parse_clock(record.get("End_time"))
    clock = _clock_text(record.get("Start_time"), record.get("End_time"))
    if origin is None or start_clock is None:
        return None, None, clock
    start = int((start_clock - origin).total_seconds() // 60)
    end = None if end_clock is None else int((end_clock - origin).total_seconds() // 60)
    return start, end, clock


def _kind(start: int | None, end: int | None) -> str:
    if start is None:
        return "time not recorded"
    if end is None or end == start:
        return "bolus"
    if end > start:
        return "infusion"
    return "end time is before the start time"


def _amount(dose, unit) -> str:
    if _missing(dose):
        return "not recorded"
    text = _number(_as_float(dose))
    unit_text = "" if _missing(unit) else str(unit).strip()
    return f"{text} {unit_text}".strip()


def _unit(unit) -> str:
    if _missing(unit):
        return ""
    return str(unit).strip()


def _clock_text(start, end) -> str:
    start_text = "" if _missing(start) or str(start).strip() in {r"\N"} else str(start).strip()
    end_text = "" if _missing(end) or str(end).strip() in {r"\N"} else str(end).strip()
    if start_text and end_text:
        return f"{start_text} to {end_text}"
    return start_text


def _parse_clock(value) -> datetime | None:
    if _missing(value):
        return None
    text = str(value).strip()
    if text in {r"\N"}:
        return None
    for fmt in _FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _as_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return int(str(value))
    return int(value)


def _as_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return float(str(value))
    return float(value)


def _missing(value) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except TypeError:
        return False


def _number(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return str(value)


def _minute_key(time_text: str) -> int:
    if not time_text.startswith("minute "):
        return 10**9
    head = time_text.removeprefix("minute ").split(" ", 1)[0]
    try:
        return int(head)
    except ValueError:
        return 10**9
