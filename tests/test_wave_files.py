"""Finding a case's waveform files: Epic files by patient id and operating-room time, never naming the id."""

from __future__ import annotations

import pandas as pd

from src.wave_files import case_wave_files, has_case_waves, wave_presence_text

PID, OTHER = "4001001", "4001002"
WAVE, WAVE_OTHER = "ab12cd34ef56ab78", "ab99cd34ef56ab00"  # same prefix folder, different patients


def _converted(tmp_path):
    emr = tmp_path / "converted"
    emr.mkdir()
    pd.DataFrame([{"PID": PID, "OR_start": "2020-02-03 07:50:00", "OR_end": "2020-02-03 10:10:00"},
                  {"PID": OTHER, "OR_start": "2020-02-03 07:50:00", "OR_end": "2020-02-03 10:10:00"}]).to_csv(
        emr / "patient_information.csv", index=False)
    pd.DataFrame([{"PID": PID, "Wave_ID": WAVE.upper()}, {"PID": OTHER, "Wave_ID": WAVE_OTHER}]).to_csv(
        emr / "epic_wave_ids.csv", index=False)
    return emr


def _xml(path, text="<cpcArchive/>"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _wave_root(tmp_path):
    root = tmp_path / "wave_v2"
    one = root / "wave_1" / "UCI_part1" / "Waveforms" / "ab"
    three = root / "wave_3" / "UCI_part4" / "Waveforms" / "ab"
    # 07:50-10:10 Pacific standard time is 15:50-18:10 UTC.
    files = {
        "early_ok": _xml(one / f"{WAVE}IP-2020-02-03-15-30-00-000Z.xml"),  # starts 20 min before room-in
        "inside": _xml(three / f"{WAVE}CB-2020-02-03-17-00-00-000Z.xml"),  # another wave set
        "too_early": _xml(one / f"{WAVE}IP-2020-02-03-13-00-00-000Z.xml"),
        "after": _xml(one / f"{WAVE}IP-2020-02-03-18-20-00-000Z.xml"),
        "other_patient": _xml(one / f"{WAVE_OTHER}IP-2020-02-03-16-00-00-000Z.xml"),
        "empty": _xml(one / f"{WAVE}IP-2020-02-03-16-30-00-000Z.xml", ""),
    }
    return root, files


def test_epic_files_are_the_patients_files_inside_the_room_window(tmp_path):
    emr = _converted(tmp_path)
    root, files = _wave_root(tmp_path)
    assert case_wave_files(PID, root, emr) == [files["early_ok"], files["inside"]]
    assert case_wave_files(OTHER, root, emr) == [files["other_patient"]]
    assert case_wave_files("no-such-case", root, emr) == []
    assert has_case_waves(PID, root, emr) and not has_case_waves("no-such-case", root, emr)


def test_epic_presence_text_counts_files_and_never_names_the_patient(tmp_path):
    emr = _converted(tmp_path)
    root, _files = _wave_root(tmp_path)
    text = wave_presence_text(PID, root, emr)
    assert text == "2 waveform files for this case's operating-room time"
    assert WAVE not in text and WAVE.upper() not in text and "Waveforms" not in text
    assert wave_presence_text("no-such-case", root, emr) is None


def test_an_sis_case_folder_still_works_and_skips_empty_files(tmp_path):
    emr = tmp_path / "sis"
    emr.mkdir()
    case = tmp_path / "waves" / "Waveforms" / "03" / "03e4d41adce6e85f"
    good = _xml(case / "a.xml")
    _xml(case / "b.xml", "")
    assert case_wave_files("03e4d41adce6e85f", tmp_path / "waves", emr) == [good]
    assert has_case_waves("03e4d41adce6e85f", tmp_path / "waves", emr)
