"""The Medication and vitals page: the chart, the table and the words agree; the cursor; narrow windows; empty states."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import matplotlib.text
import pandas as pd
import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from src.desktop.theme import apply_theme, token_color
from src.modules.medication_vitals.align import DISCLAIMER, align_rows, fictional_demo
from src.modules.medication_vitals.panel import DEMO_ID, NO_ROWS, UNREADABLE, MedicationVitalsPanel
from src.modules.medication_vitals.timeline import build_timeline, cursor_text, describe
from src.modules.medication_vitals.timeline_chart import (
    LEFT_OUT,
    MAX_LANE_ROWS,
    REVERSED_KEY,
    build_timeline_figure,
    left_out_text,
    render_timeline,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    apply_theme(app, "light")
    return app


@pytest.fixture
def panel(qapp):
    page = MedicationVitalsPanel()
    page.resize(1300, 820)
    page.show()
    for _ in range(3):
        qapp.processEvents()
    page.chart.redraw()
    yield page
    page.close()
    page.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def _record() -> tuple[pd.DataFrame, pd.DataFrame]:
    """A small record whose every number is written here: the expected values below are worked out from it."""
    medications = pd.DataFrame(
        [
            {"Drug_name": "Drug A", "Dose": 5, "Drug_units": "mg", "t_min": 2, "end_min": 2},
            {"Drug_name": "Drug B", "Dose": 10, "Drug_units": "mcg", "t_min": 3, "end_min": 9},
            {"Drug_name": "Drug C", "Dose": None, "Drug_units": "", "t_min": 5, "end_min": None},
        ]
    )
    timeseries = pd.DataFrame(
        [
            {"t_min": 0, "HR": 60, "SPO2": 97, "nMAP": 80},
            {"t_min": 1, "HR": 62, "SPO2": None, "nMAP": 78},
            {"t_min": 2, "HR": None, "SPO2": None, "nMAP": 75},
            {"t_min": 5, "HR": 70, "SPO2": 96, "nMAP": None},
        ]
    )
    return medications, timeseries


READINGS = {
    "Heart rate": {0: 60, 1: 62, 5: 70},
    "SpO2": {0: 97, 5: 96},
    "Noninvasive MAP": {0: 80, 1: 78, 2: 75},
}
MISSING = {"Heart rate": [2], "SpO2": [1, 2], "Noninvasive MAP": [5]}
LABELS = {"Heart rate": ("Heart rate", "bpm"), "SpO2": ("SpO₂", "%"), "Noninvasive MAP": ("Noninvasive MAP", "mmHg")}


def _by_subject(fig) -> dict:
    return {ax.get_label(): ax for ax in fig.axes}


def _table_rows(page) -> list[list[str]]:
    table = page.table
    return [[table.item(row, col).text() for col in range(table.columnCount())] for row in range(table.rowCount())]


def _points(ax) -> dict[float, float]:
    """Every plotted reading of an axes by minute; a break in a trace is NaN and is not a reading."""
    found: dict[float, float] = {}
    for line in ax.lines:
        for x, y in zip(line.get_xdata(), line.get_ydata(), strict=True):
            if y == y:
                found[float(x)] = float(y)
    return found


# —— the chart, the table and the description agree ——


def test_the_chart_the_table_and_the_description_state_the_same_numbers(qapp):
    page = MedicationVitalsPanel()
    medications, timeseries = _record()
    page.show_case("TEST-REC", medications, timeseries)
    timeline = page.chart.timeline()
    assert timeline is not None
    fig, _, _ = build_timeline_figure(timeline, 900, "light")
    axes = _by_subject(fig)
    description = describe(timeline)
    rows = _table_rows(page)

    for subject, found in READINGS.items():
        label, unit = LABELS[subject]
        # The table: one row per recorded minute, the value or the word "missing".
        table = {int(row[0].removeprefix("minute ")): row[2] for row in rows if row[1] == subject}
        assert table == {**{m: str(v) for m, v in found.items()}, **{m: "missing" for m in MISSING[subject]}}
        # The chart: a point for each reading and no other, joined only across neighbouring minutes.
        assert _points(axes[subject]) == {float(m): float(v) for m, v in found.items()}
        joined = [list(line.get_xdata()) for line in axes[subject].lines if line.get_linestyle() != "None"]
        for xs in joined:
            assert xs == list(range(int(xs[0]), int(xs[-1]) + 1)), "a line never runs across a minute the record skips"
            assert all(minute in found for minute in xs), "and never across a missing one"
        # The hatch covers each missing minute and never a minute that has a reading.
        spans = [(p.get_x(), p.get_x() + p.get_width()) for p in axes[subject].patches if p.get_hatch()]
        for minute in MISSING[subject]:
            assert any(low <= minute <= high for low, high in spans), (subject, minute)
        for minute in found:
            assert not any(low < minute < high for low, high in spans), (subject, minute)
        # The words.
        first, last = min(found), max(found)
        assert (f"{label} ({unit}): {len(found)} readings from minute {first} to minute {last}, "
                f"lowest {min(found.values())}, highest {max(found.values())}") in description

    assert "missing at minute 2." in description
    assert "missing at minutes 1 and 2." in description
    assert "missing at minute 5." in description

    lane = axes["doses"]
    marks = [line for line in lane.lines if line.get_marker() == "s"]
    assert sorted(x for line in marks for x in line.get_xdata()) == [2, 5], "a bolus is a mark at its minute"
    bars = [(p.get_x(), p.get_width()) for p in lane.patches if p.get_hatch()]
    assert bars == [(3, 6)], "an infusion is a bar from minute 3 to minute 9"
    assert {"Drug A 5 mg", "Drug B 10 mcg", "Drug C, dose not recorded"} <= {t.get_text() for t in lane.texts}
    for line in ("Drug A 5 mg at minute 2 (bolus)", "Drug B 10 mcg from minute 3 to minute 9 (infusion)",
                 "Drug C, dose not recorded at minute 5 (bolus)"):
        assert line in description
    doses = {row[1]: row for row in rows if row[4] in {"bolus", "infusion"}}
    assert doses["Drug A"][:5] == ["minute 2", "Drug A", "5", "mg", "bolus"]
    assert doses["Drug B"][:5] == ["minute 3 to 9", "Drug B", "10", "mcg", "infusion"]
    assert doses["Drug C"][:5] == ["minute 5", "Drug C", "not recorded", "", "bolus"]
    assert doses["Drug C"][6] == "dose field empty; no end time recorded"
    texts = {t.get_text() for t in fig.findobj(matplotlib.text.Text) if t.get_text()}
    assert {"Heart rate", "SpO₂", "Noninvasive MAP", "bpm", "mmHg", "%", "minute", "Drugs", "missing"} <= texts


def test_a_value_the_record_leaves_out_is_never_drawn_as_zero_or_filled_in(qapp):
    medications, timeseries = _record()
    timeline = build_timeline("t", "minute", align_rows(medications, timeseries))
    fig, _, _ = build_timeline_figure(timeline, 900, "light")
    for subject, ax in _by_subject(fig).items():
        if subject == "doses":
            continue
        shown = set(_points(ax).values())
        assert shown == {float(v) for v in READINGS[subject].values()}, subject
        assert 0.0 not in shown


def test_a_missing_boxs_hatch_grows_to_hold_its_label_only_where_no_reading_is_under_it():
    timeseries = pd.DataFrame(
        [{"t_min": minute, "HR": value} for minute, value in
         ((0, 60), (1, 61), (2, 62), (3, None), (4, 64), (5, 65), (20, 70))]
    )
    timeline = build_timeline("t", "minute", align_rows(None, timeseries))

    def hatch(width: int) -> list[tuple[float, float]]:
        fig, _, _ = build_timeline_figure(timeline, width, "light")
        ax = _by_subject(fig)["Heart rate"]
        return [(p.get_x(), p.get_x() + p.get_width()) for p in ax.patches if p.get_hatch()]

    # Narrow, a label's width would span minutes 2 and 4, which have readings: the hatch keeps its one minute.
    assert hatch(276) == [(2.5, 3.5)]
    # Wide, it grows to hold "missing" and still covers no reading.
    (low, high), = hatch(1100)
    assert high - low > 1 and low < 3 < high and 2 < low and high < 4


def test_the_description_says_how_many_drug_rows_have_only_a_clock_time():
    medications = pd.DataFrame(
        [{"Drug_name": "Drug A", "Dose": 5, "Drug_units": "mg", "Start_time": "2000-01-01 08:10:00", "End_time": r"\N"}]
    )
    timeseries = pd.DataFrame([{"t_min": 0, "HR": 70}, {"t_min": 1, "HR": 71}])
    timeline = build_timeline("t", "minute", align_rows(medications, timeseries))
    assert timeline.off_axis == 1 and timeline.doses == ()
    description = describe(timeline)
    assert "1 drug rows have only a clock time and are not placed on the axis." in description
    assert "No drug doses on the axis." in description
    fig, _, _ = build_timeline_figure(timeline, 900, "light")
    assert "Drug times are clock times, not placed on this axis" in {t.get_text() for t in _by_subject(fig)["doses"].texts}


def test_the_demo_record_in_words_has_its_three_vital_tracks_and_four_drug_rows(qapp):
    medications, timeseries = fictional_demo()
    timeline = build_timeline("Invented record", "invented minute", align_rows(medications, timeseries))
    description = describe(timeline)
    assert "Invented record, minutes 0 to 20." in description
    assert "Heart rate (bpm): 2 readings from minute 0 to minute 4, lowest 70, highest 72, missing at minute 8." in description
    assert "SpO₂ (%): 2 readings from minute 0 to minute 8, lowest 97, highest 98, missing at minute 4." in description
    assert "Noninvasive MAP (mmHg): 3 readings from minute 0 to minute 8, lowest 75, highest 80." in description
    assert "Norepinephrine 50 mcg from minute 4 to minute 20 (infusion)" in description
    assert "Phenylephrine nasal 2 drops at minute 6 (bolus)" in description
    assert "Ephedrine, dose not recorded at minute 8 (bolus)" in description


# —— the page ——


def test_the_chart_has_a_name_and_a_description_and_the_cursor_is_in_it(panel):
    chart = panel.chart
    assert chart.accessibleName() == "Drugs and measurements chart"
    timeline = chart.timeline()
    assert timeline is not None
    assert chart.accessibleDescription().startswith(describe(timeline))
    assert chart.accessibleDescription().endswith(cursor_text(timeline, 0))
    assert panel.table.accessibleName() == "Medication and vital rows"
    assert panel.view_tabs.accessibleName() == "Medication and vitals view"


def test_the_page_says_what_it_is_and_gives_no_reason_or_advice(panel):
    texts = " ".join(label.text() for label in panel.findChildren(QLabel))
    assert DISCLAIMER in texts and "do not say why a drug was given" in texts
    assert panel.case_line.text() == f"{DEMO_ID} · invented record"
    assert "Fictional demonstration" in panel.note.text()
    assert not re.search(r"\b(recommend|should|consider|treat)", texts, re.I)


def test_the_cursor_steps_through_minutes_that_have_a_row_and_the_table_selects_them(panel, qapp):
    medications, timeseries = _record()
    panel.show_case("TEST-REC", medications, timeseries)
    chart = panel.chart
    assert chart.timeline().cursor_minutes() == (0, 1, 2, 3, 5)
    assert chart.cursor_minute() == 0

    def selected_minutes() -> list[str]:
        rows = sorted({index.row() for index in panel.table.selectedIndexes()})
        return [panel.table.item(row, 0).text() for row in rows]

    assert selected_minutes() == ["minute 0"] * 3
    chart.setFocus()
    QTest.keyClick(chart, Qt.Key.Key_Right)
    QTest.keyClick(chart, Qt.Key.Key_Right)
    assert chart.cursor_minute() == 2
    assert selected_minutes() == ["minute 2"] * 4, "three vitals and Drug A"
    assert chart.accessibleDescription().endswith(
        "Cursor at minute 2: Noninvasive MAP 75 mmHg; Heart rate missing; SpO₂ missing; starting: Drug A 5 mg."
    )
    QTest.keyClick(chart, Qt.Key.Key_Right)
    assert chart.cursor_minute() == 3
    assert selected_minutes() == ["minute 3 to 9"]
    assert chart.accessibleDescription().endswith(
        "Cursor at minute 3: Noninvasive MAP no row; Heart rate no row; SpO₂ no row; starting: Drug B 10 mcg."
    )
    QTest.keyClick(chart, Qt.Key.Key_End)
    assert chart.cursor_minute() == 5
    QTest.keyClick(chart, Qt.Key.Key_Right)
    assert chart.cursor_minute() == 5, "the last minute stays the last"
    QTest.keyClick(chart, Qt.Key.Key_Home)
    assert chart.cursor_minute() == 0
    chart.set_cursor(4)
    assert chart.cursor_minute() == 0, "a minute with no row is not a place for the cursor"


def test_choosing_a_table_row_moves_the_cursor_to_its_minute(panel, qapp):
    medications, timeseries = _record()
    panel.show_case("TEST-REC", medications, timeseries)
    row = next(index for index, cells in enumerate(_table_rows(panel)) if cells[1] == "Drug B")
    panel.table.setCurrentCell(row, 1)
    assert panel.chart.cursor_minute() == 3
    drug_a = next(index for index, cells in enumerate(_table_rows(panel)) if cells[1] == "Drug A")
    panel.table.setCurrentCell(drug_a, 1)
    assert panel.chart.cursor_minute() == 2


def test_a_click_on_the_chart_puts_the_cursor_on_the_nearest_minute_with_a_row(panel, qapp):
    medications, timeseries = _record()
    panel.show_case("TEST-REC", medications, timeseries)
    chart = panel.chart
    chart.redraw()
    low, high = chart._xlim
    first = chart._axes[0]
    x = first.left() + (3.9 - low) * first.width() / (high - low)
    QTest.mouseClick(chart, Qt.MouseButton.LeftButton, pos=QPoint(round(x), 20))
    assert chart.cursor_minute() == 3, "minute 4 has no row; 3 is the nearest that has one"
    assert chart.hasFocus()


def test_tab_goes_from_the_view_tabs_to_the_chart_to_the_table_and_the_table_keeps_tab(panel, qapp):
    panel.view_tabs.setFocus()
    seen = []
    for _ in range(2):
        QTest.keyClick(qapp.focusWidget(), Qt.Key.Key_Tab)
        seen.append(qapp.focusWidget())
    assert seen == [panel.chart, panel.table]
    assert not panel.table.tabKeyNavigation()


def test_tab_never_stops_on_an_unnamed_scroll_area(panel, qapp):
    from PySide6.QtWidgets import QAbstractScrollArea

    panel.view_tabs.setFocus()
    stops = []
    for _ in range(12):
        QTest.keyClick(qapp.focusWidget(), Qt.Key.Key_Tab)
        stops.append(qapp.focusWidget())
    assert panel.chart in stops and panel.table in stops
    assert panel.page_scroll not in stops
    assert not [w for w in stops if isinstance(w, QAbstractScrollArea) and not w.accessibleName()]


def test_rows_only_hides_the_chart_and_keeps_the_table(panel, qapp):
    assert panel.chart.isVisibleTo(panel)
    panel.view_tabs.setCurrentIndex(1)
    assert not panel.chart.isVisibleTo(panel) and panel.table.isVisibleTo(panel)
    panel.view_tabs.setCurrentIndex(0)
    assert panel.chart.isVisibleTo(panel)


def test_the_chart_is_redrawn_in_the_other_theme_and_nothing_stays_in_the_old_one(panel, qapp):
    chart = panel.chart

    def ground() -> str:
        image = chart.grab().toImage()
        return image.pixelColor(image.width() // 2, image.height() - 2).name().upper()

    assert ground() == token_color("paper", "light").name().upper()
    apply_theme(qapp, "dark")
    try:
        for _ in range(3):
            qapp.processEvents()
        assert ground() == token_color("paper", "dark").name().upper()
    finally:
        apply_theme(qapp, "light")
    for _ in range(3):
        qapp.processEvents()
    assert ground() == token_color("paper", "light").name().upper()


# —— empty and error states ——


def test_no_rows_and_unreadable_rows_are_said_in_a_plain_sentence(panel, qapp):
    panel.show_case("SYN-X", None, None)
    assert panel.empty_label.isVisibleTo(panel) and panel.empty_label.text() == NO_ROWS
    assert not panel.chart.isVisibleTo(panel) and not panel.table.isVisibleTo(panel)
    assert panel.table.rowCount() == 0
    assert panel.case_line.text().startswith("SYN-X · open case")

    bad = pd.DataFrame([{"Drug_name": "Drug A", "Dose": 1, "Drug_units": "mg", "t_min": "abc", "end_min": 1}])
    panel.show_case("SYN-Y", bad, None)
    assert panel.empty_label.text() == UNREADABLE and panel.table.rowCount() == 0
    assert not panel.chart.isVisibleTo(panel)

    panel.show_fictional()
    assert not panel.empty_label.isVisibleTo(panel)
    assert panel.chart.isVisibleTo(panel) and panel.table.isVisibleTo(panel) and panel.table.rowCount() > 0


def test_an_open_case_is_named_as_research_data_and_says_whether_the_clocks_are_shifted(panel, qapp):
    medications = pd.DataFrame(
        [{"Drug_name": "Drug A", "Dose": 5, "Drug_units": "mg", "Start_time": "2000-01-01 08:10:00", "End_time": r"\N"}]
    )
    timeseries = pd.DataFrame([{"t_min": 0, "HR": 70}])
    panel.show_case("SYN-1", medications, timeseries)
    assert "de-identified research data, not for patient care" in panel.case_line.text()
    assert "not shifted onto one axis" in panel.note.text()
    assert panel.title_label.text() == "Drugs and measurements"
    assert panel.chart.timeline().off_axis == 1
    from datetime import datetime

    panel.show_case("SYN-1", medications, timeseries, origin=datetime(2000, 1, 1, 8, 0, 0))
    assert "Drug minutes use the case start" in panel.note.text()
    assert panel.title_label.text() == "Drugs and measurements on one clock"
    assert panel.chart.timeline().off_axis == 0 and len(panel.chart.timeline().doses) == 1


# —— narrow windows ——


def _inside(page, widget) -> bool:
    body = page.page_scroll.widget()
    return widget.mapTo(body, QPoint(widget.width(), 0)).x() <= body.width() + 1


@pytest.mark.parametrize("width", [276, 340, 480, 640, 760, 940, 960, 1100, 1600])
def test_the_page_never_scrolls_sideways_and_no_edge_is_cut_off(panel, qapp, width):
    panel.resize(width, 820)
    for _ in range(4):
        qapp.processEvents()
    panel.chart.redraw()
    for _ in range(2):
        qapp.processEvents()
    assert panel.page_scroll.horizontalScrollBar().maximum() == 0
    body = panel.page_scroll.widget()
    assert body.width() <= panel.page_scroll.viewport().width()
    for widget in (panel.chart, panel.table, panel.title_label, panel.view_tabs, panel.notice, panel.note):
        assert _inside(panel, widget), (width, widget.objectName() or type(widget).__name__)
    assert render_timeline(panel.chart.timeline(), panel.chart.width(), "light").width == panel.chart.width()


def test_the_table_keeps_two_columns_when_the_page_is_narrow_and_all_seven_when_it_is_wide(panel, qapp):
    panel.resize(276, 820)
    for _ in range(4):
        qapp.processEvents()
    table = panel.table
    assert [col for col in range(7) if not table.isColumnHidden(col)] == [1, 2]
    cells = _table_rows(panel)
    drug = next(row for row in cells if row[1].startswith("Norepinephrine"))
    assert drug[1].split("\n") == ["Norepinephrine", "minute 4 to 20 · infusion", "patient_medication.csv"]
    assert drug[2] == "50 mcg"
    ephedrine = next(row for row in cells if row[1].startswith("Ephedrine"))
    assert ephedrine[1].split("\n") == [
        "Ephedrine", "minute 8 · bolus", "patient_medication.csv", "dose field empty; no end time recorded"]
    assert ephedrine[2] == "not recorded"
    assert {row[2] for row in cells if row[1].startswith("SpO2")} >= {"missing"}
    panel.resize(1300, 820)
    for _ in range(4):
        qapp.processEvents()
    assert [col for col in range(7) if table.isColumnHidden(col)] == []
    assert _table_rows(panel)[0][1] in {"Heart rate", "Noninvasive MAP", "Phenylephrine", "SpO2"}


def test_the_chart_puts_names_above_the_tracks_when_narrow_and_keeps_every_label_in_view(qapp):
    medications, timeseries = fictional_demo()
    timeline = build_timeline("Invented record", "invented minute", align_rows(medications, timeseries))
    narrow, boxes, _ = build_timeline_figure(timeline, 276, "light")
    wide, wide_boxes, _ = build_timeline_figure(timeline, 1100, "light")
    assert boxes[0].left() < 40 < wide_boxes[0].left()
    renderer = narrow.canvas.get_renderer()
    for text in narrow.findobj(matplotlib.text.Text):
        if text.get_text() and text.get_visible():
            box = text.get_window_extent(renderer)
            if text.axes is None or text.axes.get_label() == "doses":
                assert box.x0 >= -1 and box.x1 <= narrow.bbox.x1 + 1, text.get_text()


# —— rules for the package ——


def test_the_medication_package_has_no_colour_of_its_own():
    """Every colour comes from the design tokens, so both themes and the contrast checks cover it."""
    for path in (ROOT / "src" / "modules" / "medication_vitals").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not re.search(r"#[0-9A-Fa-f]{3,8}\b", node.value), (path.name, node.value)
                assert not re.search(r"rgba?\(", node.value), (path.name, node.value)
        assert not re.search(r"QColor\(\s*[\"']#", source), path.name
        assert not re.search(r"QColor\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*\)", source), path.name
        assert "Qt.GlobalColor" not in source, path.name


# —— one word for a reading the record has a row for and no value ——


def test_the_chart_the_table_and_the_description_use_the_same_word_for_a_missing_reading(qapp):
    page = MedicationVitalsPanel()
    medications, timeseries = _record()
    page.show_case("TEST-REC", medications, timeseries)
    timeline = page.chart.timeline()
    fig, _, _ = build_timeline_figure(timeline, 900, "light")
    drawn = {t.get_text() for ax in fig.axes for t in ax.texts}
    assert "missing" in drawn and "no samples" not in drawn
    assert any(row[2] == "missing" for row in _table_rows(page))
    assert "missing at minute 2." in describe(timeline)
    assert "Heart rate missing; SpO₂ missing" in cursor_text(timeline, 2)


# —— a dose whose end time is before its start time ——


def _reversed_record() -> tuple[pd.DataFrame, pd.DataFrame]:
    medications = pd.DataFrame(
        [
            {"Drug_name": "Drug A", "Dose": 2, "Drug_units": "mg", "t_min": 1, "end_min": None},
            {"Drug_name": "Drug W", "Dose": 0.5, "Drug_units": "mg", "t_min": 3, "end_min": 2},
        ]
    )
    return medications, pd.DataFrame([{"t_min": 0, "HR": 60}, {"t_min": 4, "HR": 64}])


def test_a_dose_that_ends_before_it_starts_is_not_drawn_as_a_bolus_and_says_so_everywhere(qapp):
    page = MedicationVitalsPanel()
    medications, timeseries = _reversed_record()
    page.show_case("TEST-REV", medications, timeseries)
    timeline = page.chart.timeline()
    fig, _, _ = build_timeline_figure(timeline, 1100, "light")
    lane = _by_subject(fig)["doses"]
    marks = {line.get_marker(): list(line.get_xdata()) for line in lane.lines}
    assert marks == {"s": [1.0], "D": [3.0]}, "the filled square is the bolus of Drug A only"
    (reversed_mark,) = [line for line in lane.lines if line.get_marker() == "D"]
    assert reversed_mark.get_markerfacecolor() != reversed_mark.get_markeredgecolor(), "an open mark"
    assert not [patch for patch in lane.patches if patch.get_hatch()], "and not an infusion bar"
    # The chart's key names the mark.
    key_words = " ".join(t.get_text() for t in fig.texts)
    assert "end time is before the start time" in key_words and "open diamond" in key_words
    assert "end time is before the start time" in REVERSED_KEY
    # The table: the Kind column and the Record note both say it, with both times as the record gives them.
    row = next(r for r in _table_rows(page) if r[1] == "Drug W")
    assert row[4] == "end time is before the start time"
    assert row[6] == "end time is before the start time (start minute 3, end minute 2)"
    # The description agrees, and does not call the dose a bolus.
    assert "Drug W 0.5 mg at minute 3 (end time is before the start time)" in describe(timeline)
    assert "Drug W 0.5 mg at minute 3 (bolus)" not in describe(timeline)


def test_a_chart_without_such_a_dose_has_no_key_line_for_it(qapp):
    medications, timeseries = _record()
    timeline = build_timeline("t", "minute", align_rows(medications, timeseries))
    fig, _, _ = build_timeline_figure(timeline, 1100, "light")
    assert "open diamond" not in " ".join(t.get_text() for t in fig.texts)
    assert {line.get_marker() for line in _by_subject(fig)["doses"].lines} == {"s"}


def test_the_key_line_for_it_fits_in_the_chart_at_a_narrow_and_a_wide_width(qapp):
    medications, timeseries = _reversed_record()
    timeline = build_timeline("t", "minute", align_rows(medications, timeseries))
    for width in (276, 420, 1100):
        fig, _, _ = build_timeline_figure(timeline, width, "light")
        renderer = fig.canvas.get_renderer()
        right = fig.bbox.width
        for text in fig.texts:
            if text.get_text():
                box = text.get_window_extent(renderer)
                assert 0 <= box.x0 and box.x1 <= right + 1 and 0 <= box.y0 and box.y1 <= fig.bbox.height + 1, (
                    width, text.get_text())


# —— more drug rows than the lane has rows ——


def _many_doses(count: int, minute_of) -> tuple[pd.DataFrame, pd.DataFrame]:
    medications = pd.DataFrame(
        [{"Drug_name": f"Drug {chr(65 + index)}", "Dose": 10 + index, "Drug_units": "mg", "t_min": minute_of(index),
          "end_min": None} for index in range(count)]
    )
    return medications, pd.DataFrame([{"t_min": 0, "HR": 60}, {"t_min": 20, "HR": 64}])


def _drawn_label_boxes(fig):
    renderer = fig.canvas.get_renderer()
    lane = _by_subject(fig)["doses"]
    return [(t.get_text(), t.get_window_extent(renderer)) for t in lane.texts if t.get_text().startswith("Drug ")]


def test_seven_doses_at_one_minute_draw_six_labels_and_say_the_seventh_is_in_the_table_only(qapp):
    page = MedicationVitalsPanel()
    page.resize(1100, 900)
    medications, timeseries = _many_doses(7, lambda index: 5)
    page.show_case("TEST-7", medications, timeseries)
    page.show()
    for _ in range(3):
        qapp.processEvents()
    page.chart.redraw()
    timeline = page.chart.timeline()
    fig, _, _ = build_timeline_figure(timeline, 1100, "light")
    labels = _drawn_label_boxes(fig)
    assert [text for text, _box in labels] == [f"Drug {chr(65 + i)} {10 + i} mg" for i in range(MAX_LANE_ROWS)]
    for index, (_, first) in enumerate(labels):
        for _, second in labels[index + 1:]:
            assert not first.overlaps(second)
    said = [t.get_text() for t in fig.texts]
    assert left_out_text(1) == "1 more dose is in the table only." and left_out_text(1) in said
    # The table lists all seven, and the chart's spoken description says what the picture leaves out.
    assert sum(row[1].startswith("Drug ") for row in _table_rows(page)) == 7
    assert left_out_text(1) in page.chart.accessibleDescription()
    assert "Drug G 16 mg at minute 5 (bolus)" in page.chart.accessibleDescription()
    page.close()


def test_the_count_changes_with_the_doses_and_a_chart_with_room_for_all_says_nothing(qapp):
    for count, expected in ((6, 0), (8, 2), (12, 6)):
        medications, timeseries = _many_doses(count, lambda index: 5)
        timeline = build_timeline("t", "minute", align_rows(medications, timeseries))
        rendered = render_timeline(timeline, 900, "light")
        assert rendered.left_out == expected, count
    # Spread over the axis, the same seven labels share rows and none is left out.
    medications, timeseries = _many_doses(7, lambda index: index * 3)
    timeline = build_timeline("t", "minute", align_rows(medications, timeseries))
    assert render_timeline(timeline, 900, "light").left_out == 0
    fig, _, _ = build_timeline_figure(timeline, 900, "light")
    assert len(_drawn_label_boxes(fig)) == 7
    assert not [t for t in fig.texts if "more dose" in t.get_text()]


@pytest.mark.parametrize("width", [276, 420, 900, 1300])
def test_no_two_drawn_dose_labels_overlap_however_many_doses_there_are(qapp, width):
    medications, timeseries = _many_doses(14, lambda index: (index * 5) % 9 + 2)
    timeline = build_timeline("t", "minute", align_rows(medications, timeseries))
    fig, _, _ = build_timeline_figure(timeline, width, "light")
    labels = _drawn_label_boxes(fig)
    assert labels
    for index, (_, first) in enumerate(labels):
        for _, second in labels[index + 1:]:
            assert not first.overlaps(second)
    assert LEFT_OUT < 0 and len(labels) + render_timeline(timeline, width, "light").left_out == 14


@pytest.mark.parametrize("reversed_index", [0, 6])
def test_the_key_names_the_open_diamond_only_when_one_is_drawn(qapp, reversed_index):
    """Seven doses at one minute: six are drawn and one is left to the table. The key explains the marks on the
    lane, so it names the open diamond exactly when the dose that ends before it starts is one of the drawn ones."""
    page = MedicationVitalsPanel()
    medications, timeseries = _many_doses(7, lambda index: 5)
    medications["end_min"] = medications["end_min"].astype(object)
    medications.loc[reversed_index, "end_min"] = 4
    page.show_case("TEST-KEY", medications, timeseries)
    fig, _, _ = build_timeline_figure(page.chart.timeline(), 1100, "light")
    lane = _by_subject(fig)["doses"]
    diamond_drawn = any(line.get_marker() == "D" for line in lane.lines)
    key_words = " ".join(t.get_text() for t in fig.texts)
    assert ("open diamond" in key_words) == diamond_drawn
    # the first dose is drawn and the seventh is the one left to the table
    assert diamond_drawn == (reversed_index == 0)
    assert left_out_text(1) in [t.get_text() for t in fig.texts]


def test_the_note_about_left_out_doses_sits_inside_the_picture_at_a_narrow_width(qapp):
    medications, timeseries = _many_doses(9, lambda index: 5)
    timeline = build_timeline("t", "minute", align_rows(medications, timeseries))
    rendered = render_timeline(timeline, 276, "light")
    assert rendered.left_out == 3
    fig, _, _ = build_timeline_figure(timeline, 276, "light")
    note = next(t for t in fig.texts if "more doses" in t.get_text())
    box = note.get_window_extent(fig.canvas.get_renderer())
    assert 0 <= box.x0 and box.x1 <= fig.bbox.width and 0 <= box.y0 and box.y1 <= fig.bbox.height
