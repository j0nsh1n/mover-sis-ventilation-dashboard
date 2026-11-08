"""Fictional checklist stages, saved practice, keyboard marks, and EMR isolation."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QCheckBox

from src.modules.who_checklist.model import (
    MARK_DONE,
    MARK_NOT_ON_CARD,
    apply_mark,
    debrief,
    load_practice,
    open_stage,
    save_practice,
    scenarios,
)
from src.modules.who_checklist.panel import WhoPanel

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_later_stages_stay_closed_until_the_open_stage_is_finished():
    card = scenarios()[0]
    marks: dict[str, str] = {}
    assert open_stage(card, marks) == "Sign in"
    marks = apply_mark(card, marks, "procedure", MARK_DONE)
    assert marks == {}
    for item_id in ("identity", "consent", "oximeter", "allergy", "airway", "blood"):
        marks = apply_mark(card, marks, item_id, MARK_DONE)
    assert open_stage(card, marks) == "Time out"
    assert debrief(card, marks).complete is False


def test_a_card_that_does_not_use_imaging_needs_that_item_marked_not_on_the_card():
    card = scenarios()[1]
    imaging = next(item for item in card.items if item.item_id == "imaging")
    specimen = next(item for item in card.items if item.item_id == "specimen")
    assert imaging.required is False
    assert specimen.required is True
    marks = {item.item_id: MARK_DONE if item.required else MARK_NOT_ON_CARD for item in card.items}
    assert debrief(card, marks).complete is True
    marks["imaging"] = MARK_DONE
    assert any(line.startswith("Mark as not on this card") for line in debrief(card, marks).lines)


def test_practice_state_round_trips_through_a_local_file(tmp_path: Path):
    card = scenarios()[0]
    target = tmp_path / "who-practice.json"
    save_practice(card, {"identity": MARK_DONE}, target)
    loaded = load_practice(target)
    assert loaded["scenario_id"] == "FAKE-WHO-1"
    assert loaded["stage"] == "Sign in"
    assert loaded["complete"] is False
    assert loaded["marks"]["identity"] == MARK_DONE


def test_space_marks_the_open_item_and_later_items_are_not_checkable(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("MOVER_CONFIG_DIR", str(tmp_path / "config"))
    panel = WhoPanel()
    panel.show()
    qapp.processEvents()
    first = panel.findChildren(QCheckBox)[0]
    later = next(box for box in panel.findChildren(QCheckBox) if not box.isEnabled())
    first.setFocus()
    QTest.keyClick(first, Qt.Key.Key_Space)
    qapp.processEvents()
    assert panel._marks.get("identity") == MARK_DONE
    assert open_stage(panel._scenario, panel._marks) == "Sign in"
    assert later.isEnabled() is False
    panel.close()
    panel.deleteLater()
    qapp.processEvents()


def test_who_package_does_not_import_case_data():
    forbidden = ("src.services", "src.pipeline", "src.llm", "src.runtime_paths", "data_context", "emr_dir")
    for path in (ROOT / "src" / "modules" / "who_checklist").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            for name in modules:
                assert not any(name == item or name.startswith(item + ".") for item in forbidden), path
