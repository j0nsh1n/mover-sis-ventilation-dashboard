"""The case page (F) in the Quiet Record design: header, chart, inspector, ruler and the keys it names."""

from __future__ import annotations

import os

import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QLabel  # noqa: E402

from src.desktop.marks import MISSING_ROLE  # noqa: E402
from src.desktop.research_workspace import ResearchWorkspace  # noqa: E402
from src.desktop.theme import apply_theme, tokens  # noqa: E402
from src.desktop.threshold_ruler import ThresholdRuler, ruler_for, scale_for, tick_values  # noqa: E402
from src.rules import load_rule_catalog  # noqa: E402
from src.services.case_fetch import FetchResult  # noqa: E402
from src.services.retrieval import RetrievalResult, SearchCandidate  # noqa: E402

MINUTES = 40


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    apply_theme(app, "light")
    return app


def _candidate() -> SearchCandidate:
    return SearchCandidate("caseA", "analyzed case", True, "Laparoscopic cholecystectomy", 55, "F",
                           "PID caseA.", "keyword", 0.5)


def _case():
    """40 minutes: PIP peaks at minute 30; ETCO2 is missing at 0-1 and 38-39 (outside its span) and at 10-13 (inside)."""
    minute = list(range(MINUTES))
    start = pd.Timestamp("2020-01-01 08:00")
    ts = pd.DataFrame({
        "PID": ["caseA"] * MINUTES, "t_min": minute,
        "PIP": [20. + (10. if m == 30 else 0.) for m in minute],
        "ETCO2": [None if m <= 1 or 10 <= m <= 13 or m >= 38 else 35. + (m % 3) for m in minute],
        "HR": [70. + (m % 5) for m in minute],
        "SPO2": [{20: 92., 21: 91., 22: 90., 23: 91., 24: 92.}.get(m, 98.) for m in minute],
        "nMAP": [None if m == 30 else 70. for m in minute],
        "Surgery_start": [start] * MINUTES, "Surgery_end": [start + pd.Timedelta(minutes=35)] * MINUTES,
    })
    flags = pd.DataFrame([
        {"PID": "caseA", "t_min": m, "rule_id": rule, "severity": severity, "value": 90.,
         "Obs_time": start + pd.Timedelta(minutes=m), "message": "synthetic", "phase": "maintenance"}
        for rule, severity, minutes in [("spo2_low", "critical", range(20, 25)), ("etco2_high", "warn", range(5, 8))]
        for m in minutes
    ])
    return ts, flags


@pytest.fixture
def page(qapp):
    catalog = load_rule_catalog("default")
    view = ResearchWorkspace()
    view.resize(1316, 1000)
    view.set_rule_catalog(catalog)
    view.set_results(RetrievalResult(candidates=(_candidate(),)))
    ts, flags = _case()
    fetched = FetchResult(cases=pd.DataFrame({"PID": ["caseA"], "Age": [55]}), timeseries=ts, flags=flags,
                          events=pd.DataFrame())
    view.set_case_data("caseA", ts, flags, fetched=fetched, thresholds_sha256=catalog.thresholds_sha256)
    view.show()
    view.open_detail("caseA")
    QTest.qWait(200)
    yield view, catalog
    view.close()


def _texts(view) -> list[str]:
    return [label.text() for label in view.signal_scroll.widget().findChildren(QLabel)]


def test_header_names_the_case_where_it_came_from_the_procedure_and_one_facts_line(page):
    view, catalog = page
    assert view.detail_context.text().startswith("caseA · opened from the evidence trail")
    assert view.detail_title.text() == "Laparoscopic cholecystectomy"
    # duration is the last minute of the opened case; age is shown as the search record shows it
    assert view.detail_facts.text() == (
        f"39 min surgery · age 55 · 1 sample/min · rules default @{catalog.thresholds_sha256[:12]} · 2 rule episodes")
    assert view.minute_stamp.text() == "30"  # the peak PIP, where the case opens
    assert "min from incision" in _texts(view)
    assert view.back_button.text() == "← Back to evidence trail"
    view.return_to_canvas()
    view.open_detail("caseA", from_overview=True)
    assert view.detail_context.text().startswith("caseA · opened from the Cases list")
    assert view.back_button.text() == "← Back to cases"


def test_the_old_header_and_foot_are_gone(page):
    view, _ = page
    joined = " ".join(_texts(view)).lower()
    for gone in ("signal detail", "go straight to the moment", "inspect the observation from g",
                 "not for clinical care", "flag marks:", "shaded = no samples", "selected moment"):
        assert gone not in joined


def test_source_row_lists_signals_with_values_units_and_no_row_column(page):
    view, _ = page
    table = view.source_row_table
    assert [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())] == ["Signal", "Value", "Unit"]
    assert view.source_heading.text() == "Source row at minute 30"
    rows = [[table.item(r, c).text() for c in range(3)] for r in range(table.rowCount())]
    assert rows == [["PIP", "30", "cmH₂O"], ["ETCO₂", "35", "mmHg"], ["Heart rate", "70", "bpm"],
                    ["SpO₂", "98", "%"], ["NIBP MAP", "missing", "mmHg"]]


def test_a_missing_value_is_hatched_and_says_missing(page):
    view, _ = page
    table = view.source_row_table
    assert table.item(4, 1).text() == "missing" and table.item(4, 1).data(MISSING_ROLE) is True
    assert all(table.item(r, 1).data(MISSING_ROLE) is not True for r in range(4))
    view.minute_slider.setValue(11)  # ETCO2 has no sample at minute 11
    assert table.item(1, 1).text() == "missing" and table.item(1, 1).data(MISSING_ROLE) is True
    assert view.metric_values["ETCO2"].text() == "missing"
    assert "ETCO₂ missing" in view.detail_chart.cursor_summary()


def test_a_charted_track_has_a_swatch_in_the_table_and_the_others_do_not(page):
    view, _ = page
    table = view.source_row_table

    def swatches() -> list[bool]:
        return [table.item(r, 0).icon().pixmap(14, 8).toImage().pixelColor(7, 4).alpha() > 0
                for r in range(table.rowCount())]

    assert swatches() == [True, True, True, False, False]  # SpO2 and MAP are rows but not tracks yet
    view.episode_list.setCurrentRow(1)  # spo2_low adds SPO2 as a track
    assert [table.item(r, 0).text() for r in range(table.rowCount())] == [
        "PIP", "ETCO₂", "Heart rate", "SpO₂", "NIBP MAP"]
    assert swatches() == [True, True, True, True, False]


def test_open_table_opens_the_source_rows_at_the_selected_minute(page, monkeypatch):
    view, _ = page
    seen = {}

    def inspect(dialog):
        seen["title"] = dialog.windowTitle()
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(QDialog, "exec", inspect)
    view.open_table_button.click()
    assert seen["title"] == "Source samples · caseA"


def test_the_selected_rule_shows_its_name_id_description_ruler_and_numbers(page):
    view, catalog = page
    assert not view.threshold_ruler.isVisibleTo(view) and not view.rule_title.isVisibleTo(view)
    assert view.rule_detail.text() == "Select an episode to inspect its rule and measurements."
    view.episode_list.setCurrentRow(1)
    assert view.rule_title.text() == "Low oxygen saturation" and view.rule_id_label.text() == "spo2_low"
    assert view.rule_description.text() == catalog.get("spo2_low").description
    text = view.rule_detail.text()
    assert "warn at most 92 %" in text and "critical at most 88 %" in text  # the loaded preset's thresholds
    assert "SPO2: min 90, max 92, mean 91.2; 0 missing minutes" in text
    ruler = view.threshold_ruler
    assert ruler.isVisibleTo(view)
    # SPO2 over minutes 20-24 is 92, 91, 90, 91, 92: the lowest is 90
    assert ruler.accessibleDescription() == (
        "Threshold ruler for Low oxygen saturation. Limits: warn at 92 % or less; critical at 88 % or less. "
        "Observed minimum during the episode: 90 %.")
    assert [mark.label for mark in ruler.spec().marks] == ["warn ≤ 92", "critical ≤ 88"]
    assert ruler.spec().observed_label == "min 90"
    assert view.rule_link.text() == "Rule reference" and view.clear_episode_button.text() == "Clear episode selection"
    view.clear_episode_button.click()
    assert not ruler.isVisibleTo(view) and ruler.spec() is None


@pytest.mark.parametrize("rule,direction,marks,observed", [
    ("etco2_high", "high", [("warn", 50.), ("critical", 60.)], 52.),
    ("hr_low", "low", [("warn", 45.), ("critical", 40.)], 41.),
    ("pip_rising", "high", [("fires", 0.5)], 0.82),
    ("agent_high", "high", [("warn", 1.5), ("critical", 2.0)], None),
])
def test_ruler_for_a_rule_with_numeric_limits(rule, direction, marks, observed):
    spec = ruler_for(load_rule_catalog("default").get(rule), observed)
    assert spec.direction == direction
    assert [(mark.word, mark.value) for mark in spec.marks] == marks
    assert spec.observed == observed
    words = spec.description()
    for _word, value in marks:
        assert f"{value:g}" in words
    assert ("Observed" in words) is (observed is not None)
    low, high, _step = scale_for(spec)
    assert low <= min(value for _w, value in marks) and high >= max(value for _w, value in marks)
    if observed is not None:
        assert low <= observed <= high
    assert tick_values(spec)[0] >= low and tick_values(spec)[-1] <= high


@pytest.mark.parametrize("rule", ["compliance_concern", "hypoventilation_pattern", "peep_zero_long", "tv_abs_extreme",
                                  "fio2_high_long", "agent_drift"])
def test_a_rule_without_one_numeric_limit_has_no_ruler(rule):
    assert ruler_for(load_rule_catalog("default").get(rule), 3.0) is None


def test_ruler_labels_that_would_collide_go_to_another_row(qapp):
    ruler = ThresholdRuler()
    ruler.resize(300, 60)
    # warn 92 and critical 88 are close on the scale; the observed 90 sits between them
    ruler.set_ruler(ruler_for(load_rule_catalog("default").get("spo2_low"), 90.))
    rows = dict(ruler.label_rows())
    assert len({rows["warn ≤ 92"], rows["critical ≤ 88"], rows["min 90"]}) >= 2
    assert ruler.height() >= 44 + 15 * (max(rows.values()))


def test_the_ruler_paints_its_pointer_in_the_accent(qapp):
    ruler = ThresholdRuler()
    ruler.resize(320, 60)
    ruler.set_ruler(ruler_for(load_rule_catalog("default").get("etco2_high"), 52.))
    image = ruler.grab().toImage()
    accent = tokens("light")["accent"].lower()
    assert any(image.pixelColor(x, y).name().lower() == accent for x in range(image.width()) for y in range(0, 14))


def test_inspector_sits_beside_the_chart_when_wide_and_below_it_when_narrow(page, qapp):
    view, _ = page
    holder, inspector = view.signal_page.chart_holder, view.signal_page.inspector
    assert inspector.geometry().left() >= holder.geometry().right() and inspector.width() == 368
    for width, drawer in ((900, True), (900, False), (700, False)):
        view.resize(width, 1000)
        if drawer:
            view.open_rules()
        qapp.processEvents()
        assert inspector.geometry().top() >= holder.geometry().bottom(), (width, drawer)
        assert view.signal_scroll.horizontalScrollBar().maximum() == 0, (width, drawer)
        assert view.rule_link.isVisible() and view.detail_chart.isVisible()
        view.rules_drawer.close_drawer() if drawer else None


def test_the_keys_line_names_only_keys_that_work(page, qapp):
    view, _ = page
    line = view.keys_label.text()
    for advertised in ("← →", "Page Up", "Page Down", "↑ ↓", "Esc", "Ctrl+R"):
        assert advertised in line
    for not_built in ("Shift", "drag", "E next", "click the time axis"):
        assert not_built not in line
    slider = view.minute_slider
    slider.setFocus()
    start = slider.value()
    QTest.keyClick(slider, Qt.Key.Key_Left)
    assert slider.value() == start - 1
    QTest.keyClick(slider, Qt.Key.Key_Right)
    assert slider.value() == start
    QTest.keyClick(slider, Qt.Key.Key_PageDown)
    assert slider.value() == start - 10
    QTest.keyClick(slider, Qt.Key.Key_PageUp)
    assert slider.value() == start
    view.episode_list.setFocus()
    QTest.keyClick(view.episode_list, Qt.Key.Key_Home)
    assert view.selected_rule_id == "etco2_high"
    QTest.keyClick(view.episode_list, Qt.Key.Key_Down)
    assert view.selected_rule_id == "spo2_low"
    QTest.keyClick(view.episode_list, Qt.Key.Key_Up)
    assert view.selected_rule_id == "etco2_high"
    slider.setFocus()
    QTest.keyClick(slider, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier)
    assert view.rules_drawer.is_open
    view.rules_drawer.close_drawer()
    QTest.keyClick(slider, Qt.Key.Key_Escape)
    assert view.stack.currentIndex() == 0


def test_the_chart_hatches_a_gap_inside_the_recorded_span_and_says_no_samples(qapp):
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    from src.desktop.charts import focused_signals_figure

    ts, _ = _case()
    figure = focused_signals_figure(ts, None, figsize=(900 / 110, 460 / 110), theme="light")
    etco2 = figure.axes[1]
    hatched = [(p.get_x(), p.get_x() + p.get_width()) for p in etco2.patches if isinstance(p, Rectangle) and p.get_hatch()]
    assert hatched == [(9.5, 13.5)]  # minutes 10-13 only; 0-1 and 38-39 lie outside the span the signal was recorded in
    assert tokens("light")["hatch"].startswith("rgba(71")
    assert any(text.get_text() == "no samples" for text in etco2.texts)
    assert not any(text.get_text() == "no samples" for text in figure.axes[0].texts)  # PIP has no gap
    narrow = focused_signals_figure(ts, None, figsize=(300 / 110, 460 / 110), theme="light")
    assert not any(text.get_text() == "no samples" for text in narrow.axes[1].texts)  # too narrow: hatch only
    assert any(isinstance(p, Rectangle) and p.get_hatch() for p in narrow.axes[1].patches)
    plt.close("all")


def test_incision_and_closure_are_marked_only_when_the_case_has_those_times(qapp):
    import matplotlib.pyplot as plt

    from src.desktop.charts import case_marks, focused_signals_figure

    ts, _ = _case()
    assert case_marks(ts) == {"Incision": 0.0, "Closure": 35.0}
    figure = focused_signals_figure(ts, None, theme="light")
    assert {"Incision", "Closure"} <= {text.get_text() for text in figure.axes[0].texts}
    bare = ts.drop(columns=["Surgery_start", "Surgery_end"])
    assert case_marks(bare) == {}
    plain = focused_signals_figure(bare, None, theme="light")
    assert not {"Incision", "Closure"} & {text.get_text() for text in plain.axes[0].texts}
    plt.close("all")


def test_the_lane_has_one_hatched_band_per_episode_named_where_it_fits(page):
    view, _ = page
    chart = view.detail_chart
    bands = chart.band_rects()
    assert [(band.rule_id, band.start, band.end) for band, _rect in bands] == [
        ("etco2_high", 4.5, 7.5), ("spo2_low", 19.5, 24.5)]
    assert all(rect.width() > 0 and rect.height() > 0 for _band, rect in bands)


def test_the_selected_episode_is_outlined_in_the_accent_and_the_cursor_has_a_tag(page):
    view, _ = page
    chart = view.detail_chart
    view.episode_list.setCurrentRow(1)
    QTest.qWait(50)
    image = chart.grab().toImage()
    accent = tokens("light")["accent"].lower()
    band = dict((b.rule_id, r) for b, r in chart.band_rects())["spo2_low"].adjusted(-2, -2, 2, 2)
    # the outline's top edge, and the tag on the time axis at the cursor
    assert image.pixelColor(int(band.center().x()), int(band.top())).name().lower() == accent
    other = dict((b.rule_id, r) for b, r in chart.band_rects())["etco2_high"].adjusted(-2, -2, 2, 2)
    assert image.pixelColor(int(other.center().x()), int(other.top())).name().lower() != accent
    tag_y = int(chart._axes_rects[-1].bottom()) + 8
    assert image.pixelColor(int(chart.cursor_x()) + 20, tag_y).name().lower() == accent


def test_the_episode_row_marks_the_selection_with_an_accent_bar(page):
    view, _ = page
    view.episode_list.setCurrentRow(0)
    QTest.qWait(50)
    image = view.episode_list.viewport().grab().toImage()
    accent = tokens("light")["accent"].lower()
    assert image.pixelColor(1, 16).name().lower() == accent
    assert image.pixelColor(1, 48).name().lower() != accent


def test_every_control_on_the_page_has_a_name(page):
    from PySide6.QtWidgets import QAbstractButton, QAbstractSlider, QLineEdit

    view, _ = page
    controls = [control for kind in (QAbstractButton, QAbstractSlider, QLineEdit)
                for control in view.signal_scroll.widget().findChildren(kind)]
    assert len(controls) >= 9
    for control in controls:
        if control.isVisibleTo(view) and control.isEnabledTo(view):
            assert control.accessibleName() or getattr(control, "text", lambda: "")(), control.objectName()
    for label in view.metric_values.values():
        assert label.accessibleName().endswith("at the selected minute")
    assert view.detail_chart.accessibleName() == "Signal chart"
    assert "Minute 30:" in view.detail_chart.accessibleDescription()


def test_the_ask_field_names_the_minute_and_the_ask_button_is_the_filled_one(page):
    view, _ = page
    assert view.follow_up_edit.placeholderText() == "Ask about minute 30"
    assert view.follow_up_button.objectName() == "primaryBtn"
    filled = [b for b in view.signal_scroll.widget().findChildren(type(view.follow_up_button)) if b.objectName() == "primaryBtn"]
    assert filled == [view.follow_up_button]
    assert view.more_signals_button.text() == "+ Track" and view.more_signals_button.accessibleName() == "More signals"
    view.minute_slider.setValue(12)
    assert view.follow_up_edit.placeholderText() == "Ask about minute 12"
