"""Shared fixtures: tiny synthetic SIS EMR and cleaned frames."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.config import load_thresholds


@pytest.fixture(scope="session")
def thresholds():
    return load_thresholds("default", validate=True)


@pytest.fixture(autouse=True)
def isolate_ollama_process_state(monkeypatch):
    """
    Keep Ollama lifecycle tests from touching real processes.

    ``src.llm.service`` keeps the spawned server in a module global. Tests that
    patch ``Popen`` leave a mock there, and a later ``stop_ollama()`` used to feed
    that mock's pid to ``os.killpg`` — where a non-int pid coerces to 1 and
    ``killpg(1, …)`` is ``kill(-1, …)``: SIGTERM to every process the user owns.
    Reset the state per test, block real matches, and fail loudly on a broadcast.
    """
    import src.llm.service as svc

    def _guarded_killpg(pgid, sig):
        raise AssertionError(
            f"test attempted a real os.killpg({pgid}, {sig}) — "
            "signalling process groups is not allowed under pytest"
        )

    monkeypatch.setattr(svc.os, "killpg", _guarded_killpg)
    monkeypatch.setattr(svc, "_pgrep", lambda *a, **k: [])
    monkeypatch.setattr(svc, "_started_proc", None, raising=False)
    monkeypatch.setattr(svc, "_app_started_server", False, raising=False)
    yield
    svc._started_proc = None
    svc._app_started_server = False


@pytest.fixture
def synthetic_emr(tmp_path: Path) -> Path:
    """
    Minimal EMR directory with 2 surgeries, ~20 minutes of vent+vitals each.
    Includes artifacts (\\N, high PIP ramp, high ETCO2) for flag tests.
    """
    emr = tmp_path / "EMR"
    emr.mkdir()

    cases = pd.DataFrame(
        [
            {
                "PID": "caseA",
                "Age": 55,
                "Ht": 170,
                "Wt": 80,
                "Gender": "M",
                "OR_start": "2016-06-01 08:00:00",
                "OR_end": "2016-06-01 10:00:00",
                "Surgery_start": "2016-06-01 08:15:00",
                "Surgery_end": "2016-06-01 09:45:00",
                "Procedure": "Laparoscopic Cholecystectomy",
            },
            {
                "PID": "caseB",
                "Age": 40,
                "Ht": 160,
                "Wt": 60,
                "Gender": "F",
                "OR_start": "2016-06-02 09:00:00",
                "OR_end": "2016-06-02 10:30:00",
                "Surgery_start": "2016-06-02 09:10:00",
                "Surgery_end": "2016-06-02 10:20:00",
                "Procedure": "Appendectomy",
            },
        ]
    )
    cases.to_csv(emr / "patient_information.csv", index=False)

    vent_rows = []
    vit_rows = []
    # caseA: rising PIP + high ETCO2 + sevo
    for i in range(25):
        t = pd.Timestamp("2016-06-01 08:15:00") + pd.Timedelta(minutes=i)
        pip = 18 + i * 0.8  # rising
        etco2 = 55 if i > 10 else 38
        vent_rows.append(
            {
                "PID": "caseA",
                "Obs_time": t.strftime("%Y-%m-%d %H:%M:%S"),
                "Agent": "S",
                "Agent_Fi": 2.2,
                "Agent_Et": 2.0 if i < 15 else 2.8,  # drift
                "ETC02": etco2,
                "FIC02": 0.1,
                "TV": 520,
                "RR": 12,
                "PEEP": 5,
                "PIP": pip,
                "FIN20": 0,
                "ETN20": 0,
                "FI02": 50,
                "ET02": 45,
            }
        )
        vit_rows.append(
            {
                "PID": "caseA",
                "Obs_time": t.strftime("%Y-%m-%d %H:%M:%S"),
                "HRe": 78,
                "HRp": 78,
                "nSBP": 120,
                "nMAP": 85,
                "nDBP": 70,
                "SP02": 99 if i < 20 else 90,
            }
        )

    # caseB: normal-ish + some \\N
    for i in range(20):
        t = pd.Timestamp("2016-06-02 09:10:00") + pd.Timedelta(minutes=i)
        vent_rows.append(
            {
                "PID": "caseB",
                "Obs_time": t.strftime("%m/%d/%y %H:%M"),
                "Agent": "S" if i > 0 else "N",
                "Agent_Fi": "\\N" if i == 0 else 1.8,
                "Agent_Et": "\\N" if i == 0 else 1.5,
                "ETC02": 36,
                "FIC02": 0,
                "TV": 450,
                "RR": 10,
                "PEEP": 4,
                "PIP": 20,
                "FIN20": 0,
                "ETN20": 0,
                "FI02": 40,
                "ET02": 38,
            }
        )
        vit_rows.append(
            {
                "PID": "caseB",
                "Obs_time": t.strftime("%Y-%m-%d %H:%M:%S"),
                "HRe": "\\N" if i == 0 else 70,
                "HRp": 72,
                "nSBP": "\\N",
                "nMAP": "\\N",
                "nDBP": "\\N",
                "SP02": 98,
            }
        )

    pd.DataFrame(vent_rows).to_csv(emr / "patient_ventilator.csv", index=False)
    pd.DataFrame(vit_rows).to_csv(emr / "patient_vitals.csv", index=False)

    events = pd.DataFrame(
        [
            {"PID": "caseA", "Event_time": "2016-06-01 08:16:00", "Event_name": "Intubation"},
            {"PID": "caseA", "Event_time": "2016-06-01 09:40:00", "Event_name": "Extubation"},
        ]
    )
    events.to_csv(emr / "patient_procedure_events.csv", index=False)

    return emr


@pytest.fixture
def pipeline_result(synthetic_emr, tmp_path, monkeypatch):
    # Allow writing processed artifacts under pytest tmp (outside repo)
    monkeypatch.setenv("MOVER_ALLOW_EXTERNAL_OUTPUT", "1")
    from src.pipeline.run import run_pipeline

    out = tmp_path / "processed"
    return run_pipeline(
        emr_dir=synthetic_emr,
        output_dir=out,
        pids=["caseA", "caseB"],
        preset="default",
        write_sample_csv=False,
        validate=True,
    )
