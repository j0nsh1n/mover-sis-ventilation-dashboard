"""Authored fictional practice cases. These rows are not from an EMR extract."""

from __future__ import annotations

from src.modules.handoff_trainer.grading import Dose, PracticeCase, Sample


def practice_cases() -> tuple[PracticeCase, ...]:
    return (
        PracticeCase(
            "FAKE-ART-LOW",
            "Fictional arterial pressure run",
            _steady(0, 9, map_art=80, spo2=98) + _steady(10, 16, map_art=60, spo2=98) + _steady(17, 20, map_art=85, spo2=98),
            (Dose("Phenylephrine", 12, amount=100, unit="mcg"),),
        ),
        PracticeCase(
            "FAKE-DESAT",
            "Fictional saturation run",
            _steady(0, 19, nmap=80, spo2=98) + (Sample(20, nmap=80, spo2=88), Sample(21, nmap=80, spo2=86)) + _steady(22, 24, nmap=80, spo2=97),
            (),
        ),
        PracticeCase(
            "FAKE-CUFF",
            "Fictional cuff readings",
            (Sample(0, nmap=60, spo2=98), Sample(6, nmap=58, spo2=97)),
            (),
        ),
        PracticeCase(
            "FAKE-NO-SPO2",
            "Fictional record with no saturation",
            _steady(0, 8, map_art=82),
            (),
        ),
        PracticeCase(
            "FAKE-QUIET",
            "Fictional uneventful record",
            _steady(0, 12, map_art=80, spo2=98),
            (),
        ),
        PracticeCase(
            "FAKE-NASAL",
            "Fictional topical dose",
            _steady(0, 8, map_art=80, spo2=98),
            (Dose("Phenylephrine nasal", 3, amount=2, unit="drops"),),
        ),
    )


def _steady(start: int, end: int, *, map_art: float | None = None, nmap: float | None = None, spo2: float | None = None) -> tuple[Sample, ...]:
    return tuple(Sample(minute, map_art=map_art, nmap=nmap, spo2=spo2) for minute in range(start, end + 1))
