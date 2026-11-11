"""Fictional checklist stages, saved practice, keyboard marks, and EMR isolation."""

from __future__ import annotations

import ast
import os
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
    # The stage pages share a stack, so Qt lists the raised page last: the open line is the first enabled box.
    first = next(box for box in panel.findChildren(QCheckBox) if box.isEnabled())
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


# —— the card, the stages and the lines as the Quiet Record page draws them ——

import re  # noqa: E402

from PySide6.QtCore import QEvent, QPoint  # noqa: E402
from PySide6.QtGui import QAccessible  # noqa: E402
from PySide6.QtWidgets import QLabel  # noqa: E402

from src.desktop.theme import apply_theme, token_color  # noqa: E402
from src.modules.who_checklist import panel as who_panel  # noqa: E402
from src.modules.who_checklist.model import (  # noqa: E402
    CARD_FIELDS,
    ITEM_CARD_FIELD,
    STAGES,
    Scenario,
    card_lines,
    stage_progress,
)
from src.modules.who_checklist.panel import NOTICE, SAVE_FAILED  # noqa: E402

CARD_A, CARD_B = scenarios()


@pytest.fixture
def who(qapp, monkeypatch, tmp_path):
    apply_theme(qapp, "light")
    monkeypatch.setenv("MOVER_CONFIG_DIR", str(tmp_path / "config"))
    page = WhoPanel()
    page.resize(1300, 820)
    page.show()
    for _ in range(3):
        qapp.processEvents()
    yield page
    page.close()
    page.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def _boxes(page, stage: str):
    return page._pages[stage].boxes


def _tick(page, stage: str, count: int | None = None) -> None:
    for box in _boxes(page, stage)[:count]:
        box.click()


def _tabs(page) -> list[str]:
    return [page.stage_tabs.tabText(index) for index in range(page.stage_tabs.count())]


def test_a_cards_lines_are_read_from_its_briefing_in_order():
    for card, expected in (
        (CARD_A, {"Site": "left knee", "Imaging": "imaging displayed", "Specimen": "no specimen",
                  "Recovery": "recovery note is to watch the dressing"}),
        (CARD_B, {"Site": "right forearm", "Allergy": "allergy listed as fictional latex",
                  "Imaging": "imaging is not used", "Specimen": "a fictional tissue sample is listed"}),
    ):
        lines = card_lines(card)
        assert [name for name, _ in lines] == list(CARD_FIELDS) and len(lines) == 14
        assert {name: said for name, said in lines if name in expected} == expected
    assert card_lines(Scenario("X", "x", "A sentence without the lines.", ())) == ()
    assert card_lines(Scenario("X", "x", "Fiction only. Card X says: one, two.", ())) == ()


def test_every_check_item_names_the_card_line_it_reads():
    for card in (CARD_A, CARD_B):
        for item in card.items:
            assert ITEM_CARD_FIELD[item.item_id] in CARD_FIELDS, item.item_id


def test_stage_progress_counts_only_marks_that_fit_the_line():
    assert stage_progress(CARD_A, {}) == (("Sign in", 0, 6), ("Time out", 0, 5), ("Sign out", 0, 5))
    marks = {"identity": MARK_DONE, "consent": MARK_DONE, "team": MARK_DONE, "specimen": MARK_NOT_ON_CARD}
    assert stage_progress(CARD_A, marks) == (("Sign in", 2, 6), ("Time out", 1, 5), ("Sign out", 1, 5))
    # Card B uses no imaging, so that line counts when it is marked not on the card, and not when it is ticked done.
    assert stage_progress(CARD_B, {"imaging": MARK_NOT_ON_CARD})[1] == ("Time out", 1, 5)
    assert stage_progress(CARD_B, {"imaging": MARK_DONE})[1] == ("Time out", 0, 5)
    assert stage_progress(CARD_B, {"specimen": MARK_DONE})[2] == ("Sign out", 1, 5)


def test_the_card_shows_its_lines_and_follows_the_picker(who):
    keys = [label.text() for label in who.card.findChildren(QLabel) if label.objectName() == "cardKey"]
    values = [label.text() for label in who.card.findChildren(QLabel) if label.objectName() == "cardValue"]
    assert keys == list(CARD_FIELDS) and values[0] == "left knee"
    assert who.card_title.text() == "Card FAKE-WHO-1"
    assert who.card.accessibleName() == "Fictional card FAKE-WHO-1"
    assert who.card.accessibleDescription() == CARD_A.briefing
    who.scenario_box.setCurrentIndex(1)
    assert who.card_title.text() == "Card FAKE-WHO-2"
    assert [label.text() for label in who.card.findChildren(QLabel) if label.objectName() == "cardValue"][0] == "right forearm"
    assert not who.briefing.isVisibleTo(who)


def test_a_card_whose_lines_cannot_be_read_shows_its_sentence_instead(who):
    odd = Scenario("FAKE-WHO-9", "Odd", "Fiction only. A card nobody could split.", CARD_A.items)
    who._show_card(odd)
    assert who.briefing.isVisibleTo(who) and who.briefing.text() == odd.briefing
    assert not any(label.objectName() == "cardKey" for label in who.card.findChildren(QLabel))


def test_each_stage_says_in_words_how_many_of_its_lines_are_marked(who):
    assert _tabs(who) == ["Sign in 0 of 6", "Time out 0 of 5", "Sign out 0 of 5"]
    assert who.stage_tabs.accessibleName() == "Checklist stages"
    _boxes(who, "Sign in")[0].click()
    _boxes(who, "Sign in")[1].click()
    assert _tabs(who)[0] == "Sign in 2 of 6"
    assert who.stage_label.text() == "Current stage: Sign in"
    assert who.debrief_label.text() == "Still open in Sign in: oximeter, allergy, airway, blood loss."


def test_the_lines_keep_the_models_wording_and_order(who):
    for card_index, card in enumerate((CARD_A, CARD_B)):
        who.scenario_box.setCurrentIndex(card_index)
        for stage in STAGES:
            expected = [item.prompt if item.required else f"Not on this card: {item.prompt}"
                        for item in card.items if item.stage == stage]
            assert [box.text() for box in _boxes(who, stage)] == expected
    assert sum(len(_boxes(who, stage)) for stage in STAGES) == 16


def test_every_line_is_a_real_check_box_named_by_its_text(who):
    for stage in STAGES:
        for box in _boxes(who, stage):
            assert isinstance(box, who_panel.QCheckBox)
            assert box.accessibleName() == box.text()
            assert box.focusPolicy() == Qt.FocusPolicy.StrongFocus
    box = _boxes(who, "Sign in")[0]
    interface = QAccessible.queryAccessibleInterface(box)
    assert interface.role() == QAccessible.Role.CheckBox
    assert interface.text(QAccessible.Text.Name) == box.text()
    assert not interface.state().checked
    box.click()
    assert QAccessible.queryAccessibleInterface(box).state().checked


def test_a_ticked_line_a_focused_line_and_a_closed_line_each_look_different(who, qapp):
    box, closed = _boxes(who, "Sign in")[0], _boxes(who, "Time out")[0]
    plain = box.grab().toImage()
    box.click()
    ticked = box.grab().toImage()
    assert ticked != plain
    assert ticked.pixelColor(12, box.height() // 2 - 6) == token_color("ink_2"), "a done line is a filled box"
    box.setFocus()
    qapp.processEvents()
    assert box.grab().toImage().pixelColor(1, box.height() // 2) == token_color("accent"), "the focus ring is the accent"
    assert not closed.isEnabled()


def test_finishing_a_stage_opens_the_next_and_moves_the_keyboard_on(who, qapp):
    _tick(who, "Sign in")
    assert _tabs(who)[0] == "Sign in 6 of 6"
    assert who.stage_tabs.currentIndex() == 1 and who.stack.currentIndex() == 1
    assert who.stage_label.text() == "Current stage: Time out"
    assert qapp.focusWidget() is _boxes(who, "Time out")[0]
    assert all(box.isEnabled() for box in _boxes(who, "Time out"))
    assert not any(box.isEnabled() for box in _boxes(who, "Sign in")), "a finished stage is closed again"


def test_a_closed_stage_can_be_read_but_not_ticked(who):
    who.stage_tabs.setCurrentIndex(2)
    assert who.stage_label.text() == "Sign out is closed until Sign in is complete."
    assert not any(box.isEnabled() for box in _boxes(who, "Sign out"))
    _boxes(who, "Sign out")[0].click()
    assert who._marks == {}
    _tick(who, "Sign in")
    who.stage_tabs.setCurrentIndex(0)
    assert who.stage_label.text() == "Sign in is complete."


def test_arrow_keys_move_along_the_grid_of_lines_and_stop_at_its_edges(who, qapp):
    boxes = _boxes(who, "Sign in")
    boxes[0].setFocus()
    for key, expected in ((Qt.Key.Key_Right, 1), (Qt.Key.Key_Down, 3), (Qt.Key.Key_Left, 2), (Qt.Key.Key_Up, 0),
                          (Qt.Key.Key_Up, 0), (Qt.Key.Key_Left, 0)):
        QTest.keyClick(qapp.focusWidget(), key)
        assert qapp.focusWidget() is boxes[expected], key
    who.resize(420, 900)
    for _ in range(4):
        qapp.processEvents()
    boxes[0].setFocus()
    QTest.keyClick(boxes[0], Qt.Key.Key_Down)
    assert qapp.focusWidget() is boxes[1], "one column: the next line is below"


def test_tab_follows_the_screen_from_the_picker_through_the_lines_to_reset_and_save(who, qapp):
    who.scenario_box.setFocus()
    seen = []
    for _ in range(9):
        QTest.keyClick(qapp.focusWidget(), Qt.Key.Key_Tab)
        seen.append(qapp.focusWidget())
    assert seen[0] is who.stage_tabs
    assert seen[1:7] == _boxes(who, "Sign in")
    assert seen[7] is who.reset_button and seen[8] is who.save_button


def test_reset_card_clears_every_mark_and_returns_to_the_first_stage(who, qapp):
    _tick(who, "Sign in")
    _tick(who, "Time out", 2)
    assert who.stage_tabs.currentIndex() == 1
    who.reset_button.click()
    assert who._marks == {} and who.stage_tabs.currentIndex() == 0
    assert _tabs(who) == ["Sign in 0 of 6", "Time out 0 of 5", "Sign out 0 of 5"]
    assert not any(box.isChecked() for stage in STAGES for box in _boxes(who, stage))
    assert who.debrief_label.text().startswith("Still open in Sign in:")


def test_the_still_open_line_names_a_line_the_card_leaves_out(who):
    who.scenario_box.setCurrentIndex(1)
    _tick(who, "Sign in")
    for box in _boxes(who, "Time out")[:4]:
        box.click()
    assert who.debrief_label.text() == "Still open in Time out: imaging (mark as not on this card)."
    imaging = _boxes(who, "Time out")[4]
    assert imaging.text().startswith("Not on this card: ")
    imaging.click()
    assert who._marks["imaging"] == MARK_NOT_ON_CARD
    assert who.stage_tabs.currentIndex() == 2


def test_a_finished_card_says_so_and_save_keeps_the_practice_on_this_computer(who, tmp_path):
    who.save_button.click()
    assert who.debrief_label.text() == "Saved. This card is still open."
    for stage in STAGES:
        _tick(who, stage)
    assert who.debrief_label.text() == "Card complete. Save keeps this practice on this computer."
    assert who.save_button.hasFocus()
    who.save_button.click()
    assert who.debrief_label.text() == "Saved. This card is complete."
    saved = load_practice()
    assert saved["scenario_id"] == "FAKE-WHO-1" and saved["complete"] is True and saved["stage"] is None
    assert saved["marks"]["specimen"] == MARK_NOT_ON_CARD and len(saved["marks"]) == 16
    assert str(tmp_path / "config") in str(Path(os.environ["MOVER_CONFIG_DIR"]))


def test_a_save_that_cannot_be_written_says_so_in_a_plain_sentence(who, monkeypatch):
    def refuse(*_args, **_kwargs):
        raise OSError("read-only")

    monkeypatch.setattr(who_panel, "save_practice", refuse)
    _boxes(who, "Sign in")[0].click()
    who.save_button.click()
    assert who.debrief_label.text() == SAVE_FAILED
    assert who._marks == {"identity": MARK_DONE}, "nothing is lost when the file cannot be written"


def test_picking_another_card_starts_it_clean(who):
    _tick(who, "Sign in", 3)
    who.scenario_box.setCurrentIndex(1)
    assert who._marks == {} and _tabs(who)[0] == "Sign in 0 of 6" and who.stage_tabs.currentIndex() == 0


def test_the_page_says_it_is_invented_and_gives_no_advice(who):
    texts = " ".join(label.text() for label in who.findChildren(QLabel))
    assert NOTICE in texts and "does not tell you what to do for a patient" in texts
    assert not re.search(r"\b(recommend|should|consider|treat)", texts, re.I)


@pytest.mark.parametrize("width", [276, 340, 420, 480, 600, 700, 860, 1100, 1600])
def test_the_page_never_scrolls_sideways_and_no_line_is_cut_off(who, qapp, width):
    who.resize(width, 900)
    for _ in range(5):
        qapp.processEvents()
    assert who.page_scroll.horizontalScrollBar().maximum() == 0
    body = who.page_scroll.widget()
    assert body.width() <= who.page_scroll.viewport().width()

    def inside(widget) -> bool:
        return widget.mapTo(body, QPoint(widget.width(), 0)).x() <= body.width() + 1

    for widget in (who.scenario_box, who.card, who.notice, who.stage_tabs, who.stage_label, who.instruction,
                   who.debrief_label, who.reset_button, who.save_button):
        assert inside(widget), (width, widget.objectName() or type(widget).__name__)
    for box in _boxes(who, "Sign in"):
        assert inside(box)
        assert box.height() >= box.heightForWidth(box.width()), "the wrapped text has all the room it needs"


def test_the_lines_run_in_two_columns_when_there_is_room_and_one_when_not(who, qapp):
    first, second = _boxes(who, "Sign in")[:2]
    assert second.mapTo(who, QPoint(0, 0)).x() > first.mapTo(who, QPoint(0, 0)).x() + 200
    assert first.mapTo(who, QPoint(0, 0)).x() > who.card.mapTo(who, QPoint(0, 0)).x() + 340, "the card is beside"
    who.resize(500, 900)
    for _ in range(4):
        qapp.processEvents()
    assert second.mapTo(who, QPoint(0, 0)).y() > first.mapTo(who, QPoint(0, 0)).y() + 40
    assert who.stage_tabs.mapTo(who, QPoint(0, 0)).y() > who.card.mapTo(who, QPoint(0, 0)).y(), "the card is above"


def test_the_stage_names_fit_in_the_windows_least_width_with_their_counts_beneath(who, qapp):
    who.resize(276, 900)
    for _ in range(5):
        qapp.processEvents()
    assert who.stage_tabs.tabText(0) == "Sign in\n0 of 6"
    assert who.stage_tabs.width() <= who.page_scroll.viewport().width()
    who.resize(1300, 900)
    for _ in range(5):
        qapp.processEvents()
    assert who.stage_tabs.tabText(0) == "Sign in 0 of 6"


def test_the_who_package_has_no_colour_of_its_own():
    """Every colour comes from the design tokens, so both themes and the contrast checks cover it."""
    for path in (ROOT / "src" / "modules" / "who_checklist").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not re.search(r"#[0-9A-Fa-f]{3,8}\b", node.value), (path.name, node.value)
                assert not re.search(r"rgba?\(", node.value), (path.name, node.value)
        assert not re.search(r"QColor\(\s*[\"']#", source), path.name
        assert not re.search(r"QColor\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*\)", source), path.name
        assert "Qt.GlobalColor" not in source, path.name
