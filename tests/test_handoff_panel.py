"""The Handoff practice page: five boxes, the timeline chart and table, feedback, saving and history."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import matplotlib.text
import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QAccessible, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox

from src.desktop.marks import MISSING_ROLE
from src.desktop.theme import apply_theme
from src.modules.handoff_trainer.cases import practice_cases
from src.modules.handoff_trainer.feedback import LEAD_INS, NOTHING_NEEDED, FeedbackMark, build_feedback
from src.modules.handoff_trainer.grading import Dose, PracticeCase, Sample, detect, grade
from src.modules.handoff_trainer.history import history_path, load_attempts
from src.modules.handoff_trainer.history_view import EMPTY, HistoryDialog, history_rows
from src.modules.handoff_trainer.panel import EMPTY_HANDOFF, NOT_SAVED, SAVED, HandoffPanel
from src.modules.handoff_trainer.sections import SECTIONS
from src.modules.handoff_trainer.timeline_chart import (
    TRACKS,
    build_timeline_figure,
    chart_description,
    readings,
    render_timeline,
)
from src.modules.handoff_trainer.timeline_table import NO_ROWS, TimelineTable, table_rows

ROOT = Path(__file__).resolve().parents[1]
KEYS = [key for key, _ in SECTIONS]
CASES = {case.case_id: case for case in practice_cases()}


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    # Applying a theme restyles every live widget, so it is done once and a test that changes it puts it back.
    apply_theme(app, "light")
    return app


@pytest.fixture
def panel(qapp, monkeypatch, tmp_path):
    monkeypatch.setenv("MOVER_CONFIG_DIR", str(tmp_path / "config"))
    page = HandoffPanel()
    page.resize(1300, 820)
    page.show()
    qapp.processEvents()
    page.timeline_chart.redraw()
    yield page
    page.close()
    page.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


@pytest.fixture
def make_panel(qapp, monkeypatch, tmp_path):
    """Builds pages after the test has put a history file in place, and disposes of every one of them."""
    monkeypatch.setenv("MOVER_CONFIG_DIR", str(tmp_path / "config"))
    made: list[HandoffPanel] = []

    def build(width: int = 1300, height: int = 820) -> HandoffPanel:
        page = HandoffPanel()
        page.resize(width, height)
        page.show()
        qapp.processEvents()
        page.timeline_chart.redraw()
        made.append(page)
        return page

    yield build
    qapp.processEvents()
    for page in made:
        page.close()
        page.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def _pick(panel, case_id: str) -> None:
    panel.case_box.setCurrentIndex(panel.case_box.findData(case_id))


def _write(panel, **sections: str) -> None:
    for key, text in sections.items():
        panel.sections[key].setPlainText(text)


def _lines(panel) -> list[str]:
    return [label.accessibleName() for label in panel.feedback.text_labels]


def _texts(fig) -> set[str]:
    return {item.get_text() for item in fig.findobj(matplotlib.text.Text) if item.get_text()}


def _axes(fig, label: str):
    return next(ax for ax in fig.axes if ax.get_label() == label)


# —— the page ——


def test_the_page_has_the_five_labelled_boxes_in_the_ipass_order(panel):
    assert list(panel.sections) == KEYS
    names = [box.accessibleName() for box in panel.sections.values()]
    assert names == [
        "Illness severity",
        "Patient summary",
        "Action list",
        "Situation awareness and contingency plans",
        "Synthesis by receiver",
    ]
    assert all(box.isVisibleTo(panel) for box in panel.sections.values())
    assert all(box.tabChangesFocus() for box in panel.sections.values())


def test_the_page_says_what_it_is_and_nothing_about_treatment(panel):
    from PySide6.QtWidgets import QLabel

    texts = [label.text() for label in panel.findChildren(QLabel)]
    assert "Your handoff" in texts
    assert "Name the finding and one concrete detail." in texts
    assert "Saved on this computer only" in texts
    assert panel.check_button.text() == "Check against timeline"
    assert panel.title_label.text() == "Fictional arterial pressure run"
    assert panel.case_line.text() == "FAKE-ART-LOW · invented timeline"
    # The running head already says the page is fictional practice; the page does not repeat it.
    assert not any("Fictional practice" in text for text in texts)


def test_tab_moves_from_box_to_box_and_out_without_a_tab_character(panel, qapp):
    boxes = list(panel.sections.values())
    for here, there in zip(boxes, boxes[1:], strict=False):
        here.setFocus()
        QTest.keyClick(here, Qt.Key.Key_Tab)
        assert qapp.focusWidget() is there
    boxes[-1].setFocus()
    QTest.keyClick(boxes[-1], Qt.Key.Key_Tab)
    assert qapp.focusWidget() is panel.check_button
    assert all("\t" not in box.toPlainText() for box in boxes)


def test_a_box_grows_with_its_text_up_to_a_limit_and_shrinks_back(panel, qapp):
    box = panel.sections["action_list"]
    empty = box.height()
    box.setPlainText("one\ntwo\nthree\nfour")
    qapp.processEvents()
    grown = box.height()
    assert grown > empty
    box.setPlainText("\n".join(str(n) for n in range(60)))
    qapp.processEvents()
    limit = box.height()
    assert limit > grown
    box.setPlainText("\n".join(str(n) for n in range(120)))
    qapp.processEvents()
    assert box.height() == limit
    box.clear()
    qapp.processEvents()
    assert box.height() == empty


def test_focus_order_follows_the_screen(panel, qapp):
    _pick(panel, "FAKE-ART-LOW")
    _write(panel, patient_summary="Hypotension from minute 10 to 16", action_list="Phenylephrine at minute 30")
    panel.check_button.click()
    qapp.processEvents()
    expected = [
        panel.case_box, panel.timeline_chart, panel.table_toggle, *panel.sections.values(), panel.check_button,
        *panel.feedback.show_buttons, panel.try_again_button, panel.next_button, panel.history_button,
    ]
    assert len(panel.feedback.show_buttons) == 3
    panel.case_box.setFocus()
    seen = [qapp.focusWidget()]
    for _ in range(len(expected) - 1):
        QTest.keyClick(qapp.focusWidget(), Qt.Key.Key_Tab)
        seen.append(qapp.focusWidget())
    assert seen == expected


def test_the_picker_announces_its_purpose_and_the_history_line_reads_out(panel):
    combo = QAccessible.queryAccessibleInterface(panel.case_box)
    assert combo.text(QAccessible.Text.Name) == "Fictional scenario"
    assert combo.text(QAccessible.Text.Value) == panel.case_box.currentText()
    line = QAccessible.queryAccessibleInterface(panel.history_label).text(QAccessible.Text.Name)
    assert line == panel.history_label.text() == "Saved attempts: 0 for this run · 0 in all"


# —— checking and saving ——


def test_checking_an_empty_handoff_saves_nothing_and_says_so(panel):
    panel.check_button.click()
    assert panel.status.text() == EMPTY_HANDOFF == "Write at least one section first."
    assert not history_path().exists()
    assert not panel.feedback.isVisibleTo(panel)
    _write(panel, illness_severity="   \n  ", synthesis="\t")
    panel.check_button.click()
    assert panel.status.text() == EMPTY_HANDOFF
    assert not history_path().exists()
    assert panel.history_label.text() == "Saved attempts: 0 for this run · 0 in all"


def test_a_check_saves_the_sections_apart_and_joined_and_says_so(panel):
    _write(panel, patient_summary="Hypotension from minute 10 to 16", synthesis="Receiver repeated it back")
    panel.check_button.click()
    assert panel.status.text() == SAVED == "Checked and saved."
    (row,) = load_attempts()
    assert row["case_id"] == "FAKE-ART-LOW"
    assert row["sections"] == {
        "illness_severity": "",
        "patient_summary": "Hypotension from minute 10 to 16",
        "action_list": "",
        "situation_awareness": "",
        "synthesis": "Receiver repeated it back",
    }
    assert row["handoff"] == panel.handoff_text()
    assert row["handoff"].startswith("Patient summary: Hypotension from minute 10 to 16.")
    assert row["grades"] == [
        {"status": item.status, "kind": item.kind, "evidence": item.evidence}
        for item in grade(CASES["FAKE-ART-LOW"], row["handoff"])
    ]
    assert history_path().parent.name == "config"
    assert panel.history_label.text() == "Saved attempts: 1 for this run · 1 in all"


def test_the_saved_counts_are_per_run_and_in_all(panel):
    _write(panel, patient_summary="Hypotension from minute 10 to 16")
    panel.check_button.click()
    panel.check_button.click()
    _pick(panel, "FAKE-DESAT")
    assert panel.history_label.text() == "Saved attempts: 0 for this run · 2 in all"
    _write(panel, patient_summary="Desaturation from minute 20 to 21")
    panel.check_button.click()
    assert panel.history_label.text() == "Saved attempts: 1 for this run · 3 in all"
    _pick(panel, "FAKE-ART-LOW")
    assert panel.history_label.text() == "Saved attempts: 2 for this run · 3 in all"


def test_a_failed_save_is_not_reported_as_saved(panel, monkeypatch):
    def refuse(*_args, **_kwargs):
        raise OSError("read-only")

    monkeypatch.setattr("src.modules.handoff_trainer.panel.record_attempt", refuse)
    _write(panel, patient_summary="Hypotension from minute 10 to 16")
    panel.check_button.click()
    assert panel.status.text() == NOT_SAVED
    assert panel.feedback.isVisibleTo(panel)


def test_attempts_saved_before_the_sections_existed_still_count_and_list(panel):
    old = {
        "at": "2026-11-01T10:00:00Z",
        "case_id": "FAKE-ART-LOW",
        "handoff": "hypotension at minute 12",
        "grades": [{"status": "mentioned", "kind": "hypotension", "evidence": "e"},
                   {"status": "missed", "kind": "vasopressor", "evidence": "e"}],
    }
    history_path().parent.mkdir(parents=True, exist_ok=True)
    history_path().write_text(json.dumps(old) + "\n", encoding="utf-8")
    _pick(panel, "FAKE-DESAT")
    _pick(panel, "FAKE-ART-LOW")
    assert panel.history_label.text() == "Saved attempts: 1 for this run · 1 in all"
    panel.show_history()
    assert [panel.history_dialog.table.item(0, c).text() for c in (1, 2)] == [
        "Fictional arterial pressure run (FAKE-ART-LOW)", "1 of 2"]
    panel.history_dialog.reject()


# —— feedback ——


def test_feedback_lines_use_plain_words_a_mark_and_the_graders_evidence(panel):
    _write(panel, patient_summary="Hypotension from minute 10 to 16")
    panel.check_button.click()
    assert panel.feedback.summary.text() == "1 of 2 found"
    assert _lines(panel) == [
        "Matches the timeline. Arterial MAP stayed under 65 mmHg from minute 10 to minute 16.",
        "Left out. Phenylephrine 100 mcg at minute 12.",
    ]
    assert panel.feedback.heading.text() == "Feedback on the text"
    assert [mark.status for mark in panel.feedback.marks] == ["mentioned", "missed"]
    assert LEAD_INS == {"mentioned": "Matches the timeline.", "unsupported": "Not on the timeline.",
                        "missed": "Left out."}


def test_a_claim_the_timeline_does_not_show_reads_not_on_the_timeline(panel):
    _write(panel, patient_summary="Hypotension from minute 10 to 16", action_list="Phenylephrine 100 mcg at minute 30")
    panel.check_button.click()
    lines = _lines(panel)
    assert lines[0].startswith("Matches the timeline. Arterial MAP")
    assert lines[1].startswith("Not on the timeline. The handoff gives Phenylephrine a minute this timeline does not show.")
    assert lines[2] == "Left out. Phenylephrine 100 mcg at minute 12."
    assert panel.feedback.summary.text() == "1 of 2 found"


def test_the_raw_status_words_are_never_shown(panel, qapp):
    from PySide6.QtWidgets import QLabel

    for case_id in CASES:
        _pick(panel, case_id)
        _write(panel, patient_summary="Hypotension and desaturation, no phenylephrine", action_list="Vasopressin given")
        panel.check_button.click()
        shown = " ".join(label.text() for label in panel.feedback.findChildren(QLabel)).lower()
        assert not re.search(r"\b(mentioned|unsupported|missed)\b", shown), case_id


def test_the_three_kinds_differ_by_mark_and_not_by_colour_alone(qapp):
    shapes = {}
    for status in ("mentioned", "unsupported", "missed"):
        mark = FeedbackMark(status)
        mark.show()
        shapes[status] = _signature(mark.grab().toImage())
        assert any(shapes[status]), status
    assert len(set(shapes.values())) == 3


def _signature(image: QImage) -> tuple[int, ...]:
    return tuple(image.pixelColor(x, y).lightness() < 128 for y in range(image.height()) for x in range(image.width()))


def test_every_item_with_minutes_has_a_show_link_that_marks_them_on_the_chart_and_table(panel, qapp):
    _write(panel, patient_summary="Hypotension from minute 10 to 16")
    panel.check_button.click()
    assert [b.accessibleName() for b in panel.feedback.show_buttons] == [
        "Show minutes 10 to 16 on the timeline", "Show minute 12 on the timeline"]
    panel.feedback.show_buttons[0].click()
    assert panel.timeline_chart.highlight() == (10, 16)
    assert panel.timeline_table.selected_minutes() == list(range(10, 17))
    panel.feedback.show_buttons[1].click()
    assert panel.timeline_chart.highlight() == (12, 12)
    assert panel.timeline_table.selected_minutes() == [12]


def test_an_item_with_no_minutes_has_no_show_link(panel):
    _pick(panel, "FAKE-NO-SPO2")
    _write(panel, patient_summary="Hypotension throughout")
    panel.check_button.click()
    lines = _lines(panel)
    assert lines == [
        "Left out. No usable SpO2 reading is on this timeline.",
        "Not on the timeline. The handoff says hypotension happened. This timeline does not.",
    ]
    assert panel.feedback.show_buttons == []


def test_a_quiet_timeline_says_nothing_needs_a_handoff_line(panel):
    _pick(panel, "FAKE-QUIET")
    _write(panel, patient_summary="Stable throughout")
    panel.check_button.click()
    assert panel.feedback.summary.text() == NOTHING_NEEDED == "Nothing on this timeline needs a handoff line."
    assert _lines(panel) == []
    assert panel.status.text() == SAVED


def test_feedback_finds_each_graded_events_minutes_by_the_events_own_sentence():
    for case in CASES.values():
        events = {event.evidence: event for event in detect(case)}
        text = "Hypotension from minute 10 to 16. Desaturation at minute 20. Phenylephrine given. Vasopressin given."
        for line in build_feedback(case, grade(case, text)).lines:
            owner = next((event for evidence, event in events.items() if line.evidence.endswith(evidence)), None)
            if owner is None or owner.start_minute is None:
                assert line.minutes is None, (case.case_id, line.text)
            else:
                assert line.minutes == (owner.start_minute, owner.end_minute), (case.case_id, line.text)


def test_try_again_clears_the_feedback_and_keeps_the_text(panel):
    _write(panel, patient_summary="Hypotension from minute 10 to 16")
    panel.check_button.click()
    panel.feedback.show_buttons[0].click()
    assert panel.try_again_button.isEnabled()
    panel.try_again_button.click()
    assert not panel.feedback.isVisibleTo(panel)
    assert panel.timeline_chart.highlight() is None and panel.timeline_table.selected_minutes() == []
    assert panel.sections["patient_summary"].toPlainText() == "Hypotension from minute 10 to 16"
    assert not panel.try_again_button.isEnabled()
    assert panel.status.text() == ""


def test_next_fictional_run_moves_on_clears_the_boxes_and_wraps_after_the_last(panel):
    ids = [case.case_id for case in practice_cases()]
    _write(panel, patient_summary="Hypotension from minute 10 to 16")
    panel.check_button.click()
    panel.next_button.click()
    assert panel.case_box.currentData() == ids[1]
    assert all(box.toPlainText() == "" for box in panel.sections.values())
    assert not panel.feedback.isVisibleTo(panel)
    assert panel.title_label.text() == CASES[ids[1]].title
    for _ in range(len(ids) - 2):
        panel.next_button.click()
    assert panel.case_box.currentData() == ids[-1]
    panel.next_button.click()
    assert panel.case_box.currentData() == ids[0]


def test_changing_the_run_clears_feedback_and_keeps_the_text(panel):
    _write(panel, patient_summary="Hypotension from minute 10 to 16")
    panel.check_button.click()
    _pick(panel, "FAKE-DESAT")
    assert not panel.feedback.isVisibleTo(panel)
    assert panel.sections["patient_summary"].toPlainText() == "Hypotension from minute 10 to 16"
    assert panel.timeline_chart.highlight() is None


# —— chart and table ——


def test_show_as_table_swaps_the_chart_for_the_rows_and_back(panel):
    assert panel.view_stack.currentWidget() is panel.timeline_chart
    assert panel.table_toggle.text() == "Show as table"
    assert panel.table_note.text() == "The same timeline minute by minute, for screen readers and close reading."
    panel.table_toggle.click()
    assert panel.view_stack.currentWidget() is panel.timeline_table
    assert panel.table_toggle.text() == "Show as chart"
    assert panel.timeline_table.rowCount() == 21
    panel.table_toggle.click()
    assert panel.view_stack.currentWidget() is panel.timeline_chart
    assert panel.table_toggle.text() == "Show as table"


def test_the_table_has_the_same_numbers_and_hatches_what_is_missing(panel):
    table = panel.timeline_table
    assert [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())] == [
        "Minute", "Arterial MAP mmHg", "Noninvasive MAP mmHg", "SpO₂ %", "Drug doses"]
    row = [table.item(12, c).text() for c in range(5)]
    assert row == ["12", "60", "missing", "98", "Phenylephrine 100 mcg at minute 12 (bolus)"]
    assert table.item(12, 2).data(MISSING_ROLE) is True
    assert table.item(12, 1).data(MISSING_ROLE) is not True
    assert not table.tabKeyNavigation()
    _pick(panel, "FAKE-CUFF")
    assert [table.item(0, c).text() for c in range(1, 4)] == ["missing", "60", "98"]
    assert [table.item(1, c).text() for c in range(1, 4)] == ["missing", "58", "97"]
    assert table.rowCount() == 2


def test_a_dose_minute_no_reading_covers_still_has_its_own_row_and_the_nasal_dose_is_listed():
    case = PracticeCase("FAKE-T", "t", (Sample(0, map_art=80),), (Dose("Norepinephrine", 5, end_minute=9, amount=2, unit="mcg"),))
    rows = table_rows(case)
    assert [(row.minute, row.values, row.doses) for row in rows] == [
        (0, (80.0, None, None), ()),
        (5, (None, None, None), ("Norepinephrine 2 mcg from minute 5 to minute 9 (infusion)",)),
    ]
    nasal = table_rows(CASES["FAKE-NASAL"])
    assert nasal[3].doses == ("Phenylephrine nasal 2 drops at minute 3 (bolus)",)


def test_an_empty_timeline_gives_one_explaining_row(qapp):
    table = TimelineTable()
    table.set_case(PracticeCase("FAKE-E", "e", (), ()))
    assert table.rowCount() == 1 and table.item(0, 0).text() == NO_ROWS


def test_the_chart_has_a_name_and_a_description_in_words_that_names_no_finding(panel):
    chart = panel.timeline_chart
    assert chart.accessibleName() == "Invented timeline chart"
    description = chart.accessibleDescription()
    assert description == chart_description(CASES["FAKE-ART-LOW"])
    for part in ("minutes 0 to 20", "Arterial MAP (mmHg): 21 readings from minute 0 to minute 20, lowest 60, highest 85.",
                 "Noninvasive MAP: no samples.", "SpO₂ (%): 21 readings", "Phenylephrine 100 mcg at minute 12 (bolus)"):
        assert part in description
    assert not re.search(r"hypotensi|desaturat|missed|mentioned", description, re.I)
    panel.timeline_chart.set_highlight((10, 16))
    assert panel.timeline_chart.accessibleDescription().endswith("Highlighted: minutes 10 to 16.")


def test_the_chart_draws_each_track_with_its_name_unit_and_the_axis_label():
    fig, boxes, xlim = build_timeline_figure(CASES["FAKE-ART-LOW"], 900, "light")
    texts = _texts(fig)
    for word in ("Arterial MAP", "Noninvasive MAP", "SpO₂", "mmHg", "%", "invented minute", "Drug doses",
                 "Phenylephrine 100 mcg", "no samples"):
        assert word in texts, word
    assert len(boxes) == 4 and xlim == (-0.5, 20.5)
    assert [ax.get_label() for ax in fig.axes] == ["map_art", "nmap", "spo2", "doses"]


def test_a_track_with_no_usable_reading_is_hatched_and_the_others_are_not():
    fig, _, _ = build_timeline_figure(CASES["FAKE-ART-LOW"], 900, "light")
    hatched = {ax.get_label(): [p for p in ax.patches if p.get_hatch()] for ax in fig.axes}
    assert len(hatched["nmap"]) == 1
    patch = hatched["nmap"][0]
    assert patch.get_x() == -0.5 and patch.get_width() == 21
    assert hatched["map_art"] == [] and hatched["spo2"] == [] and hatched["doses"] == []
    quiet, _, _ = build_timeline_figure(CASES["FAKE-QUIET"], 900, "light")
    assert [ax.get_label() for ax in quiet.axes if any(p.get_hatch() for p in ax.patches)] == ["nmap"]


def test_a_reading_outside_the_sensors_range_is_not_drawn_and_the_description_says_so():
    case = PracticeCase("FAKE-A", "a", (Sample(0, map_art=80), Sample(1, map_art=300), Sample(2, map_art=82)), ())
    assert readings(case, TRACKS[0]) == {0: 80.0, 2: 82.0}
    assert "1 reading outside the usable range is not drawn." in chart_description(case)


def test_a_trace_breaks_at_a_missing_minute_and_the_gap_is_hatched_and_labelled():
    samples = tuple(Sample(m, map_art=70, spo2=97) for m in (0, 1, 2, 3, 9, 10, 11, 12))
    fig, _, _ = build_timeline_figure(PracticeCase("FAKE-G", "g", samples, ()), 900, "light")
    arterial = _axes(fig, "map_art")
    ydata = list(arterial.lines[0].get_ydata())
    xdata = list(arterial.lines[0].get_xdata())
    gap = [y for x, y in zip(xdata, ydata, strict=True) if 4 <= x <= 8]
    assert len(gap) == 5 and all(y != y for y in gap), "NaN at the minutes with no reading"
    assert all(y == 70 for x, y in zip(xdata, ydata, strict=True) if x <= 3 or x >= 9)
    spans = [(p.get_x(), p.get_x() + p.get_width()) for p in arterial.patches if p.get_hatch()]
    assert spans == [(3.5, 8.5)]
    assert "no samples" in {t.get_text() for t in arterial.texts}


def test_a_bolus_is_a_mark_and_an_infusion_is_a_bar_over_its_minutes():
    case = PracticeCase(
        "FAKE-D", "d", tuple(Sample(m, map_art=80, spo2=98) for m in range(0, 61)),
        (Dose("Phenylephrine", 12, amount=100, unit="mcg"), Dose("Norepinephrine", 40, end_minute=50, amount=5, unit="mcg")),
    )
    fig, _, _ = build_timeline_figure(case, 900, "light")
    lane = _axes(fig, "doses")
    marks = [line for line in lane.lines if line.get_marker() == "s"]
    assert [list(line.get_xdata()) for line in marks] == [[12]]
    bars = [p for p in lane.patches if p.get_hatch()]
    assert [(p.get_x(), p.get_width()) for p in bars] == [(40, 10)]
    assert {"Phenylephrine 100 mcg", "Norepinephrine 5 mcg"} <= {t.get_text() for t in lane.texts}


def test_a_dose_without_an_amount_says_so_and_the_nasal_dose_is_shown():
    case = PracticeCase("FAKE-D", "d", (Sample(0, map_art=80, spo2=98),), (Dose("Phenylephrine", 0),))
    fig, _, _ = build_timeline_figure(case, 700, "light")
    assert "Phenylephrine dose not recorded" in {t.get_text() for t in _axes(fig, "doses").texts}
    nasal, _, _ = build_timeline_figure(CASES["FAKE-NASAL"], 700, "light")
    assert "Phenylephrine nasal 2 drops" in {t.get_text() for t in _axes(nasal, "doses").texts}
    empty, _, _ = build_timeline_figure(CASES["FAKE-QUIET"], 700, "light")
    assert "No drug doses on this timeline" in {t.get_text() for t in _axes(empty, "doses").texts}


def test_overlapping_dose_labels_go_to_their_own_lane_rows():
    doses = tuple(Dose("Ephedrine", 5 + n, amount=5, unit="mg") for n in range(3))
    case = PracticeCase("FAKE-O", "o", tuple(Sample(m, map_art=80, spo2=98) for m in range(0, 21)), doses)
    fig, boxes, _ = build_timeline_figure(case, 700, "light")
    lane = _axes(fig, "doses")
    rows = sorted({round(t.get_position()[1], 2) for t in lane.texts if t.get_text().startswith("Ephedrine")})
    assert len(rows) == 3
    assert boxes[-1].height() == 3 * 26
    # Labels that do not overlap share a row, so the lane stays one row tall.
    apart = PracticeCase("FAKE-O", "o", case.samples, (Dose("Ephedrine", 2, amount=5, unit="mg"),
                                                       Dose("Ephedrine", 18, amount=5, unit="mg")))
    assert build_timeline_figure(apart, 700, "light")[1][-1].height() == 26


def test_the_chart_is_drawn_from_tokens_in_both_themes_and_the_highlight_is_painted(panel, qapp):
    chart = panel.timeline_chart
    light = chart.grab().toImage()
    assert light.pixelColor(light.width() // 2, light.height() - 4).name().upper() == "#F3F0E8"
    chart.set_highlight((10, 16))
    band = chart.highlight_rect()
    first, lane = chart._axes[0], chart._axes[-1]
    low, high = chart._xlim
    assert band.left() == pytest.approx(first.left() + (9.5 - low) / (high - low) * first.width())
    assert band.right() == pytest.approx(first.left() + (16.5 - low) / (high - low) * first.width())
    assert band.bottom() == pytest.approx(lane.bottom())
    marked = chart.grab().toImage()
    assert marked != light
    chart.set_highlight((12, 12))
    line = chart.highlight_rect()
    assert line.width() == pytest.approx(first.width() / (high - low))
    chart.set_highlight(None)
    assert chart.grab().toImage() == light
    apply_theme(qapp, "dark")
    try:
        qapp.processEvents()
        chart.redraw()
        dark = chart.grab().toImage()
        assert dark.pixelColor(dark.width() // 2, dark.height() - 4).name().upper() == "#131518"
    finally:
        apply_theme(qapp, "light")


def test_render_timeline_height_follows_the_lane_rows():
    one = render_timeline(CASES["FAKE-ART-LOW"], 800, "light")
    assert QImage.fromData(one.png).width() == 800 and QImage.fromData(one.png).height() == one.height
    crowded = PracticeCase("FAKE-O", "o", CASES["FAKE-ART-LOW"].samples,
                           tuple(Dose("Ephedrine", 5 + n, amount=5, unit="mg") for n in range(3)))
    assert render_timeline(crowded, 800, "light").height == one.height + 2 * 26


# —— layout ——


def test_wide_the_handoff_sits_beside_the_timeline_and_narrow_it_goes_under(panel, qapp):
    def at(widget):
        return widget.mapTo(panel, QPoint(0, 0))

    assert at(panel.side).x() > at(panel.timeline_chart).x() + 300
    assert at(panel.side).y() <= at(panel.title_label).y()
    panel.resize(700, 900)
    qapp.processEvents()
    chart_bottom = at(panel.timeline_chart).y() + panel.timeline_chart.height()
    assert at(panel.side).y() >= chart_bottom
    assert panel.page_scroll.horizontalScrollBar().maximum() == 0
    assert panel.side.property("stacked") is True
    assert panel.side.width() >= panel.page_scroll.viewport().width() - 20


@pytest.mark.parametrize("width", [560, 640, 760, 940, 1100, 1600])
def test_the_page_never_scrolls_sideways(panel, qapp, width):
    panel.resize(width, 800)
    qapp.processEvents()
    panel.timeline_chart.redraw()
    assert panel.page_scroll.horizontalScrollBar().maximum() == 0
    body = panel.page_scroll.widget()
    assert body.width() <= panel.page_scroll.viewport().width()


# —— history ——


def test_history_lists_attempts_newest_first_read_only_and_closes_with_escape(panel, qapp):
    _write(panel, patient_summary="Hypotension from minute 10 to 16")
    panel.check_button.click()
    _pick(panel, "FAKE-DESAT")
    _write(panel, patient_summary="Nothing to add")
    panel.check_button.click()
    panel.history_button.click()
    dialog = panel.history_dialog
    assert isinstance(dialog, HistoryDialog) and dialog.isVisible()
    table = dialog.table
    assert [table.horizontalHeaderItem(c).text() for c in range(3)] == ["When", "Run", "Found"]
    assert [[table.item(r, c).text() for c in (1, 2)] for r in range(table.rowCount())] == [
        ["Fictional saturation run (FAKE-DESAT)", "0 of 1"],
        ["Fictional arterial pressure run (FAKE-ART-LOW)", "1 of 2"],
    ]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", table.item(0, 0).text())
    assert table.editTriggers() == table.EditTrigger.NoEditTriggers
    buttons = [b.text() for b in dialog.findChildren(QDialogButtonBox)[0].buttons()]
    assert buttons == ["Close"], "no way to delete an attempt"
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    qapp.processEvents()
    assert panel.history_dialog is None
    # The window's own focus memory, since an offscreen window is never really active.
    assert panel.window().focusWidget() is panel.history_button


def test_history_with_no_attempts_says_so(panel):
    panel.show_history()
    assert panel.history_dialog.empty.text() == EMPTY
    assert not panel.history_dialog.table.isVisibleTo(panel.history_dialog)
    panel.history_dialog.reject()


def test_history_rows_read_an_unknown_run_and_a_bad_stamp_without_failing():
    rows = history_rows([{"at": "yesterday", "case_id": "FAKE-GONE", "grades": []}], {})
    assert rows == [("yesterday", "FAKE-GONE", "0 of 0")]


# —— rules for the package ——


def test_the_handoff_package_has_no_colour_of_its_own():
    """Every colour comes from the design tokens, so both themes and the contrast checks cover it."""
    for path in (ROOT / "src" / "modules" / "handoff_trainer").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not re.search(r"#[0-9A-Fa-f]{3,8}\b", node.value), (path.name, node.value)
                assert not re.search(r"rgba?\(", node.value), (path.name, node.value)
        assert not re.search(r"QColor\(\s*[\"']#", source), path.name
        assert "Qt.GlobalColor" not in source, path.name


# —— review fixes: counts, boxes, links, scrolling, unreadable history ——


def _scrolled_feedback_top(panel, qapp) -> tuple[int, int, int]:
    qapp.processEvents()
    qapp.processEvents()
    bar = panel.page_scroll.verticalScrollBar()
    top = panel.feedback.mapTo(panel.page_scroll.viewport(), QPoint(0, 0)).y()
    return top, bar.value(), bar.maximum()


def test_a_wrong_value_for_a_missing_series_reads_zero_of_one_on_the_page_and_in_history(panel):
    _pick(panel, "FAKE-NO-SPO2")
    _write(panel, patient_summary="SpO2 98")
    panel.check_button.click()
    assert _lines(panel) == [
        "Not on the timeline. The handoff states a value. No usable SpO2 reading is on this timeline."]
    assert panel.feedback.summary.text() == "0 of 1 found"
    panel.show_history()
    assert panel.history_dialog.table.item(0, 2).text() == "0 of 1"
    panel.history_dialog.reject()


def _lines_text(count: int) -> str:
    return "\n".join(f"line {n}" for n in range(count))


@pytest.mark.parametrize("count", [1, 2, 3, 8])
def test_a_box_up_to_eight_lines_shows_every_line_with_no_scroll_bar(panel, qapp, count):
    for key, box in panel.sections.items():
        box.setPlainText(_lines_text(count))
        qapp.processEvents()
        qapp.processEvents()
        bar = box.verticalScrollBar()
        assert bar.maximum() == 0, (key, count)
        assert not bar.isVisible(), (key, count)


def test_a_ninth_line_scrolls_and_the_box_stops_growing(panel, qapp):
    box = panel.sections["patient_summary"]
    box.setPlainText(_lines_text(8))
    qapp.processEvents()
    eight = box.height()
    box.setPlainText(_lines_text(9))
    qapp.processEvents()
    qapp.processEvents()
    assert box.height() == eight
    assert box.verticalScrollBar().maximum() > 0


@pytest.mark.parametrize("pixels", [13, 14, 16, 18, 22])
def test_the_box_fits_its_lines_at_other_text_sizes(panel, qapp, pixels):
    """The document lays a line out at the font's line spacing rounded up; a size that rounds the other way
    would scroll by a line."""
    box = panel.sections["action_list"]
    box.setStyleSheet(f"font-size: {pixels}px;")
    for count in (2, 3, 8):
        box.setPlainText(_lines_text(count))
        qapp.processEvents()
        qapp.processEvents()
        assert box.verticalScrollBar().maximum() == 0, (pixels, count)


def test_two_show_links_for_the_same_minutes_say_which_line_they_belong_to(panel):
    _write(panel, patient_summary="Hypotension from minute 10 to 16", action_list="Phenylephrine 100 mcg at minute 30")
    panel.check_button.click()
    buttons = panel.feedback.show_buttons
    assert [b.accessibleName() for b in buttons] == [
        "Show minutes 10 to 16 on the timeline", "Show minute 12 on the timeline", "Show minute 12 on the timeline"]
    descriptions = [b.accessibleDescription() for b in buttons]
    assert descriptions == [_lines(panel)[0], _lines(panel)[1], _lines(panel)[2]]
    assert len(set(descriptions)) == 3
    assert descriptions[1].startswith("Not on the timeline.") and descriptions[2].startswith("Left out.")


def test_a_check_scrolls_so_the_feedback_block_starts_at_the_top_of_the_view(panel, qapp):
    panel.resize(1000, 420)
    qapp.processEvents()
    _write(panel, patient_summary="Hypotension from minute 10 to 16", action_list="Phenylephrine 100 mcg at minute 30",
           synthesis="Receiver repeated it back")
    panel.check_button.click()
    top, value, maximum = _scrolled_feedback_top(panel, qapp)
    # The block is the last thing on the page, so the view stops at the page's end when the block cannot
    # reach the top; either way the heading, the count and the first lines are all in view.
    assert value > 0
    assert top == 0 or value == maximum, (top, value, maximum)
    assert 0 <= top < panel.page_scroll.viewport().height() - 100


def test_the_feedback_block_is_scrolled_to_again_when_the_page_is_stacked(panel, qapp):
    panel.resize(700, 420)
    qapp.processEvents()
    _write(panel, patient_summary="Hypotension from minute 10 to 16")
    panel.check_button.click()
    top, value, maximum = _scrolled_feedback_top(panel, qapp)
    assert value > 0 and (top == 0 or value == maximum), (top, value, maximum)


def _write_history(*lines: bytes) -> bytes:
    history_path().parent.mkdir(parents=True, exist_ok=True)
    content = b"\n".join(lines) + b"\n"
    history_path().write_bytes(content)
    return content


GOOD_ATTEMPT = json.dumps({"at": "2026-11-05T09:30:00Z", "case_id": "FAKE-ART-LOW", "handoff": "x",
                           "grades": [{"status": "mentioned", "kind": "hypotension", "evidence": "e"}]}).encode()


def test_one_unreadable_history_line_does_not_break_the_page_and_the_page_says_so(qapp, make_panel):
    from src.modules.handoff_trainer.history import UNREADABLE

    content = _write_history(GOOD_ATTEMPT, b"{not json", b"[1]")
    page = make_panel()
    assert page.history_label.text() == "Saved attempts: 1 for this run · 1 in all"
    assert page.history_note.text() == UNREADABLE == "Some saved attempts could not be read."
    assert page.history_note.isVisibleTo(page)
    _write(page, patient_summary="Hypotension from minute 10 to 16")
    page.check_button.click()
    assert page.status.text() == SAVED
    assert page.feedback.isVisibleTo(page)
    assert page.history_label.text() == "Saved attempts: 2 for this run · 2 in all"
    assert history_path().read_bytes().startswith(content), "the unreadable lines are left where they were"
    page.show_history()
    assert page.history_dialog.problem.text() == UNREADABLE and page.history_dialog.problem.isVisibleTo(
        page.history_dialog)
    assert page.history_dialog.table.rowCount() == 2
    page.history_dialog.reject()


def test_a_failed_read_of_the_history_never_stops_the_saved_status_the_announcement_or_the_scroll(
        qapp, monkeypatch, make_panel):
    from src.modules.handoff_trainer.history import READ_FAILED

    spoken: list[str] = []
    monkeypatch.setattr("src.modules.handoff_trainer.panel.announce", lambda _widget, text: spoken.append(text))

    def refuse(*_args, **_kwargs):
        raise PermissionError("no access")

    monkeypatch.setattr("src.modules.handoff_trainer.panel.load_attempts", refuse)
    page = make_panel(1000, 420)
    assert page.history_note.text() == READ_FAILED
    _write(page, patient_summary="Hypotension from minute 10 to 16", action_list="Phenylephrine 100 mcg at minute 30",
           synthesis="Receiver repeated it back")
    page.check_button.click()
    assert page.status.text() == SAVED
    assert spoken[-1].startswith("Checked and saved. 1 of 2 found")
    top, value, _ = _scrolled_feedback_top(page, qapp)
    assert value > 0 and top >= 0
    page.show_history()
    assert page.history_dialog.problem.text() == READ_FAILED
    assert not page.history_dialog.empty.isVisibleTo(page.history_dialog), "no claim that nothing is saved"
    page.history_dialog.reject()


# —— polish: stale feedback, Tab, plurals, the table's unusable readings, names, narrow pages ——


def test_checking_every_box_blank_after_a_check_takes_the_old_feedback_down(panel):
    _write(panel, patient_summary="Hypotension from minute 10 to 16")
    panel.check_button.click()
    panel.feedback.show_buttons[0].click()
    assert panel.feedback.isVisibleTo(panel)
    panel.sections["patient_summary"].clear()
    panel.check_button.click()
    assert panel.status.text() == EMPTY_HANDOFF
    assert not panel.feedback.isVisibleTo(panel)
    assert panel.timeline_chart.highlight() is None and panel.timeline_table.selected_minutes() == []
    assert not panel.try_again_button.isEnabled()
    assert len(load_attempts()) == 1, "nothing new is saved"


def test_after_an_edit_the_feedback_says_it_is_for_the_handoff_as_it_was_when_checked(panel):
    from src.modules.handoff_trainer.feedback import STALE

    box = panel.sections["patient_summary"]
    box.setPlainText("Hypotension from minute 10 to 16")
    assert not panel.feedback.stale.isVisibleTo(panel), "nothing is stale before a check"
    panel.check_button.click()
    assert not panel.feedback.stale.isVisibleTo(panel)
    box.setPlainText("Hypotension from minute 10 to 17")
    assert panel.feedback.isVisibleTo(panel) and panel.feedback.stale.isVisibleTo(panel)
    assert panel.feedback.stale.text() == STALE == "This feedback is for the handoff as it was when you checked it."
    # Typing it back to what was checked makes it current again.
    box.setPlainText("Hypotension from minute 10 to 16")
    assert not panel.feedback.stale.isVisibleTo(panel)
    # An edit in another box counts too, and the next check clears it.
    panel.sections["synthesis"].setPlainText("Receiver repeated it back")
    assert panel.feedback.stale.isVisibleTo(panel)
    panel.check_button.click()
    assert not panel.feedback.stale.isVisibleTo(panel)
    assert panel.feedback.summary.text() == "1 of 2 found"


def test_next_run_and_try_again_leave_no_stale_line_behind(panel):
    _write(panel, patient_summary="Hypotension from minute 10 to 16")
    panel.check_button.click()
    panel.sections["patient_summary"].setPlainText("changed")
    assert panel.feedback.stale.isVisibleTo(panel)
    panel.try_again_button.click()
    assert not panel.feedback.stale.isVisibleTo(panel)
    panel.check_button.click()
    assert not panel.feedback.stale.isVisibleTo(panel) and panel.feedback.isVisibleTo(panel)
    panel.next_button.click()
    assert not panel.feedback.stale.isVisibleTo(panel) and not panel.feedback.isVisibleTo(panel)


def test_tab_after_history_goes_to_the_first_control_and_not_to_an_unnamed_scroll_area(panel, qapp):
    panel.history_button.setFocus()
    QTest.keyClick(panel.history_button, Qt.Key.Key_Tab)
    assert qapp.focusWidget() is panel.case_box
    assert panel.page_scroll.focusPolicy() == Qt.FocusPolicy.NoFocus
    QTest.keyClick(panel.case_box, Qt.Key.Key_Backtab)
    assert qapp.focusWidget() is panel.history_button


def test_the_chart_description_counts_in_the_singular_and_the_plural():
    one = PracticeCase("FAKE-1", "o", (Sample(0, map_art=80),), ())
    assert "Arterial MAP (mmHg): 1 reading from minute 0 to minute 0, lowest 80, highest 80." in chart_description(one)
    gap = PracticeCase("FAKE-2", "g", (Sample(0, map_art=80), Sample(2, map_art=81)), ())
    assert "2 readings from minute 0 to minute 2, lowest 80, highest 81, 1 minute between them has no reading." in (
        chart_description(gap))
    wide = PracticeCase("FAKE-3", "w", (Sample(0, map_art=80), Sample(3, map_art=81)), ())
    assert "2 minutes between them have no reading." in chart_description(wide)
    ignored = PracticeCase("FAKE-4", "i", (Sample(0, map_art=80), Sample(1, map_art=300), Sample(2, spo2=101)), ())
    assert "2 readings outside the usable range are not drawn." in chart_description(ignored)
    assert not re.search(r"\b1 (readings|minutes)\b", chart_description(one) + chart_description(gap))


def test_the_table_marks_a_reading_the_chart_leaves_out_in_words_and_with_the_hatch(qapp):
    case = PracticeCase(
        "FAKE-U", "u",
        (Sample(0, map_art=80, spo2=100), Sample(1, map_art=300, spo2=101), Sample(2, map_art=20, nmap=19, spo2=50),
         Sample(3, spo2=49)),
        (),
    )
    table = TimelineTable()
    table.set_case(case)
    cell = {(row, column): table.item(row, column) for row in range(4) for column in range(1, 4)}
    unusable = "(outside usable range, not drawn)"
    drawn = {(0, 1): "80", (0, 3): "100", (2, 1): "20", (2, 3): "50"}  # the edges of the usable range count
    for key, text in drawn.items():
        assert cell[key].text() == text and cell[key].data(MISSING_ROLE) is not True, key
    for key, text in {(1, 1): "300", (1, 3): "101", (2, 2): "19", (3, 3): "49"}.items():
        assert cell[key].text() == f"{text} {unusable}" and cell[key].data(MISSING_ROLE) is True, key
    assert cell[(0, 2)].text() == "missing" and cell[(0, 2)].data(MISSING_ROLE) is True
    # Chart, table and description agree: the cells marked "not drawn" are the readings the chart leaves out.
    marked = sum(1 for item in cell.values() if "not drawn" in item.text())
    assert marked == 4
    assert f"{marked} readings outside the usable range are not drawn." in chart_description(case)
    assert readings(case, TRACKS[0]) == {0: 80.0, 2: 20.0}
    assert readings(case, TRACKS[1]) == {}
    assert readings(case, TRACKS[2]) == {0: 100.0, 2: 50.0}


def test_the_table_and_chart_helpers_have_public_names_and_nothing_imports_a_private_one_from_the_grader():
    from src.modules.handoff_trainer import grading

    assert grading.classify_dose(Dose("Phenylephrine", 3)) == "bolus"
    assert grading.classify_dose(Dose("Phenylephrine", 3, end_minute=9)) == "infusion"
    assert grading.classify_dose(Dose("Phenylephrine", 3, end_minute=1)) == "end time is before the start time"
    assert grading.format_number(60.0) == "60" and grading.format_number(0.5) == "0.5"
    for path in (ROOT / "src" / "modules" / "handoff_trainer").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "src.modules.handoff_trainer.grading":
                assert not [alias.name for alias in node.names if alias.name.startswith("_")], path.name


@pytest.mark.parametrize("width", [293, 340, 479, 480])
def test_the_page_fits_the_windows_least_width_with_nothing_cut_off(panel, qapp, width):
    panel.resize(width, 800)
    qapp.processEvents()
    panel.timeline_chart.redraw()
    scroll = panel.page_scroll
    body = scroll.widget()
    assert body.minimumSizeHint().width() <= scroll.viewport().width(), (width, body.minimumSizeHint().width())
    assert body.width() <= scroll.viewport().width()
    for widget in (panel.side, panel.timeline_chart, panel.case_box, panel.check_button, panel.next_button):
        right = widget.mapTo(body, QPoint(widget.width(), 0)).x()
        assert right <= body.width(), (widget.objectName() or type(widget).__name__, width, right, body.width())


def test_the_window_can_shrink_to_its_least_width_with_the_practice_page_whole(qapp, monkeypatch, tmp_path):
    from src.desktop.app import MainWindow

    monkeypatch.setenv("MOVER_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("MOVER_EMR_DIR", str(tmp_path / "EMR"))
    monkeypatch.setenv("MOVER_PROCESSED_DIR", str(tmp_path / "processed"))
    monkeypatch.setattr(MainWindow, "_startup", lambda self: None)
    window = MainWindow()
    try:
        window.show()
        window._module_actions["handoff"].trigger()
        qapp.processEvents()
        page = window.module_stack.currentWidget()
        window.resize(window.minimumSizeHint().width(), 800)
        qapp.processEvents()
        qapp.processEvents()
        assert page.objectName() == "handoff-practice" and window.width() == window.minimumSizeHint().width()
        scroll = page.page_scroll
        assert scroll.widget().minimumSizeHint().width() <= scroll.viewport().width()
        assert scroll.widget().width() <= scroll.viewport().width()
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


# —— the contingency box is not graded, and says so ——


NOT_GRADED_LINE = "Not graded: plans and if-then statements are not checked against the timeline."


def test_the_contingency_box_carries_a_visible_not_graded_line_and_describes_itself_with_it(panel):
    from PySide6.QtWidgets import QLabel

    box = panel.sections["situation_awareness"]
    assert box.accessibleDescription() == NOT_GRADED_LINE
    shown = [label for label in panel.side.findChildren(QLabel) if label.text() == NOT_GRADED_LINE]
    assert len(shown) == 1 and shown[0].isVisibleTo(panel)
    # It sits between the box's heading and the box, and no other box has such a line.
    assert shown[0].mapTo(panel, QPoint(0, 0)).y() < box.mapTo(panel, QPoint(0, 0)).y()
    assert all(other.accessibleDescription() == "" for key, other in panel.sections.items()
               if key != "situation_awareness")
    interface = QAccessible.queryAccessibleInterface(box)
    assert interface.text(QAccessible.Text.Description) == NOT_GRADED_LINE


def test_a_plan_in_the_contingency_box_is_saved_and_never_graded(panel):
    _pick(panel, "FAKE-QUIET")
    _write(panel, patient_summary="Stable throughout",
           situation_awareness="If MAP below 65, call senior. Watch for desaturation")
    panel.check_button.click()
    assert _lines(panel) == [] and panel.feedback.summary.text() == NOTHING_NEEDED
    assert panel.feedback.ungraded_note.isVisibleTo(panel)
    assert panel.feedback.ungraded_note.text() == (
        "The Situation awareness and contingency plans section was not graded.")
    (row,) = load_attempts()
    assert row["sections"]["situation_awareness"] == "If MAP below 65, call senior. Watch for desaturation"
    assert "call senior" not in row["handoff"] and row["handoff"] == "Patient summary: Stable throughout."
    assert row["grades"] == []
    assert panel.status.text() == SAVED


def test_the_same_plan_in_a_graded_box_is_read_as_claims(panel):
    _pick(panel, "FAKE-QUIET")
    _write(panel, patient_summary="If MAP below 65, call senior. Watch for desaturation")
    panel.check_button.click()
    assert [line.startswith("Not on the timeline.") for line in _lines(panel)] == [True, True]
    assert not panel.feedback.ungraded_note.isVisibleTo(panel)


def test_text_only_in_the_contingency_box_counts_as_written_saves_and_says_nothing_was_graded(panel):
    _write(panel, situation_awareness="If hypotensive, phenylephrine ready")
    panel.check_button.click()
    assert panel.status.text() == SAVED
    assert panel.feedback.isVisibleTo(panel) and panel.feedback.summary.text() == "Nothing was graded."
    assert _lines(panel) == [] and panel.feedback.show_buttons == []
    assert panel.feedback.ungraded_note.isVisibleTo(panel)
    (row,) = load_attempts()
    assert row["sections"]["situation_awareness"] == "If hypotensive, phenylephrine ready"
    assert row["handoff"] == "" and row["grades"] == []
    assert panel.history_label.text() == "Saved attempts: 1 for this run · 1 in all"
    panel.show_history()
    assert panel.history_dialog.table.item(0, 2).text() == "0 of 0"
    panel.history_dialog.reject()


def test_the_not_graded_sentence_is_said_once_and_only_when_that_box_has_text(panel):
    _write(panel, patient_summary="Hypotension from minute 10 to 16")
    panel.check_button.click()
    assert not panel.feedback.ungraded_note.isVisibleTo(panel)
    _write(panel, situation_awareness="Call if worse")
    panel.check_button.click()
    from PySide6.QtWidgets import QLabel

    shown = [label.text() for label in panel.feedback.findChildren(QLabel) if "not graded" in label.text().lower()]
    assert shown == ["The Situation awareness and contingency plans section was not graded."]


@pytest.mark.parametrize("width", [700, 1300])
def test_the_table_needs_no_sideways_scroll_where_the_page_is_wide_enough_for_it(panel, qapp, width):
    panel.resize(width, 800)
    panel.table_toggle.click()
    qapp.processEvents()
    qapp.processEvents()
    assert panel.timeline_table.horizontalScrollBar().maximum() == 0, width


def test_a_page_too_narrow_for_the_table_scrolls_the_table_and_not_the_page(panel, qapp):
    panel.resize(480, 800)
    panel.table_toggle.click()
    qapp.processEvents()
    qapp.processEvents()
    assert panel.timeline_table.horizontalScrollBar().maximum() > 0, "no column is cut off without a way to reach it"
    assert panel.page_scroll.horizontalScrollBar().maximum() == 0
