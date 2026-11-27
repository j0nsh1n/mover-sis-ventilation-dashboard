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


def _one_case(tmp_path, or_start, or_end, files):
    emr = tmp_path / "converted"
    emr.mkdir()
    pd.DataFrame([{"PID": PID, "OR_start": or_start, "OR_end": or_end}]).to_csv(emr / "patient_information.csv",
                                                                             index=False)
    pd.DataFrame([{"PID": PID, "Wave_ID": WAVE}]).to_csv(emr / "epic_wave_ids.csv", index=False)
    folder = tmp_path / "wave_v2" / "wave_1" / "UCI_part1" / "Waveforms" / "ab"
    made = {key: _xml(folder / f"{WAVE}IP-{stamp}-000Z.xml") for key, stamp in files.items()}
    return emr, tmp_path / "wave_v2", made


def test_summer_times_use_daylight_time(tmp_path):
    # 07:50-10:10 Pacific daylight time is 14:50-17:10 UTC.
    emr, root, f = _one_case(tmp_path, "2020-07-03 07:50:00", "2020-07-03 10:10:00",
                             {"in": "2020-07-03-15-00-00", "before": "2020-07-03-14-00-00", "after": "2020-07-03-17-30-00"})
    assert case_wave_files(PID, root, emr) == [f["in"]]


def test_the_repeated_autumn_hour_keeps_a_bounded_window(tmp_path):
    # 2020-11-01 01:30 happens twice in Los Angeles (08:30 and 09:30 UTC); the window covers both, nothing more.
    emr, root, f = _one_case(tmp_path, "2020-11-01 01:30:00", "2020-11-01 01:45:00",
                             {"first": "2020-11-01-08-40-00", "second": "2020-11-01-09-40-00",
                              "other_day": "2020-11-03-09-00-00"})
    assert case_wave_files(PID, root, emr) == [f["first"], f["second"]]


def test_a_case_without_room_times_gets_no_files(tmp_path):
    emr, root, _f = _one_case(tmp_path, "", "", {"any": "2020-02-03-16-00-00"})
    assert case_wave_files(PID, root, emr) == []


def test_an_upper_case_prefix_folder_is_found_once(tmp_path):
    emr = _converted(tmp_path)
    folder = tmp_path / "wave_v2" / "wave_1" / "UCI_part1" / "Waveforms" / "AB"
    f = _xml(folder / f"{WAVE.upper()}IP-2020-02-03-16-00-00-000Z.xml")
    assert case_wave_files(PID, tmp_path / "wave_v2", emr) == [f]
