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
    assert "1 readings outside the usable range are not drawn." in chart_description(case)


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
