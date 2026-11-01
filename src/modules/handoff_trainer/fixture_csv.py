"""Read the trainer's fictional CSV shape into a practice case."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from src.modules.handoff_trainer.grading import Dose, PracticeCase, Sample

_FORMATS = ("%Y-%m-%d %H:%M:%S", "%m/%d/%y %H:%M", "%m/%d/%y %H:%M:%S")


def load_practice_case(directory: Path, case_id: str) -> PracticeCase:
    """Load one fictional id from a directory of trainer CSV fixtures."""
    root = Path(directory)
    origin = _surgery_start(root / "patient_information.csv", case_id)
    samples = _samples(root, case_id, origin)
    doses = _doses(root / "patient_medication.csv", case_id, origin)
    return PracticeCase(case_id, "Fictional fixture", tuple(samples.values()), tuple(doses))


def _surgery_start(path: Path, case_id: str) -> datetime | None:
    for row in _rows(path):
        if row.get("PID") == case_id:
            return _parse(row.get("Surgery_start", ""))
    return None


def _samples(root: Path, case_id: str, origin: datetime | None) -> dict[int, Sample]:
    found: dict[int, dict[str, float]] = {}
    _fill_measurements(found, root / "patient_observations.csv", case_id, origin, {"MAP_ART": "map_art"})
    _fill_measurements(found, root / "patient_vitals.csv", case_id, origin, {"nMAP": "nmap", "SP02": "spo2"})
    if origin is None and found:
        shift = min(found)
        found = {minute - shift: values for minute, values in found.items()}
    return {
        minute: Sample(minute, map_art=values.get("map_art"), nmap=values.get("nmap"), spo2=values.get("spo2"))
        for minute, values in sorted(found.items())
    }


def _fill_measurements(
    found: dict[int, dict[str, float]],
    path: Path,
    case_id: str,
    origin: datetime | None,
    columns: dict[str, str],
) -> None:
    for row in _rows(path):
        if row.get("PID") != case_id:
            continue
        stamp = _parse(row.get("Obs_time", ""))
        if stamp is None or origin is None:
            continue
        minute = int((stamp - origin).total_seconds() // 60)
        bucket = found.setdefault(minute, {})
        for source, target in columns.items():
            value = _float(row.get(source, ""))
            if value is not None:
                bucket[target] = value


def _doses(path: Path, case_id: str, origin: datetime | None) -> list[Dose]:
    doses: list[Dose] = []
    for row in _rows(path):
        if row.get("PID") != case_id:
            continue
        start = _parse(row.get("Start_time", ""))
        if start is None or origin is None:
            continue
        end = _parse(row.get("End_time", ""))
        start_minute = int((start - origin).total_seconds() // 60)
        end_minute = None if end is None else int((end - origin).total_seconds() // 60)
        doses.append(
            Dose(
                row.get("Drug_name", "").strip(),
                start_minute,
                end_minute=end_minute,
                amount=_float(row.get("Dose", "")),
                unit=row.get("Drug_units", "").strip(),
            )
        )
    return doses


def _rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def _parse(value: str) -> datetime | None:
    text = value.strip()
    if text in {"", r"\N"}:
        return None
    for fmt in _FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _float(value: str) -> float | None:
    text = value.strip()
    if text in {"", r"\N"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None
