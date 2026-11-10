"""The Cases page in the Quiet Record design: header, filter, charts, table, preview and layout (offscreen Qt)."""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QModelIndex, QPoint, Qt  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

import src.desktop.cases_overview as cases_module  # noqa: E402
from src.desktop.cases_overview import (  # noqa: E402
    COLUMNS,
    EMPTY_MESSAGE,
    SCORE_BAR_WIDTH,
    SCORE_COLUMN,
    CasesOverview,
    score_bar_length,
)
from src.desktop.cases_preview import (  # noqa: E402
    EPISODES_SHOWN,
    NO_CASES_TO_PREVIEW,
    NOTHING_TO_PREVIEW,
    SIGNALS_ON_OPEN,
    PreviewSource,
)
from src.desktop.cases_summary import (  # noqa: E402
    NO_DURATIONS,
    NO_FLAGGED_RULES,
    SCORE_CAPTION,
    duration_spec,
    rules_spec,
    score_spec,
)
from src.desktop.research_workspace import ResearchWorkspace  # noqa: E402
from src.desktop.theme import apply_theme, token_color, tokens  # noqa: E402
from src.pipeline.corpus_scan import ScanResult  # noqa: E402
from src.rules import load_rule_catalog  # noqa: E402

SRC = Path(__file__).resolve().parents[1] / "src" / "desktop"
HIGH_PIP = load_rule_catalog("default").get("pip_high").label


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True, scope="module")
def light_theme(qapp):
    # the page is styled by the app's stylesheet, so its pictures need the theme applied once
    apply_theme(qapp, "light")
    yield
    apply_theme(qapp, "light")


def _cases() -> pd.DataFrame:
    """Three cases: a flagged hip, a flagged craniotomy and one with no flagged minute."""
    return pd.DataFrame({
        "PID": ["SYN0000000001", "SYN0000000002", "SYN0000000003"],
        "Procedure_short": ["Total hip arthroplasty", "Craniotomy", "Thyroidectomy"],
        "Age": [71, 22, 51], "Gender": ["F", "M", "F"],
        "case_duration_min": [120.0, 90.0, 60.0],
        "anomaly_score": [50, 25, 0], "n_warn": [15, 0, 0], "n_critical": [10, 10, 0],
        "top_rules": ["pip_high(12), made_up_rule(3)", "made_up_rule(9)", ""],
    })


def _minutes(pid: str, pip: list[float | None]) -> pd.DataFrame:
    return pd.DataFrame({"PID": pid, "t_min": range(len(pip)), "PIP": pip,
                         "ETCO2": [35.0] * len(pip), "HR": [70.0] * len(pip)})


def _episodes(pid: str, rows: list[tuple[str, str, float]]) -> pd.DataFrame:
    return pd.DataFrame({"PID": pid, "rule_id": [r[0] for r in rows], "severity": [r[1] for r in rows],
                         "duration_min": [r[2] for r in rows],
                         "t_start_min": 0.0, "t_end_min": [r[2] - 1 for r in rows]})


_OPEN: list[ResearchWorkspace] = []


@pytest.fixture(autouse=True)
def delete_workspaces(qapp):
    """A page is a large tree; leaving every test's behind makes each later theme change repolish all of them."""
    yield
    while _OPEN:
        view = _OPEN.pop()
        view.close()
        view.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _workspace(width: int = 1316, **data) -> tuple[ResearchWorkspace, CasesOverview]:
    view = ResearchWorkspace()
    _OPEN.append(view)
    view.resize(width, 800)
    view.show()
    view.set_cases_data(data.pop("cases", _cases()), data.pop("scan", None), load_rule_catalog("default"),
                        data.pop("flags", None), data.pop("timeseries", None), data.pop("episodes", None))
    view.show_cases()
    QApplication.processEvents()
    assert view._cases_overview is not None
    return view, view._cases_overview


def _pids(overview: CasesOverview) -> list[str]:
    return [overview.cell_text(row, 0) for row in range(overview._proxy.rowCount())]


def _column(key: str) -> int:
    return next(i for i, column in enumerate(COLUMNS) if column.key == key)


# -- header, scope and back ------------------------------------------------------------------------------


def test_header_names_the_page_and_says_what_is_listed_in_one_sentence(qapp):
    view, overview = _workspace()
    assert overview.summary_label.text() == "2 of 3 analyzed cases have rule episodes · sorted by anomaly score"
    assert overview.scope_label.text() == "Loaded sample: 3 analyzed cases."
    view.close()


@pytest.mark.parametrize(("key", "order", "phrase"), [
    ("Age", Qt.SortOrder.AscendingOrder, "sorted by age"),
    ("Age", Qt.SortOrder.DescendingOrder, "sorted by age, highest first"),
    ("anomaly_score", Qt.SortOrder.AscendingOrder, "sorted by anomaly score, lowest first"),
    ("PID", Qt.SortOrder.AscendingOrder, "sorted by PID"),
    ("Procedure_short", Qt.SortOrder.DescendingOrder, "sorted by procedure, Z to A"),
    ("median_ETCO2", Qt.SortOrder.AscendingOrder, "sorted by median ETCO₂"),
])
def test_the_sentence_names_the_sort_on_screen(qapp, key, order, phrase):
    view, overview = _workspace()
    overview.table.sortByColumn(_column(key), order)
    assert overview.summary_label.text().endswith(f" · {phrase}")
    view.close()


def test_the_scope_tabs_appear_for_a_scan_and_switch_the_list(qapp):
    scan_cases = _cases().iloc[:2].assign(PID=["SCAN1", "SCAN2"])
    scan = ScanResult(scan_cases, pd.DataFrame(), {"n_indexed_surgeries": 19114, "n_ventilated_surgeries": 90,
                                                   "preset": "default", "failed_shards": []})
    view, overview = _workspace(scan=scan)
    assert (overview.sample_tab.text(), overview.scan_tab.text()) == ("Analyzed 3", "Indexed 19,114")
    assert overview.scan_tab.isVisibleTo(overview)
    assert _pids(overview)[0].startswith("SYN")
    QTest.mouseClick(overview.scan_tab, Qt.MouseButton.LeftButton)
    assert overview.scope() == "scan" and _pids(overview)[0].startswith("SCAN")
    assert overview.scope_label.text() == (
        "Full-EMR scan: 2 scored of 19,114 indexed surgeries, 19,024 without ventilator data.")
    QTest.mouseClick(overview.sample_tab, Qt.MouseButton.LeftButton)
    assert overview.scope() == "sample" and _pids(overview)[0].startswith("SYN")
    view.close()


def test_a_case_with_only_info_flags_still_has_rule_episodes(qapp):
    cases = _cases().assign(n_info=[0, 0, 4], n_warn=[0, 0, 0], n_critical=[0, 0, 0])
    view, overview = _workspace(cases=cases)
    assert overview.summary_label.text().startswith("1 of 3 analyzed cases has rule episodes")
    view.close()


def test_a_scan_that_does_not_know_the_indexed_count_labels_its_tab_with_the_scored_count(qapp):
    scan = ScanResult(_cases().iloc[:2].assign(PID=["SCAN1", "SCAN2"]), pd.DataFrame(), {"preset": "default"})
    view, overview = _workspace(scan=scan)
    assert overview.scan_tab.text() == "Scanned 2"
    assert overview.scope_label.text() == "Loaded sample: 3 analyzed cases."
    overview.set_scope("scan")
    assert overview.scope_label.text() == "Full-EMR scan: 2 scored."
    view.close()


def test_a_lone_analyzed_tab_is_a_label_and_not_a_tab_stop(qapp):
    view, overview = _workspace()
    assert overview.sample_tab.isVisibleTo(overview) and not overview.scan_tab.isVisibleTo(overview)
    assert overview.sample_tab.focusPolicy() == Qt.FocusPolicy.NoFocus
    view.close()


def test_back_is_a_quiet_text_link_that_says_where_it_goes(qapp):
    view, overview = _workspace()
    back = overview.back_button
    assert back.text() == "← Back to question" and back.objectName() == "linkBtn"
    assert back.accessibleName() == "Back to the research question"
    seen = []
    overview.back_requested.connect(lambda: seen.append(1))
    back.click()
    assert seen == [1] and view.stack.currentIndex() == 0
    view.close()


# -- filter ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("typed", "expected"), [
    ("hip", ["SYN0000000001"]),                  # procedure
    ("ARTHRO", ["SYN0000000001"]),               # case-insensitive
    ("0000000002", ["SYN0000000002"]),           # case ID
    ("  cranio  ", ["SYN0000000002"]),           # surrounding spaces do not count
    (HIGH_PIP.lower(), ["SYN0000000001"]),       # a rule's name, as shown
    ("made_up", ["SYN0000000001", "SYN0000000002"]),  # an id the preset does not know is its own name
    ("12", []),                                  # a flagged-minute count is not a name
    ("pip_high", []),                            # nor is the id of a rule the preset names
    ("", ["SYN0000000001", "SYN0000000002", "SYN0000000003"]),
])
def test_the_filter_keeps_rows_whose_case_procedure_or_rule_name_contains_the_text(qapp, typed, expected):
    view, overview = _workspace()
    overview.filter_edit.setText(typed)
    assert sorted(_pids(overview)) == expected
    view.close()


def test_the_filter_says_how_many_rows_show_and_what_the_charts_count(qapp, monkeypatch):
    spec = Mock(wraps=cases_module.score_spec)
    monkeypatch.setattr(cases_module, "score_spec", spec)
    view, overview = _workspace()
    spec.reset_mock()
    assert not overview.filter_count_label.isVisibleTo(overview)
    overview.filter_edit.setText("made_up")
    assert overview.filter_count_label.text() == "2 of 3 shown. The charts above still count all 3."
    assert overview.summary_label.text() == (
        "2 of 2 analyzed cases matching “made_up” have rule episodes · sorted by anomaly score")
    # the charts describe the whole scope: typing in the filter does not redraw them
    spec.assert_not_called()
    overview.filter_edit.setText("")
    assert not overview.filter_count_label.isVisibleTo(overview)
    view.close()


def test_a_filter_that_matches_nothing_says_so_in_a_sentence(qapp):
    view, overview = _workspace()
    overview.filter_edit.setText("zzz")
    assert overview.no_match_label.isVisibleTo(overview) and not overview.table.isVisibleTo(overview)
    assert overview.no_match_label.text() == "No case matches “zzz”. Clear the filter to see all 3 cases."
    assert overview.summary_label.text() == "0 of 3 analyzed cases match “zzz”."
    assert overview.preview.note.text() == NO_CASES_TO_PREVIEW
    overview.filter_edit.setText("")
    assert overview.table.isVisibleTo(overview) and not overview.no_match_label.isVisibleTo(overview)
    view.close()


def test_sorting_works_on_the_filtered_rows(qapp):
    view, overview = _workspace()
    overview.filter_edit.setText("made_up")
    assert _pids(overview) == ["SYN0000000001", "SYN0000000002"]
    overview.table.sortByColumn(SCORE_COLUMN, Qt.SortOrder.AscendingOrder)
    assert _pids(overview) == ["SYN0000000002", "SYN0000000001"]
    view.close()


def test_slash_reaches_the_filter_from_the_page_but_types_a_slash_inside_a_text_field(qapp):
    view, overview = _workspace()
    window = view.window()
    for start in (overview.table, overview.back_button, overview.preview.open_button):
        start.setFocus()
        QTest.keyClick(start, Qt.Key.Key_Slash)
        assert window.focusWidget() is overview.filter_edit, start
        assert overview.filter_edit.text() == ""
        overview.back_button.setFocus()
    QTest.keyClick(overview.filter_edit, Qt.Key.Key_Slash)
    assert overview.filter_edit.text() == "/"
    view.close()


def test_escape_clears_typed_text_first_and_goes_back_only_from_an_empty_filter(qapp):
    view, overview = _workspace()
    backs = []
    overview.back_requested.connect(lambda: backs.append(1))
    overview.focus_filter()
    QTest.keyClicks(overview.filter_edit, "hip")
    assert _pids(overview) == ["SYN0000000001"]
    QTest.keyClick(overview.filter_edit, Qt.Key.Key_Escape)
    assert overview.filter_edit.text() == "" and backs == []
    assert len(_pids(overview)) == 3 and view.window().focusWidget() is overview.filter_edit
    QTest.keyClick(overview.filter_edit, Qt.Key.Key_Escape)
    assert backs == [1]
    view.close()


# -- the table -------------------------------------------------------------------------------------------


def test_the_table_keeps_every_column_and_name_and_aligns_headings_over_their_cells(qapp):
    view, overview = _workspace()
    model = overview.table.model()
    names = [model.headerData(i, Qt.Orientation.Horizontal) for i in range(model.columnCount())]
    assert names == ["PID", "Procedure", "Age", "Sex", "Duration (min)", "Anomaly score", "Warn count",
                     "Critical count", "Top rules", "Primary agent", "Median TV (mL)", "Median PIP (cmH₂O)",
                     "Median ETCO₂ (mmHg)"]
    numbers = {"Age", "case_duration_min", "anomaly_score", "n_warn", "n_critical", "median_TV", "median_PIP",
               "median_ETCO2"}
    for i, column in enumerate(COLUMNS):
        heading = int(model.headerData(i, Qt.Orientation.Horizontal, Qt.ItemDataRole.TextAlignmentRole))
        cell = model.data(model.index(0, i), Qt.ItemDataRole.TextAlignmentRole)
        side = Qt.AlignmentFlag.AlignRight if column.key in numbers else Qt.AlignmentFlag.AlignLeft
        assert heading & int(side), column.key
        assert bool(cell is not None and int(cell) & int(Qt.AlignmentFlag.AlignRight)) == (column.key in numbers), column.key
    assert not overview.table.showGrid() and not overview.table.alternatingRowColors()
    view.close()


def test_a_zero_count_is_muted_text_and_never_a_faded_one(qapp):
    view, overview = _workspace()
    overview.table.sortByColumn(_column("PID"), Qt.SortOrder.AscendingOrder)
    delegate = overview.table.itemDelegate()
    model = overview.table.model()
    # SYN...2 has no warn minutes and ten critical ones; SYN...1 has fifteen warn minutes
    assert delegate.text_token(model.index(1, _column("n_warn"))) == "ink_3"
    assert delegate.text_token(model.index(1, _column("n_critical"))) == "ink"
    assert delegate.text_token(model.index(0, _column("n_warn"))) == "ink"
    assert delegate.text_token(model.index(2, _column("anomaly_score"))) == "ink"  # only counts are muted

    # painted: the zero is drawn in the muted token itself, not a blend of ink and the ground
    image = overview.table.viewport().grab().toImage()
    cell = overview.table.visualRect(model.index(1, _column("n_warn")))
    ground = image.pixelColor(cell.left() + 2, cell.top() + 2)
    painted = [image.pixelColor(x, y) for x in range(cell.left(), cell.right())
               for y in range(cell.top() + 4, cell.bottom() - 4)]
    darkest = max(painted, key=lambda c: abs(c.red() - ground.red()) + abs(c.green() - ground.green()))
    muted = token_color("ink_3")
    assert abs(darkest.red() - muted.red()) + abs(darkest.green() - muted.green()) <= 40
    view.close()


@pytest.mark.parametrize(("value", "peak", "room", "length"), [
    (100, 100, 200, SCORE_BAR_WIDTH),    # the largest score in view fills the bar
    (25, 100, 200, SCORE_BAR_WIDTH / 4),
    (0, 100, 200, 0),                    # a zero score draws no bar
    (1, 1000, 200, 2),                   # a tiny score still shows as a short stroke
    (100, 100, 30, 30),                  # the bar never runs under the number
    (5, 0, 200, 0),
])
def test_the_score_bar_is_scaled_to_the_largest_score_in_view(value, peak, room, length):
    assert score_bar_length(value, peak, room) == pytest.approx(length)


def _bar_pixels(overview: CasesOverview, proxy_row: int) -> int:
    """Width of the ink bar drawn in a row's score cell, counted along the cell's middle line."""
    image = overview.table.viewport().grab().toImage()
    cell = overview.table.visualRect(overview.table.model().index(proxy_row, SCORE_COLUMN))
    ink = token_color("ink_2")
    middle = cell.center().y()
    return sum(1 for x in range(cell.left(), cell.right())
               if abs(image.pixelColor(x, middle).red() - ink.red()) < 24
               and abs(image.pixelColor(x, middle).green() - ink.green()) < 24)


def test_score_bars_are_drawn_against_the_largest_score_that_is_in_view(qapp):
    view, overview = _workspace()
    # scores 50, 25, 0 in this order: the 25 bar is half the 50 bar
    assert _bar_pixels(overview, 0) == pytest.approx(SCORE_BAR_WIDTH, abs=2)
    assert _bar_pixels(overview, 1) == pytest.approx(SCORE_BAR_WIDTH / 2, abs=2)
    assert _bar_pixels(overview, 2) == 0
    overview.filter_edit.setText("cranio")     # only the 25 is left in view, so it now has the full bar
    assert _bar_pixels(overview, 0) == pytest.approx(SCORE_BAR_WIDTH, abs=2)
    view.close()


def test_the_current_row_has_the_selection_fill_and_an_accent_bar_at_its_left_edge(qapp):
    view, overview = _workspace()
    overview.table.selectRow(1)
    QApplication.processEvents()
    image = overview.table.viewport().grab().toImage()
    model = overview.table.model()
    selected = overview.table.visualRect(model.index(1, 0))
    other = overview.table.visualRect(model.index(0, 0))
    accent = QColor(tokens("light")["accent"])
    assert image.pixelColor(1, selected.center().y()) == accent
    assert image.pixelColor(1, other.center().y()) != accent
    assert image.pixelColor(selected.left() + 6, selected.top() + 3) == QColor(tokens("light")["sel"])
    view.close()


# -- click handling --------------------------------------------------------------------------------------


def _click(overview: CasesOverview, row: int, *, double: bool = False) -> None:
    table = overview.table
    center = table.visualRect(table.model().index(row, 1)).center()
    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center)
    if double:
        QTest.mouseDClick(table.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center)
        QTest.mouseRelease(table.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center)


def test_a_click_opens_the_case_today(qapp):
    view, overview = _workspace()
    opened = []
    overview.case_requested.connect(opened.append)
    _click(overview, 1)
    assert opened == [overview.cell_text(1, 0)]
    view.close()


def test_one_flag_makes_a_click_only_select_and_a_double_click_or_enter_open(qapp, monkeypatch):
    monkeypatch.setattr(cases_module._CasesTable, "CLICK_OPENS", False)
    view, overview = _workspace()
    opened = []
    overview.case_requested.connect(opened.append)
    _click(overview, 1)
    assert opened == [] and overview.table.currentIndex().row() == 1
    assert overview.preview.kicker.text() == f"Preview · {overview.cell_text(1, 0)}"
    overview.table.clearSelection()
    _click(overview, 2, double=True)
    assert opened == [overview.cell_text(2, 0)]
    QTest.keyClick(overview.table, Qt.Key.Key_Return)
    assert opened == [overview.cell_text(2, 0)] * 2
    view.close()


# -- the preview -----------------------------------------------------------------------------------------


def _preview_data() -> dict:
    flagged = [("pip_high", "warn", 15.0), ("made_up_rule", "info", 4.0), ("pip_rising", "critical", 10.0),
               ("peep_high", "warn", 8.0), ("pip_high", "info", 2.0), ("peep_zero_long", "info", 3.0),
               ("tv_low_mlkg", "warn", 1.0)]
    episodes = pd.concat([_episodes("SYN0000000001", flagged), _episodes("SYN0000000002", flagged[:1])])
    minutes = pd.concat([_minutes("SYN0000000001", [20.0, None, 22.0, 24.0]), _minutes("SYN0000000002", [30.0, 31.0])])
    return {"timeseries": minutes, "episodes": episodes}


def test_the_preview_follows_the_current_row_with_the_arrow_keys(qapp):
    view, overview = _workspace(**_preview_data())
    opened = []
    overview.case_requested.connect(opened.append)
    preview = overview.preview
    overview.table.setFocus()
    overview.table.selectRow(0)
    assert preview.kicker.text() == "Preview · SYN0000000001"
    assert preview.title.text() == "Total hip arthroplasty"
    assert preview.facts.text() == "120 min surgery · age 71 · F · 7 rule episodes"
    QTest.keyClick(overview.table, Qt.Key.Key_Down)
    assert preview.kicker.text() == "Preview · SYN0000000002"
    assert preview.title.text() == "Craniotomy"
    assert preview.facts.text() == "90 min surgery · age 22 · M · 1 rule episode"
    QTest.keyClick(overview.table, Qt.Key.Key_Up)
    assert preview.kicker.text() == "Preview · SYN0000000001"
    # a row that became current is only previewed: nothing was opened
    assert opened == []
    view.close()


def test_the_preview_draws_the_cases_traces_from_the_minute_data_in_memory(qapp):
    view, overview = _workspace(**_preview_data())
    traces = overview.preview.traces
    assert overview.preview.trace_box.isVisibleTo(overview) and not overview.preview.trace_note.isVisibleTo(overview)
    assert [traces[name].signal() for name in ("PIP", "ETCO2", "HR")] == [
        ("PIP", "s_pip"), ("ETCO₂", "s_etco2"), ("Heart rate", "s_hr")]
    # the first row's minutes, in minute order, with the missing minute left as a gap
    assert traces["PIP"].state()[1] == (20.0, None, 22.0, 24.0)
    assert traces["HR"].state()[1] == (70.0,) * 4
    assert traces["PIP"].accessibleDescription() == "PIP over 4 minutes, lowest 20, highest 24 cmH₂O, 1 minutes missing."
    view.close()


def test_the_preview_lists_episodes_worst_first_with_shape_word_and_minutes_and_a_limit(qapp):
    view, overview = _workspace(**_preview_data())
    rows = overview.preview.episode_list
    texts = [rows.item(i).text() for i in range(rows.count())]
    assert len(texts) == EPISODES_SHOWN + 1
    assert texts[:3] == ["◆ Rising PIP · critical · 10 min", f"△ {HIGH_PIP} · warn · 15 min",
                         "△ High PEEP · warn · 8 min"]
    # seven episodes, five shown, so two more
    assert texts[-1] == "and 2 more"
    assert rows.accessibleName() == "Rule episodes"
    view.close()


def test_the_preview_says_signals_show_when_opened_if_the_minutes_are_not_in_memory(qapp):
    scan_cases = _cases().assign(PID=["SCAN1", "SCAN2", "SCAN3"])
    scan = ScanResult(scan_cases, _episodes("SCAN1", [("pip_high", "warn", 12.0)]),
                      {"n_indexed_surgeries": 50, "preset": "default"})
    view, overview = _workspace(scan=scan, **_preview_data())
    overview.set_scope("scan")
    overview.table.selectRow(0)
    preview = overview.preview
    assert preview.kicker.text() == "Preview · SCAN1"
    assert not preview.trace_box.isVisibleTo(overview)
    assert preview.trace_note.isVisibleTo(overview) and preview.trace_note.text() == SIGNALS_ON_OPEN
    # the scan's own episodes still show
    assert preview.episode_list.item(0).text() == f"△ {HIGH_PIP} · warn · 12 min"
    view.close()


def test_without_minute_data_or_an_episode_table_the_panel_says_both_show_on_opening(qapp):
    view, overview = _workspace()
    assert overview.preview.trace_note.text() == SIGNALS_ON_OPEN
    assert overview.preview.episode_list.item(0).text() == "Rule episodes show when the case is opened."
    assert overview.preview.facts.text() == "120 min surgery · age 71 · F"
    view.close()


def test_a_case_with_no_episodes_says_so(qapp):
    view, overview = _workspace(timeseries=_minutes("SYN0000000001", [1.0]),
                                episodes=_episodes("SYN0000000002", [("pip_high", "warn", 5.0)]))
    assert overview.preview.episode_list.item(0).text() == "No rule episodes in this case."
    assert overview.preview.facts.text().endswith("F · no rule episodes")
    view.close()


def test_with_no_current_row_the_panel_says_what_to_do(qapp):
    view, overview = _workspace()
    overview.table.selectionModel().clear()
    overview.table.setCurrentIndex(QModelIndex())
    assert overview.preview.note.text() == NOTHING_TO_PREVIEW and not overview.preview.open_button.isEnabled()
    assert overview.preview.note.isVisibleTo(overview) and not overview.preview.title.isVisibleTo(overview)
    view.set_cases_data(None, None, load_rule_catalog("default"))
    assert overview.preview.note.text() == NO_CASES_TO_PREVIEW
    view.close()


def test_open_case_opens_the_previewed_case_and_a_new_current_row_starts_nothing(qapp, monkeypatch):
    view, overview = _workspace(**_preview_data())
    opened = []
    overview.case_requested.connect(opened.append)

    def forbidden(*args, **kwargs):
        raise AssertionError("moving through the rows read data")

    for owner, name in ((pd, "read_parquet"), (pd, "read_csv"), (pd, "read_feather")):
        monkeypatch.setattr(owner, name, forbidden)
    monkeypatch.setattr("src.services.case_fetch.fetch_cases", forbidden)
    monkeypatch.setattr("builtins.open", forbidden)
    overview.table.setFocus()
    for key in (Qt.Key.Key_Down, Qt.Key.Key_Down, Qt.Key.Key_Up):
        QTest.keyClick(overview.table, key)
    assert opened == []
    monkeypatch.undo()
    assert overview.preview.open_button.isEnabled()
    overview.preview.open_button.click()
    assert opened == [overview.cell_text(1, 0)]
    view.close()


def test_the_keys_line_names_only_keys_that_work(qapp):
    view, overview = _workspace()
    assert overview.preview.keys.text() == "Enter opens · ↑ ↓ moves · / filters · Esc goes back to the question"
    view.close()


# -- the preview's data source ---------------------------------------------------------------------------


def test_preview_source_gives_a_cases_minutes_in_order_and_none_when_it_is_not_there():
    ts = pd.DataFrame({"PID": ["A", "A", "A", "B"], "t_min": [2, 0, 1, 0], "PIP": [3.0, 1.0, None, 9.0],
                       "ETCO2": [1.0, 2.0, 3.0, 4.0]})
    traces = PreviewSource(ts).traces("A")
    assert traces is not None
    assert traces["PIP"] == (1.0, None, 3.0)
    assert traces["ETCO2"] == (2.0, 3.0, 1.0)
    # a column the frame lacks is all gaps, not an error
    assert traces["HR"] == (None, None, None)
    assert PreviewSource(ts).traces("C") is None
    assert PreviewSource(None).traces("A") is None


def test_preview_source_episodes_are_worst_first_and_unknown_differs_from_none():
    labels = {"a": "Rule A"}
    table = pd.DataFrame({"PID": ["X", "X", "X"], "rule_id": ["a", "b", "a"], "severity": ["info", "critical", "warn"],
                          "duration_min": [30.0, 2.0, 5.0]})
    source = PreviewSource(episodes=table)
    found = source.episodes("X", labels, scan=False)
    assert found is not None
    assert [(e.label, e.severity, e.minutes) for e in found] == [("b", "critical", 2.0), ("Rule A", "warn", 5.0),
                                                                 ("Rule A", "info", 30.0)]
    assert source.episodes("Y", labels, scan=False) == ()           # the table is loaded; this case has none
    assert source.episodes("X", labels, scan=True) is None          # no scan table at all
    assert PreviewSource().episodes("X", labels, scan=False) is None
    assert PreviewSource(scan_episodes=table).episodes("X", labels, scan=True) is not None


# -- layout ----------------------------------------------------------------------------------------------


def test_the_preview_sits_at_the_right_full_height_on_the_side_panel_ground(qapp):
    view, overview = _workspace(1316)
    preview = overview.preview
    assert overview._wide and preview.property("stacked") is False
    assert preview.width() == 336 and preview.x() + preview.width() == overview.width()
    assert preview.height() == overview.height() and preview.y() == 0
    assert preview.x() > overview.table.mapTo(overview, QPoint(0, 0)).x() + overview.table.width() - 1
    image = overview.grab().toImage()
    assert image.pixelColor(preview.x() + 100, preview.height() // 2) == QColor(tokens("light")["sheet"])
    view.close()


@pytest.mark.parametrize("width", [900, 700])
def test_a_narrow_page_puts_the_preview_under_the_list_and_never_scrolls_sideways(qapp, width):
    view, overview = _workspace(width)
    preview = overview.preview
    assert not overview._wide and preview.property("stacked") is True
    body_top = preview.mapTo(overview._scroll.widget(), QPoint(0, 0)).y()
    table_bottom = overview.table.mapTo(overview._scroll.widget(), QPoint(0, overview.table.height())).y()
    assert body_top >= table_bottom - 1
    assert preview.width() == overview._scroll.viewport().width()
    assert overview._scroll.horizontalScrollBar().maximum() == 0
    # everything on the page stays inside it
    for widget in (overview.filter_edit, overview.back_button, overview.table, preview.open_button):
        right = widget.mapTo(overview._scroll.widget(), widget.rect().bottomRight()).x()
        assert right <= overview._scroll.widget().width(), widget
    view.close()


def test_beside_the_open_rules_drawer_the_page_stacks_instead_of_scrolling_sideways(qapp):
    view, overview = _workspace(1300)
    assert overview._wide
    view.open_rules()
    QTest.qWait(50)
    assert overview.width() < 1300 - 200 and not overview._wide
    assert overview._scroll.horizontalScrollBar().maximum() == 0
    view.close()


def test_narrow_pages_stack_the_header_controls_and_the_three_charts(qapp):
    view, overview = _workspace(560)
    assert overview._header.direction().name == "TopToBottom"
    assert overview._charts.direction().name == "TopToBottom"
    assert overview.filter_edit.width() > 220
    view.close()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_page_paints_on_the_themes_own_grounds(qapp, theme):
    apply_theme(qapp, theme)
    try:
        view, overview = _workspace(1316)
        palette = tokens(theme)
        image = overview.grab().toImage()
        assert image.pixelColor(overview.width() // 2, 4) == QColor(palette["paper"])
        assert image.pixelColor(overview.preview.x() + 100, overview.height() // 2) == QColor(palette["sheet"])
        view.close()
    finally:
        apply_theme(qapp, "light")


# -- empty and message states ----------------------------------------------------------------------------


def test_the_empty_states_are_plain_sentences_without_a_frame(qapp):
    view, overview = _workspace(cases=None)
    assert overview.empty_label.text() == EMPTY_MESSAGE and overview.empty_label.isVisibleTo(overview)
    assert overview.empty_label.objectName() == "bodyText"
    assert not overview.table.isVisibleTo(overview) and not overview.charts_panel.isVisibleTo(overview)
    scan = ScanResult(pd.DataFrame(), pd.DataFrame(), {"preset": "default"})
    view.set_cases_data(None, scan, load_rule_catalog("default"))
    overview.set_scope("scan")
    assert overview.empty_label.text().startswith("The full-EMR scan scored no cases.")
    assert overview.summary_label.text() == "No cases in the full-EMR scan."
    view.close()


def test_a_message_shows_in_one_quiet_line_and_goes_when_cleared(qapp):
    view, overview = _workspace()
    assert not overview.status_label.isVisibleTo(overview)
    overview.set_message("Loading this case: SYN0000000001…")
    assert overview.status_label.isVisibleTo(overview)
    assert overview.status_label.text() == "Loading this case: SYN0000000001…"
    assert overview.status_label.objectName() == "quietText"
    overview.set_message("")
    assert not overview.status_label.isVisibleTo(overview)
    view.close()


# -- charts ----------------------------------------------------------------------------------------------


def test_the_score_chart_counts_cases_per_range_and_says_it_is_not_a_risk_score():
    cases = pd.DataFrame({"anomaly_score": [0, 1, 6, 7, 7, 12]})
    spec = score_spec(cases)
    assert spec.caption == SCORE_CAPTION
    assert "counts each flagged minute once at its worst severity and composites per episode" in spec.caption
    assert "count, not a risk score" in spec.caption
    # 6 cases, bins of 2 from 0: 0-2 holds two, 6-8 holds three, 12-14 holds one
    assert spec.description == (
        "Histogram of the anomaly score of 6 cases, from 0 to 12. Cases in each score range: "
        "0 to 2, 2; 2 to 4, 0; 4 to 6, 0; 6 to 8, 3; 8 to 10, 0; 10 to 12, 0; 12 to 14, 1.")


def test_the_rule_chart_counts_flagged_minutes_per_rule_and_names_how():
    flags = pd.DataFrame({"rule_id": ["a", "a", "a", "b", "b", "c"]})
    spec = rules_spec(flags, {"a": "Rule A"}, minutes_column=None)
    assert spec.description == "Most frequent rules by flagged minutes: Rule A, 3, b, 2, c, 1."
    assert spec.caption == "Minutes each rule flagged, summed over these cases. All 3 rules that flagged."
    episodes = pd.DataFrame({"rule_id": ["a", "a", "b"], "duration_min": [10.0, 5.5, 20.0]})
    assert rules_spec(episodes, {}, minutes_column="duration_min").description == (
        "Most frequent rules by flagged minutes: b, 20, a, 15.5.")
    many = pd.DataFrame({"rule_id": [f"r{i}" for i in range(8) for _ in range(8 - i)]})
    assert rules_spec(many, {}, minutes_column=None).caption == (
        "Minutes each rule flagged, summed over these cases. The 5 most frequent of 8 rules.")


def test_the_duration_chart_marks_the_median_and_says_what_a_tick_is():
    spec = duration_spec(pd.DataFrame({"case_duration_min": [60.0, 90.0, 96.0, 100.0, 300.0]}))
    assert spec.caption == "Each tick is one case. Median 96 min."
    assert spec.description == "Surgery duration of 5 cases, from 60 to 300 minutes. Median 96 minutes."


@pytest.mark.parametrize(("spec", "sentence"), [
    (rules_spec(None, {}, minutes_column=None), NO_FLAGGED_RULES),
    (rules_spec(pd.DataFrame({"rule_id": ["a"], "duration_min": [0.0]}), {}, minutes_column="duration_min"),
     NO_FLAGGED_RULES),
    (duration_spec(pd.DataFrame({"anomaly_score": [1]})), NO_DURATIONS),
    (duration_spec(pd.DataFrame({"case_duration_min": [None, None]})), NO_DURATIONS),
])
def test_a_chart_without_data_has_no_picture_and_a_sentence(spec, sentence):
    assert spec.build is None and spec.empty == sentence


def test_a_scope_without_durations_says_so_in_the_chart_place_and_the_chart_is_no_tab_stop(qapp):
    view, overview = _workspace(cases=_cases().drop(columns="case_duration_min"))
    block = overview.duration_block
    assert block.placeholder.isVisibleTo(overview) and not block.chart.isVisibleTo(overview)
    assert block.placeholder.text() == NO_DURATIONS
    assert block.chart.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert overview.score_block.chart.focusPolicy() == Qt.FocusPolicy.StrongFocus
    view.close()


def test_a_focused_chart_shows_the_accent_ring(qapp):
    view, overview = _workspace()
    chart = overview.score_block.chart
    chart.setFocus()
    QApplication.processEvents()
    image = chart.grab().toImage()
    assert image.pixelColor(1, chart.height() // 2) == QColor(tokens("light")["accent"])
    view.close()


# -- accessibility and the page's own colours --------------------------------------------------------------


def test_every_control_on_the_page_has_an_accessible_name(qapp):
    scan = ScanResult(_cases().iloc[:1].assign(PID="SCAN1"), pd.DataFrame(), {"n_indexed_surgeries": 9})
    view, overview = _workspace(scan=scan, **_preview_data())
    controls = [w for w in overview.findChildren(QWidget)
                if w.focusPolicy() != Qt.FocusPolicy.NoFocus and w.isVisibleTo(overview)]
    assert {w.objectName() for w in controls} >= {"linkBtn", "scopeOption", "casesFilter", "summaryChart",
                                                  "casesTable", "primaryBtn"}
    for widget in controls:
        text = widget.text() if hasattr(widget, "text") else ""
        assert widget.accessibleName() or text, widget
    view.close()


def test_the_cases_page_carries_no_colors_of_its_own(qapp):
    view, overview = _workspace()
    assert overview.styleSheet() == ""
    pattern = r"#[0-9a-fA-F]{3,8}\b|QColor\(\"|rgba?\(\d"
    for name in ("cases_overview.py", "cases_preview.py", "cases_summary.py"):
        assert re.findall(pattern, (SRC / name).read_text(encoding="utf-8")) == [], name
    source = (SRC / "charts.py").read_text(encoding="utf-8")
    lines = source.splitlines()
    names = {"score_histogram_figure", "rule_minutes_figure", "duration_ticks_figure", "_summary_canvas", "_label",
             "_elide", "histogram_edges", "tick_values", "_nice_step", "_count_text"}
    spans = [(n.lineno, n.end_lineno) for n in ast.parse(source).body
             if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
    assert len(spans) == len(names), "a summary-chart function was renamed; update this list"
    for start, end in spans:
        assert re.findall(pattern, "\n".join(lines[start - 1:end])) == [], lines[start - 1]
    view.close()
