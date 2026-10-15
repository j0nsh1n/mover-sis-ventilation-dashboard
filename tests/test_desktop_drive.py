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
