"""The offscreen desktop driver boots the real window and reports each step."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtGui import QImage

from src.pipeline.synthetic import main as write_synthetic_emr

ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / "tools" / "desktop_drive.py"


def _drive(*args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(ROOT), "QT_QPA_PLATFORM": "offscreen"}
    return subprocess.run(
        [sys.executable, str(DRIVER), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _distinct_colors(image: QImage) -> int:
    step_x = max(1, image.width() // 40)
    step_y = max(1, image.height() // 40)
    return len(
        {
            image.pixel(x, y)
            for x in range(0, image.width(), step_x)
            for y in range(0, image.height(), step_y)
        }
    )


def test_research_roundtrip_keeps_the_question(tmp_path):
    emr = tmp_path / "EMR"
    write_synthetic_emr(["--out", str(emr), "--n-cases", "10", "--seed", "3"])
    out = tmp_path / "run"

    proc = _drive("--emr", str(emr), "--out", str(out), "--scenario", "research-roundtrip")

    assert proc.returncode == 0, proc.stderr
    events = [json.loads(line) for line in (out / "events.jsonl").read_text().splitlines()]
    assert [e["step"] for e in events] == ["ask", "case", "return"]
    assert all(e["ok"] for e in events)
    by_step = {e["step"]: e for e in events}
    assert by_step["case"]["view"] == "detail"
    assert by_step["return"]["view"] == "canvas"
    assert by_step["return"]["question_kept"] is True
    assert by_step["ask"]["candidates"] > 0
    assert "highest sampled PIP" in by_step["ask"]["answer"]
    assert "failed" not in by_step["ask"]["research_status"].lower()
    for step in ("ask", "case", "return"):
        png = out / f"{step}.png"
        assert png.stat().st_size > 0
        image = QImage(str(png))
        assert not image.isNull()
        assert _distinct_colors(image) > 1, f"{png.name} is a blank grab"
    assert (out / "state" / "config" / "mover-sis-monitor" / "settings.json").is_file()


def test_invalid_emr_exits_nonzero_with_reason(tmp_path):
    emr = tmp_path / "empty"
    emr.mkdir()
    out = tmp_path / "run"

    proc = _drive("--emr", str(emr), "--out", str(out), "--scenario", "research-roundtrip")

    assert proc.returncode != 0
    assert "invalid EMR" in proc.stderr
    assert "patient_information.csv" in proc.stderr
    assert not (out / "ask.png").exists()


def test_refuses_an_out_folder_it_did_not_write(tmp_path):
    emr = tmp_path / "EMR"
    write_synthetic_emr(["--out", str(emr), "--n-cases", "10", "--seed", "3"])
    out = tmp_path / "someone-elses"
    (out / "state").mkdir(parents=True)
    keep = out / "state" / "keep.txt"
    keep.write_text("not the driver's")

    proc = _drive("--emr", str(emr), "--out", str(out), "--scenario", "research-roundtrip")

    assert proc.returncode == 2
    assert "not written by this driver" in proc.stderr
    assert keep.read_text() == "not the driver's"


def test_cases_overview_runs_with_a_scripted_model_and_frames_every_step(tmp_path):
    emr = tmp_path / "EMR"
    write_synthetic_emr(["--out", str(emr), "--n-cases", "12", "--seed", "3"])
    out = tmp_path / "run"

    proc = _drive("--emr", str(emr), "--out", str(out), "--scenario", "cases-overview",
                  "--llm", "scripted", "--frames")

    assert proc.returncode == 0, proc.stderr
    events = [json.loads(line) for line in (out / "events.jsonl").read_text().splitlines()]
    assert [e["step"] for e in events] == ["load-data", "open-cases", "sorted-by-score", "top-case-in-f"]
    # A step's frame is held long enough to see at the video's frame rate, and the sort and the
    # row click each add their own
    assert len(list((out / "frames").glob("*.png"))) >= 30


def test_empty_overview_scenario_accepts_a_scripted_model(tmp_path):
    emr = tmp_path / "EMR"
    write_synthetic_emr(["--out", str(emr), "--n-cases", "10", "--seed", "3"])

    proc = _drive("--emr", str(emr), "--out", str(tmp_path / "run"), "--scenario", "cases-overview-empty",
                  "--llm", "scripted")

    assert proc.returncode == 0, proc.stderr


@pytest.fixture(scope="module")
def prototype_emr(tmp_path_factory):
    emr = tmp_path_factory.mktemp("prototype") / "EMR"
    write_synthetic_emr(["--out", str(emr), "--n-cases", "30", "--seed", "7"])
    return emr


@pytest.mark.parametrize("llm", ["scripted", "off"])
@pytest.mark.parametrize("scenario", ["proto-peak", "proto-scenario", "proto-missing", "proto-flags", "proto-cause"])
def test_prototype_questions(prototype_emr, tmp_path, scenario, llm):
    out = tmp_path / "run"
    proc = _drive("--emr", str(prototype_emr), "--out", str(out), "--scenario", scenario, "--llm", llm)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"{scenario}: ok" in proc.stdout
    events = [json.loads(line) for line in (out / "events.jsonl").read_text().splitlines()]
    assert events[0]["step"] == "load-data" and events[0]["requested_cases"] == 30
    assert 0 < events[0]["loaded_cases"] <= 30
    assert all(event["ok"] for event in events)
    board = events[1]
    assert board["shortlist_pids"] and board["source_labels"]
    assert board["turns"][-1]["state"] == ("answered" if llm == "scripted" else "no_model")
    if llm == "off":
        assert board["turns"][-1]["badge"] == "No AI answer"
    final = events[-1]
    if scenario == "proto-peak" and llm == "scripted":
        assert final["selected_pip"] == final["max_pip"]
    elif scenario == "proto-missing" and llm == "scripted":
        assert final["cursor_minute"] == final["first_missing_minute"]
        assert "internal_etco2_gaps" in final
    elif scenario == "proto-flags" and llm == "scripted":
        assert "etco2" in final["selected_rule_id"]
        assert final["selected_rule_id"] in final["rule_detail"]
    elif scenario == "proto-cause" and llm == "scripted":
        assert "record cannot establish cause" in final["answer"].lower()
        assert "bronchodilator" not in final["answer"].lower()
    for event in events:
        image = QImage(str(out / event["png"]))
        assert not image.isNull() and _distinct_colors(image) > 1


@pytest.mark.parametrize("llm", ["scripted", "off"])
@pytest.mark.parametrize(("scenario", "scope"), [("intent-scenario", "Scenario: "), ("intent-compare", "Comparing ")])
def test_intent_questions_show_their_scope_line(prototype_emr, tmp_path, scenario, scope, llm):
    out = tmp_path / "run"
    proc = _drive("--emr", str(prototype_emr), "--out", str(out), "--scenario", scenario, "--llm", llm)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    events = [json.loads(line) for line in (out / "events.jsonl").read_text().splitlines()]
    final = events[-1]
    assert final["ok"] and final["scope_line"].startswith(scope) and final["shortlist"]
    assert final["turns"][-1]["state"] == ("answered" if llm == "scripted" else "no_model")
    image = QImage(str(out / final["png"]))
    assert not image.isNull() and _distinct_colors(image) > 1


def test_workspace_walk_covers_the_moved_outputs(prototype_emr, tmp_path):
    out = tmp_path / "run"
    proc = _drive("--emr", str(prototype_emr), "--out", str(out), "--scenario", "workspace-walk", "--llm", "scripted")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    events = [json.loads(line) for line in (out / "events.jsonl").read_text().splitlines()]
    assert all(event["ok"] for event in events)
    assert [event["step"] for event in events] == ["load-data", "question", "charts", "extra-signal", "scoring"]
    assert events[2]["charts_expanded"]
    assert events[3]["chart_track_names"] == ["PIP", "ETCO2", "HR", "SPO2"]
    assert events[4]["scoring_expanded"]


@pytest.mark.parametrize("scenario,llm,counts", [
    ("conversation-thread", "scripted", [0, 1, 2, 3, 4, 4]),
    ("conversation-thread", "off", [0, 1, 2, 3, 4, 4]),
    ("conversation-no-model", "off", [0, 1]),
    ("conversation-cancel", "scripted", [0, 1, 2, 2]),
    ("conversation-cancel", "off", [0, 1, 2, 2]),
    ("conversation-narrow", "scripted", [0, 1, 2]),
    ("conversation-narrow", "off", [0, 1, 2]),
])
def test_conversation_scenarios_record_panel_and_board(tmp_path, scenario, llm, counts):
    emr = tmp_path / "EMR"
    write_synthetic_emr(["--out", str(emr), "--n-cases", "30", "--seed", "3"])
    out = tmp_path / "run"
    proc = _drive("--emr", str(emr), "--out", str(out), "--scenario", scenario, "--llm", llm)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    events = [json.loads(line) for line in (out / "events.jsonl").read_text().splitlines()]
    assert [event["turn_count"] for event in events] == counts
    for event in events:
        assert event["ok"]
        assert "scope" in event and "shortlist_pids" in event
        assert all(set(turn) == {"question", "state", "badge", "answer", "cited_pids"} for turn in event["turns"])
        assert not QImage(str(out / event["png"])).isNull()
    if scenario == "conversation-thread":
        assert all(event["shortlist_pids"] for event in events[1:4])
        assert events[4]["shortlist_pids"] == []
        assert events[4]["turns"][-1]["state"] == ("answered" if llm == "scripted" else "no_model")
        assert events[-1]["shortlist_pids"] == events[1]["shortlist_pids"]
    elif scenario == "conversation-cancel":
        assert events[-1]["turns"][-1]["state"] == "cancelled"
        assert events[-1]["shortlist_pids"] == events[1]["shortlist_pids"]
    elif scenario == "conversation-narrow":
        assert all(event["panel_below_board"] for event in events[1:])
    if scenario != "conversation-thread":
        assert events[1]["shortlist_pids"]
