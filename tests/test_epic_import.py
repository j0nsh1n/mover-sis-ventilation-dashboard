"""Converting an invented Epic folder into the SIS layout: units, minutes, phases, safety and resume."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pandas as pd
import pytest

from src.config import load_thresholds
from src.pipeline.epic_import import (
    EPIC_EVENTS,
    EpicImportError,
    height_cm,
    import_epic,
    looks_like_epic_emr,
    read_import_marker,
    weight_kg,
)
from src.pipeline.phases import classify_event_names

# Invented identifiers: case LOG_IDs, patient MRNs and PAT_IDs that look like the real kinds.
A, B, C = "4001001", "4001002", "4001003"
MRN = {A: "70000001", B: "70000002", C: "70000003"}
PAT = {A: "AB12CD34EF56AB78", B: "11112222333344FF", C: "99990000AAAABBBB"}


def _t(minute: int, second: int = 0) -> str:
    return f"2020-02-03 {8 + minute // 60:02d}:{minute % 60:02d}:{second:02d}"


def _epic(tmp_path: Path) -> Path:
    root = tmp_path / "mover-epic"
    emr = root / "emr" / "EPIC_EMR" / "EMR"
    emr.mkdir(parents=True)
    pd.DataFrame([
        {"LOG_ID": A, "MRN": MRN[A], "BIRTH_DATE": "57", "HEIGHT": "5' 7", "WEIGHT": "2688", "SEX": "Male",
         "PRIMARY_PROCEDURE_NM": "Laparoscopic Cholecystectomy", "IN_OR_DTTM": "2/3/2020 7:50",
         "OUT_OR_DTTM": "2/3/2020 10:10", "AN_START_DATETIME": "2/3/2020 7:55", "AN_STOP_DATETIME": "2/3/2020 10:05"},
        {"LOG_ID": B, "MRN": MRN[B], "BIRTH_DATE": "40", "HEIGHT": "5' 2", "WEIGHT": "2000", "SEX": "Female",
         "PRIMARY_PROCEDURE_NM": "Thyroidectomy", "IN_OR_DTTM": "2/3/2020 7:50", "OUT_OR_DTTM": "2/3/2020 9:00",
         "AN_START_DATETIME": "2/3/2020 7:55", "AN_STOP_DATETIME": "2/3/2020 8:55"},
        {"LOG_ID": C, "MRN": MRN[C], "BIRTH_DATE": "71", "HEIGHT": "6' 0\"", "WEIGHT": "3200", "SEX": "Female",
         "PRIMARY_PROCEDURE_NM": "Total Knee Arthroplasty", "IN_OR_DTTM": "2/3/2020 7:50",
         "OUT_OR_DTTM": "2/3/2020 10:10", "AN_START_DATETIME": "2/3/2020 7:58", "AN_STOP_DATETIME": "2/3/2020 10:01"},
    ]).to_csv(emr / "patient_information.csv", index=False)
    pd.DataFrame([
        {"LOG_ID": A, "MRN": MRN[A], "EVENT_DISPLAY_NAME": "Intubation", "EVENT_TIME": "2/3/2020 8:05", "NOTE_TEXT": ""},
        {"LOG_ID": A, "MRN": MRN[A], "EVENT_DISPLAY_NAME": "Skin Incision", "EVENT_TIME": "2/3/2020 8:20",
         "NOTE_TEXT": "private note text"},
        {"LOG_ID": A, "MRN": MRN[A], "EVENT_DISPLAY_NAME": "Extubation", "EVENT_TIME": "2/3/2020 9:50", "NOTE_TEXT": ""},
        {"LOG_ID": C, "MRN": MRN[C], "EVENT_DISPLAY_NAME": "LMA  Placed", "EVENT_TIME": "2/3/2020 8:02", "NOTE_TEXT": ""},
        {"LOG_ID": C, "MRN": MRN[C], "EVENT_DISPLAY_NAME": "LMA Removed", "EVENT_TIME": "2/3/2020 9:55", "NOTE_TEXT": ""},
    ]).to_csv(emr / EPIC_EVENTS, index=False)
    meds = [
        (A, "PROPOFOL 10 MG/ML", "150", "mg", _t(5), "Given", "INTRA-OP"),
        (A, "CEFAZOLIN 2 G", "2", "g", _t(10), "Canceled Entry", "INTRA-OP"),
        (A, "MIDAZOLAM 1 MG/ML", "2", "mg", _t(-20), "Given", "PRE-OP"),
        (C, "PHENYLEPHRINE", "100", "mcg", _t(30), "Bolus", "INTRA-OP"),
    ]
    pd.DataFrame([{"LOG_ID": m[0], "MRN": MRN[m[0]], "MEDICATION_NM": m[1], "ADMIN_SIG": m[2], "DOSE_UNIT_NM": m[3],
                   "MED_ACTION_TIME": m[4], "MAR_ACTION_NM": m[5], "RECORD_TYPE": m[6]} for m in meds]).to_csv(
        emr / "patient_medications.csv", index=False)
    flow = root / "flowsheets" / "flowsheets_cleaned"
    flow.mkdir(parents=True)

    def row(case, name, minute, value, record="INTRA-OP", second=0):
        return {"Unnamed: 0": "0", "LOG_ID": case, "MRN": MRN[case], "FLO_NAME": "Data", "FLO_DISPLAY_NAME": name,
                "RECORD_TYPE": record, "RECORDED_TIME": _t(minute, second), "MEAS_VALUE": value, "UNITS": ""}

    part1 = [
        row(A, "ETCO2 (mmHg)", 10, "36"), row(A, "ETCO2 (mmHg)", 10, "38", second=40),  # last reading in a minute wins
        row(A, "Set Resp Rate", 10, "12"), row(A, "ETCO2RR", 10, "14"),  # measured beats set
        row(A, "Set Resp Rate", 11, "12"),  # no measured rate: the set rate stands in
        row(A, "SEV ET", 10, "2.1"), row(A, "SEV In", 10, "2.6"), row(A, "Set PEEP", 10, "5"),
        row(A, "PIP Observed", 10, "18"), row(A, "Exhaled TV (ml)", 10, "480"),
        row(A, "Heart Rate", 10, "72"), row(A, "NIBP", 10, "120/80"), row(A, "NIBP - MAP", 10, "93"),
        row(A, "SpO2", 10, "99"), row(A, "Bite Block Secure", 10, "Yes"),  # unmapped: dropped, counted
        row(B, "Heart Rate", 5, "80", record="PRE-OP"),  # B has no intra-op rows, so it is left out
    ]
    part2 = [
        row(C, "DES ET", 40, "5.9"), row(C, "DES In", 40, "6.5"), row(C, "Heart Rate", 40, "64"),
        row(A, "ETCO2 (mmHg)", 12, "40"),
    ]
    pd.DataFrame(part1).to_csv(flow / "flowsheet_part1.csv", index=False)
    pd.DataFrame(part2).to_csv(flow / "flowsheet_part2.csv", index=False)
    ids = root / "ids"
    ids.mkdir()
    pd.DataFrame([{"LOG_ID": k, "PAT_ID": PAT[k], "MRN": MRN[k]} for k in (A, B, C)]).to_csv(
        ids / "EPIC_MRN_PAT_ID.csv", index=False)
    return root


def _read(out: Path, name: str) -> pd.DataFrame:
    return pd.read_csv(out / name, dtype=str, keep_default_na=False)


def test_detection_tells_epic_from_sis(tmp_path):
    root = _epic(tmp_path)
    emr = root / "emr" / "EPIC_EMR" / "EMR"
    assert looks_like_epic_emr(emr)
    sis = tmp_path / "sis"
    sis.mkdir()
    pd.DataFrame({"PID": ["x"], "Age": ["50"]}).to_csv(sis / "patient_information.csv", index=False)
    (sis / EPIC_EVENTS).write_text("PID\n", encoding="utf-8")
    assert not looks_like_epic_emr(sis)
    (emr / EPIC_EVENTS).unlink()
    assert not looks_like_epic_emr(emr)


def test_unit_conversions():
    assert height_cm("5' 7") == 170.2
    assert height_cm("6' 0\"") == 182.9
    assert height_cm("172") == 172.0
    assert pd.isna(height_cm("unknown"))
    assert weight_kg("2688") == pytest.approx(76.2, abs=0.01)


def test_import_converts_cases_minutes_events_and_medications(tmp_path):
    out = tmp_path / "converted"
    result = import_epic(_epic(tmp_path), out, min_cell=1)
    assert result["finished"] and result["cases"] == 2

    info = _read(out, "patient_information.csv").set_index("PID")
    assert sorted(info.index) == [A, C]  # B had pre-op readings only (owner: intra-op cases only)
    assert float(info.loc[A, "Age"]) == 57 and info.loc[A, "Gender"] == "M" and info.loc[C, "Gender"] == "F"
    assert float(info.loc[A, "Ht"]) == 170.2 and float(info.loc[A, "Wt"]) == pytest.approx(76.2, abs=0.01)
    assert info.loc[A, "Surgery_start"] == "2020-02-03 08:20:00"  # the skin incision event
    assert info.loc[C, "Surgery_start"] == "2020-02-03 07:58:00"  # no incision: anesthesia start
    assert info.loc[A, "Surgery_end"] == "2020-02-03 10:05:00" and info.loc[A, "OR_start"] == "2020-02-03 07:50:00"

    vent = _read(out, "patient_ventilator.csv")
    def num(r, *cols):
        return tuple(float(r[c]) for c in cols)

    a10 = vent[(vent["PID"] == A) & (vent["Obs_time"] == _t(10))].iloc[0]
    assert num(a10, "ETC02", "RR", "PEEP", "PIP", "TV") == (38, 14, 5, 18, 480)
    assert a10["Agent"] == "S" and num(a10, "Agent_Et", "Agent_Fi") == (2.1, 2.6)
    a11 = vent[(vent["PID"] == A) & (vent["Obs_time"] == _t(11))].iloc[0]
    assert float(a11["RR"]) == 12
    c40 = vent[vent["PID"] == C].iloc[0]
    assert c40["Agent"] == "D" and num(c40, "Agent_Et", "Agent_Fi") == (5.9, 6.5)
    assert set(vent[vent["PID"] == A]["Obs_time"]) == {_t(10), _t(11), _t(12)}  # rows from both parts

    vitals = _read(out, "patient_vitals.csv")
    v = vitals[(vitals["PID"] == A) & (vitals["Obs_time"] == _t(10))].iloc[0]
    assert num(v, "HRe", "nSBP", "nDBP", "nMAP", "SP02") == (72, 120, 80, 93, 99)

    events = _read(out, "patient_procedure_events.csv")
    assert list(events.columns) == ["PID", "Event_time", "Event_name"]
    roles = dict(zip(events["Event_name"], classify_event_names(events["Event_name"], load_thresholds()["phases"]),
                     strict=True))
    assert roles["LMA  Placed"] == "intubation" and roles["LMA Removed"] == "extubation"

    meds = _read(out, "patient_medication.csv")
    assert list(meds["Drug_name"]) == ["PROPOFOL 10 MG/ML", "PHENYLEPHRINE"]  # cancelled and pre-op left out
    assert meds.iloc[0]["Start_time"] == _t(5) and meds.iloc[0]["Drug_units"] == "mg"

    names = json.loads((out / "epic_import_names.json").read_text())
    assert {"name": "bite block secure", "cases": 1, "mapped": False} in names["names"]


def test_patient_identifiers_stay_in_the_private_wave_file_only(tmp_path):
    out = tmp_path / "converted"
    import_epic(_epic(tmp_path), out)
    waves = _read(out, "epic_wave_ids.csv")
    assert dict(zip(waves["PID"], waves["Wave_ID"], strict=True)) == {A: PAT[A].lower(), C: PAT[C].lower()}
    assert stat.S_IMODE((out / "epic_wave_ids.csv").stat().st_mode) == 0o600
    for f in out.iterdir():
        if f.is_file() and f.name != "epic_wave_ids.csv":
            text = f.read_text(encoding="utf-8")
            for secret in (*MRN.values(), *PAT.values(), PAT[A].lower(), "private note text"):
                assert secret not in text, (f.name, secret)


def test_rare_names_are_pooled_in_the_name_report(tmp_path):
    out = tmp_path / "converted"
    import_epic(_epic(tmp_path), out, min_cell=2)
    names = json.loads((out / "epic_import_names.json").read_text())
    assert [n["name"] for n in names["names"]] == ["heart rate"]  # the only intra-op name seen in two cases
    assert names["pooled_names"] > 0


def test_the_output_may_not_sit_inside_the_epic_folder(tmp_path):
    root = _epic(tmp_path)
    with pytest.raises(EpicImportError):
        import_epic(root, root / "converted")
    with pytest.raises(EpicImportError):
        import_epic(root, root)


def test_small_chunks_and_a_resumed_run_give_the_same_result(tmp_path):
    root = _epic(tmp_path)
    whole = tmp_path / "whole"
    import_epic(root, whole)
    resumed = tmp_path / "resumed"
    calls = {"n": 0}

    def stop_after_first_part() -> bool:
        calls["n"] += 1
        return calls["n"] > 1

    partial = import_epic(root, resumed, chunk_rows=2, should_stop=stop_after_first_part)
    assert not partial["finished"] and len(partial["parts_done"]) == 1
    assert read_import_marker(resumed) is None
    done = import_epic(root, resumed, chunk_rows=2)
    assert done["finished"] and read_import_marker(resumed)["cases"] == 2
    for name in ("patient_ventilator.csv", "patient_vitals.csv", "patient_information.csv"):
        a = _read(whole, name).sort_values(["PID", *(["Obs_time"] if "Obs_time" in _read(whole, name) else [])])
        b = _read(resumed, name).sort_values(["PID", *(["Obs_time"] if "Obs_time" in _read(resumed, name) else [])])
        pd.testing.assert_frame_equal(a.reset_index(drop=True), b.reset_index(drop=True))


def test_the_sis_pipeline_runs_on_a_converted_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("MOVER_ALLOW_EXTERNAL_OUTPUT", "1")
    from src.pipeline.run import run_pipeline

    root = _epic(tmp_path)
    # The pipeline only picks cases with enough ventilator minutes, so both cases get two hours of readings.
    rows = [{"Unnamed: 0": "0", "LOG_ID": case, "MRN": MRN[case], "FLO_NAME": "Data", "FLO_DISPLAY_NAME": name,
             "RECORD_TYPE": "INTRA-OP", "RECORDED_TIME": _t(m), "MEAS_VALUE": value, "UNITS": ""}
            for case in (A, C) for m in range(0, 120)
            for name, value in (("ETCO2 (mmHg)", "37"), ("Set Resp Rate", "12"), ("Exhaled TV (ml)", "480"),
                                ("Set PEEP", "5"), ("PIP Observed", "18"), ("FiO2 (%)", "50"), ("Heart Rate", "70"),
                                ("SpO2", "99"))]
    pd.DataFrame(rows).to_csv(root / "flowsheets" / "flowsheets_cleaned" / "flowsheet_part3.csv", index=False)
    out = tmp_path / "converted"
    import_epic(root, out)
    processed = tmp_path / "processed"
    run_pipeline(emr_dir=str(out), output_dir=str(processed), seed=1, preset="default", n_cases=10,
                 write_sample_csv=False)
    cases = pd.read_parquet(processed / "cases.parquet").set_index("PID")
    assert cases.loc[A, "phase_source"] == "events"  # Intubation and Extubation events
    assert cases.loc[C, "phase_source"] == "events"  # "LMA  Placed" and "LMA Removed"


def test_settings_say_raw_epic_records_need_converting_and_name_a_converted_folder(tmp_path):
    from src.desktop.settings_ui import check_emr_folder
    from src.runtime_paths import EpicRecordsNotConverted, resolve_emr_directory

    root = _epic(tmp_path)
    for chosen in (root, root / "emr" / "EPIC_EMR" / "EMR"):
        with pytest.raises(EpicRecordsNotConverted):
            resolve_emr_directory(chosen)
        check = check_emr_folder(str(chosen))
        assert check.state == "problem" and check.text.startswith("These are MOVER Epic records. Convert them once")
    out = tmp_path / "converted"
    import_epic(root, out)
    assert resolve_emr_directory(out) == out.resolve()
    assert check_emr_folder(str(out)).text.startswith("Epic records (converted) · ")


def test_the_epic_wave_root_is_accepted_with_waveforms_two_levels_down(tmp_path):
    from src.runtime_paths import resolve_wave_directory

    root = tmp_path / "wave_v2"
    (root / "wave_1" / "UCI_part1" / "Waveforms" / "ab").mkdir(parents=True)
    assert resolve_wave_directory(root) == root.resolve()


def test_a_converted_folder_gets_its_own_processed_folder(tmp_path):
    from src.runtime_paths import default_processed_for_emr

    out = tmp_path / "games" / "mover-epic-sis"
    import_epic(_epic(tmp_path), out)
    assert default_processed_for_emr(out) == (tmp_path / "games" / "mover-epic-sis-processed").resolve()
