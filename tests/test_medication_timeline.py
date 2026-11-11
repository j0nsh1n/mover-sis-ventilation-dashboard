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
from src.modules.medication_vitals.timeline_chart import build_timeline_figure, render_timeline

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
    assert {"Heart rate", "SpO₂", "Noninvasive MAP", "bpm", "mmHg", "%", "minute", "Drugs", "no samples"} <= texts


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
    # Wide, it grows to hold "no samples" and still covers no reading.
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
