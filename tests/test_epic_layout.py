"""The Epic layout report lists structure only: no key, date, free text, person or rare name leaks out."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from src.pipeline.epic_layout import main, profile_csv, profile_root, profile_waves, render_markdown


def _events_table(tmp_path: Path) -> Path:
    rows = []
    for i in range(40):
        pat = f"P{1000000 + i}"
        rows.append({"PAT_ID": pat, "LOG_ID": f"{9000000 + i}", "EVENT_NAME": "Intubation",
                     "EVENT_TIME": f"2019-03-{(i % 28) + 1:02d} 08:{i % 60:02d}:00",
                     "SURGEON_NAME": "Dr Quillfeather", "NOTE": f"free note about bed {i} and the wound dressing",
                     "AGE": str(30 + i)})
        rows.append({"PAT_ID": pat, "LOG_ID": f"{9000000 + i}", "EVENT_NAME": "Extubation",
                     "EVENT_TIME": f"2019-03-{(i % 28) + 1:02d} 10:{i % 60:02d}:00",
                     "SURGEON_NAME": "Dr Quillfeather", "NOTE": f"another note {i}", "AGE": str(30 + i)})
    for i in range(3):  # a rare name, seen for three patients only
        rows.append({"PAT_ID": f"P{1000000 + i}", "LOG_ID": f"{9000000 + i}", "EVENT_NAME": "Zygomatic Rarity",
                     "EVENT_TIME": "2019-03-01 09:00:00", "SURGEON_NAME": "Dr Quillfeather",
                     "NOTE": "x", "AGE": str(30 + i)})
    path = tmp_path / "emr" / "patient_procedure events.csv"
    path.parent.mkdir(parents=True)
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _wave_root(tmp_path: Path) -> Path:
    root = tmp_path / "wave_1"
    case = root / "UCI_part1" / "Waveforms" / "3f" / "3f2a9c41d07e55b1"
    case.mkdir(parents=True)
    (case / "a.xml").write_text(
        '<cpcArchive><cpc datetime="2019-03-01T08:00:00Z"><mg name="GE_ART">'
        '<m name="Wave">U0VDUkVUU0FNUExFUw==</m><m name="Hz">180</m></mg></cpc></cpcArchive>',
        encoding="utf-8")
    (case / "empty.xml").write_text("", encoding="utf-8")
    return root


def test_names_for_enough_patients_are_listed_and_nothing_private_is(tmp_path):
    _events_table(tmp_path)
    report = profile_root(tmp_path, wave_roots=[_wave_root(tmp_path)], min_cell=11)
    text = render_markdown(report) + json.dumps(report)
    cols = {c["name"]: c for c in report["tables"][0]["columns"]}
    assert report["tables"][0]["patient_key"] == "PAT_ID"
    assert cols["PAT_ID"]["kind"] == "id" and cols["LOG_ID"]["kind"] == "id"
    assert cols["EVENT_TIME"] == {**cols["EVENT_TIME"], "kind": "datetime", "shape": "YYYY-MM-DD HH:MM:SS"}
    assert cols["EVENT_NAME"]["names"] == {"Intubation": 40, "Extubation": 40}
    assert cols["EVENT_NAME"]["pooled_names"] == 1
    assert cols["NOTE"]["kind"] == "text"
    assert cols["SURGEON_NAME"]["kind"] == "id"
    assert cols["AGE"]["kind"] == "number" and cols["AGE"]["p5_p50_p95"][1] == 49.0  # 83 ages: 30-69 twice plus 30, 31, 32
    for private in ("P1000000", "9000000", "2019-03", "Quillfeather", "free note", "Zygomatic", "U0VDUkVU"):
        assert private not in text, private


def test_numbers_from_too_few_patients_have_no_percentiles(tmp_path):
    path = tmp_path / "t.csv"
    pd.DataFrame({"PAT_ID": [f"P{1000000 + i}" for i in range(5)], "WEIGHT": ["70", "80", "90", "71", "66"]}).to_csv(
        path, index=False)
    col = {c["name"]: c for c in profile_csv(path, min_cell=11)["columns"]}["WEIGHT"]
    assert col["kind"] == "number" and "p5_p50_p95" not in col and col["patients"] == "<11"


def test_a_table_without_a_patient_key_lists_no_names(tmp_path):
    path = tmp_path / "t.csv"
    pd.DataFrame({"FLO_MEAS_NAME": ["PEEP"] * 50 + ["TV"] * 50}).to_csv(path, index=False)
    col = profile_csv(path, min_cell=11)["columns"][0]
    assert col["kind"] == "label" and col["names"] == "not listed: no patient key column"
    assert "PEEP" not in render_markdown({"min_cell": 11, "sample_rows": 1, "tables": [
        {"folder": ".", **profile_csv(path, min_cell=11)}], "waves": []})


def test_wave_report_gives_shapes_counts_and_xml_names_only(tmp_path):
    w = profile_waves(_wave_root(tmp_path))
    assert w["files"] == 2 and w["empty_files"] == 1
    assert w["folder_shapes"] == {"UCI_part1/Waveforms/<2 hex>/<16 hex>": 1}
    assert w["signal_groups"] == {"GE_ART": 1} and set(w["measure_names"]) == {"Wave", "Hz"}
    assert "cpc@datetime" in w["xml_attributes"]
    assert "3f2a9c41d07e55b1" not in json.dumps(w) and "U0VDUkVU" not in json.dumps(w)


def test_tables_under_a_waveform_tree_are_not_searched(tmp_path):
    _events_table(tmp_path)
    stray = _wave_root(tmp_path) / "UCI_part1" / "Waveforms" / "stray.csv"
    stray.write_text("PAT_ID\nP1\n", encoding="utf-8")
    assert [t["file"] for t in profile_root(tmp_path)["tables"]] == ["patient_procedure events.csv"]


def test_cli_writes_markdown_and_json(tmp_path):
    _events_table(tmp_path)
    out, js = tmp_path / "layout.md", tmp_path / "layout.json"
    main(["--root", str(tmp_path), "--out", str(out), "--json", str(js)])
    assert "patient_procedure events.csv" in out.read_text() and json.loads(js.read_text())["min_cell"] == 11


def test_a_file_without_a_header_row_never_shows_its_first_row(tmp_path):
    path = tmp_path / "raw.csv"
    rows = [["4a12f7053d92ae68", "2021-11-09 07:58:00", "Surgery", "-1"],
            ["5b23f8164e03bf79", "2021-11-10 08:10:00", "Surgery", "-1"]] * 30
    pd.DataFrame(rows).to_csv(path, index=False, header=False)
    t = profile_csv(path, min_cell=11)
    text = json.dumps(t) + render_markdown({"min_cell": 11, "sample_rows": 1, "tables": [{"folder": ".", **t}], "waves": []})
    assert t["header_row"] is False and t["sampled_rows"] == 60
    assert [c["name"] for c in t["columns"]] == ["column 1", "column 2", "column 3", "column 4"]
    assert "No header row" in text
    for private in ("4a12f705", "5b23f816", "2021-11"):
        assert private not in text, private


def test_one_value_like_name_in_a_real_header_is_masked(tmp_path):
    path = tmp_path / "t.csv"
    pd.DataFrame({"PAT_ID": [f"P{1000000 + i}" for i in range(20)], "EVENT_NAME": ["Intubation"] * 20,
                  "2021-11-09 07:58:00": ["x"] * 20}).to_csv(path, index=False)
    t = profile_csv(path, min_cell=11)
    assert t["header_row"] is True
    assert [c["name"] for c in t["columns"]][2] == "column 3 (name looks like a value)"
    assert "2021-11" not in json.dumps(t)
