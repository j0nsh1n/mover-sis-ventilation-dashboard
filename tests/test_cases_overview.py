"""Cases overview inside the research workspace (offscreen Qt, synthetic data only)."""

from __future__ import annotations

import os
import threading
import time
from unittest.mock import Mock

import pandas as pd
import pytest

from src.services.case_fetch import FetchResult

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from src.desktop.cases_overview import COLUMNS, EMPTY_MESSAGE, SCORE_COLUMN
from src.desktop.research_workspace import CASES_PAGE, ResearchWorkspace
from src.pipeline.corpus_scan import ScanResult
from src.services.retrieval import RetrievalResult, SearchCandidate
from src.rules import load_rule_catalog


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _cases(scores=(9, 100, 25)) -> pd.DataFrame:
    rows = []
    for i, score in enumerate(scores):
        rows.append({
            "PID": f"SYN{i:010x}",
            "Age": 40 + i,
            "Gender": "F" if i % 2 else "M",
            "Procedure_short": f"Synthetic procedure {i}",
            "case_duration_min": 60.0 + i,
            "anomaly_score": score,
            "n_warn": i,
            "n_critical": 2 * i,
            "top_rules": "pip_high(12), made_up_rule(3)",
        })
    return pd.DataFrame(rows)


def _scan(n: int = 4, **meta) -> ScanResult:
    frame = _cases(tuple(range(1, n + 1)))
    frame["PID"] = [f"SCAN{i:010x}" for i in range(n)]
    base = {"n_indexed_surgeries": 40, "n_ventilated_surgeries": 8, "n_scanned_cases": n,
            "failed_shards": [], "preset": "default"}
    return ScanResult(frame, pd.DataFrame(), {**base, **meta})


def _workspace(cases=None, scan=None, preset="default") -> ResearchWorkspace:
    view = ResearchWorkspace()
    view.resize(1000, 700)
    view.show()
    view.set_cases_data(cases, scan, load_rule_catalog(preset))
    return view


def _first_column(view: ResearchWorkspace, count: int = 3) -> list[str]:
    overview = view._cases_overview
    return [overview.cell_text(row, 0) for row in range(count)]


def _scores(view: ResearchWorkspace) -> list[str]:
    overview = view._cases_overview
    return [overview.cell_text(row, SCORE_COLUMN) for row in range(overview._proxy.rowCount())]


def test_overview_is_not_built_until_opened(qapp):
    view = _workspace(_cases())
    assert view._cases_overview is None
    assert view.stack.count() == 2
    view.cases_button.click()
    assert view._cases_overview is not None
    assert view.stack.currentIndex() == CASES_PAGE


def test_scope_text_for_sample_scan_and_empty_states(qapp):
    view = _workspace(_cases())
    view.show_cases()
    overview = view._cases_overview
    assert overview.scope_label.text() == "Analyzed cases, 3 (loaded sample) · with flags: 2 · max score: 100"
    assert not overview.scope_combo.isVisibleTo(overview)

    view.set_cases_data(_cases(), _scan(4), load_rule_catalog("default"))
    assert overview.scope_label.text() == "Analyzed cases, 3 of 40 indexed surgeries (loaded sample) · with flags: 2 · max score: 100"
    assert overview.scope_combo.isVisibleTo(overview)
    assert overview.scope_combo.itemText(1) == "Full-EMR scan, 4 scored, 32 without ventilator data"

    overview.scope_combo.setCurrentIndex(1)
    assert overview.scope_label.text() == "Full-EMR scan, 4 scored, 32 without ventilator data · with flags: 3 · max score: 4"
    assert [overview.cell_text(r, 0)[:4] for r in range(4)] == ["SCAN"] * 4

    view.set_cases_data(None, None, load_rule_catalog("default"))
    assert overview.scope_label.text() == "Analyzed cases, 0 (loaded sample) · with flags: 0 · max score: —"
    assert overview.empty_label.text() == EMPTY_MESSAGE
    assert overview.empty_label.isVisibleTo(overview)
    assert not overview.table.isVisibleTo(overview)


def test_scan_scope_reports_unscored_shards_and_preset_mismatch(qapp):
    scan = _scan(4, failed_shards=[{"shard": 1, "n_pids": 3, "error": "x"}], preset="strict")
    view = _workspace(_cases(), scan)
    view.show_cases()
    overview = view._cases_overview
    overview.scope_combo.setCurrentIndex(1)
    text = overview.scope_label.text()
    assert "3 could not be scored" in text
    assert "scanned with preset strict" in text


def test_sorting_by_score_is_numeric_in_both_directions(qapp):
    view = _workspace(_cases((9, 100, 25)))
    view.show_cases()
    overview = view._cases_overview
    assert _scores(view) == ["100", "25", "9"]
    overview.table.sortByColumn(SCORE_COLUMN, Qt.SortOrder.AscendingOrder)
    assert _scores(view) == ["9", "25", "100"]
    overview.table.sortByColumn(SCORE_COLUMN, Qt.SortOrder.DescendingOrder)
    assert _scores(view) == ["100", "25", "9"]


def test_missing_columns_show_empty_cells(qapp):
    frame = _cases().drop(columns=["Age", "top_rules"])
    view = _workspace(frame)
    view.show_cases()
    overview = view._cases_overview
    age_column = next(i for i, c in enumerate(COLUMNS) if c.key == "Age")
    rules_column = next(i for i, c in enumerate(COLUMNS) if c.key == "top_rules")
    assert {overview.cell_text(r, age_column) for r in range(3)} == {""}
    assert {overview.cell_text(r, rules_column) for r in range(3)} == {""}


def test_click_and_enter_request_the_right_case(qapp):
    view = _workspace(_cases((9, 100, 25)))
    requests = []
    view.case_requested.connect(lambda *args: requests.append(args))
    view.show_cases()
    overview = view._cases_overview
    table = overview.table
    viewport = table.viewport()

    # Sorted by score descending: row 1 is the score-25 case
    pid_at_row_1 = overview.cell_text(1, 0)
    center = table.visualRect(table.model().index(1, 0)).center()
    QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center)
    assert requests == [(pid_at_row_1, False, True)]

    requests.clear()
    table.setFocus()
    table.setCurrentIndex(table.model().index(2, 0))
    QTest.keyClick(table, Qt.Key.Key_Return)
    assert requests == [(overview.cell_text(2, 0), False, True)]

    requests.clear()
    QTest.keyClick(table, Qt.Key.Key_Up)
    assert table.currentIndex().row() == 1
    QTest.keyClick(table, Qt.Key.Key_Enter)
    assert requests == [(overview.cell_text(1, 0), False, True)]


def test_back_button_and_escape_return_focus_to_the_cases_button(qapp):
    view = _workspace(_cases())
    view.show_cases()
    view._cases_overview.back_button.click()
    assert view.stack.currentIndex() == 0
    assert view.window().focusWidget() is view.cases_button

    view.show_cases()
    QTest.keyClick(view._cases_overview.table, Qt.Key.Key_Escape)
    assert view.stack.currentIndex() == 0
    assert view.window().focusWidget() is view.cases_button


def test_rule_labels_come_from_the_loaded_presets_catalog(qapp):
    view = _workspace(_cases())
    view.show_cases()
    overview = view._cases_overview
    rules_column = next(i for i, c in enumerate(COLUMNS) if c.key == "top_rules")
    label = load_rule_catalog("default").get("pip_high").label
    assert overview.cell_text(0, rules_column) == f"{label} (12), made_up_rule (3)"

    view.set_cases_data(_cases(), None, None)
    assert overview.cell_text(0, rules_column) == "pip_high (12), made_up_rule (3)"


def test_refresh_after_reload_drops_old_rows(qapp):
    view = _workspace(_cases((9, 100, 25)), _scan(4))
    view.show_cases()
    overview = view._cases_overview
    overview.scope_combo.setCurrentIndex(1)
    assert overview._proxy.rowCount() == 4

    fresh = _cases((5, 6))
    fresh["PID"] = ["SYNnew0000001", "SYNnew0000002"]
    view.set_cases_data(fresh, None, load_rule_catalog("default"))
    assert overview.scope() == "sample"
    assert sorted(_first_column(view, 2)) == ["SYNnew0000001", "SYNnew0000002"]
    assert overview._proxy.rowCount() == 2
    assert not overview.scope_combo.isVisibleTo(overview)


def test_reload_keeps_the_overview_open_but_leaves_a_case(qapp):
    view = _workspace(_cases())
    view.show_cases()
    view.invalidate_case_data()
    assert view.stack.currentIndex() == CASES_PAGE


def test_overview_case_opens_f_and_returns_to_the_overview(qapp):
    view = _workspace(_cases((9, 100, 25)))
    view.show_cases()
    overview = view._cases_overview
    pid = overview.cell_text(0, 0)
    view._open_overview_case(pid)
    series = pd.DataFrame({"t_min": [0, 1], "PIP": [20.0, 30.0], "ETCO2": [35.0, 36.0], "HR": [70, 71]})
    view.set_case_data(pid, series, pd.DataFrame(), open_detail=True)
    assert view.stack.currentIndex() == 1
    assert pid in view.detail_title.text()
    assert view.back_button.text() == "← Back to cases"
    assert view._primary_pid is None
    view.back_button.click()
    assert view.stack.currentIndex() == CASES_PAGE


class _GatedFetch(QThread):
    loaded = Signal(object)
    failed = Signal(str)
    gate = threading.Event()
    started_for: list[str] = []

    def __init__(self, pid, emr, preset="default", parent=None):
        super().__init__(parent)
        self.pid = pid

    def run(self):
        type(self).started_for.append(self.pid)
        self.gate.wait(2)
        series = pd.DataFrame({"t_min": [0, 1], "PIP": [20.0, 30.0], "ETCO2": [35.0, 36.0], "HR": [70, 71]})
        self.loaded.emit(FetchResult(timeseries=series))


def _pump(app, predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()
    assert predicate()


def test_scan_only_case_goes_through_the_fetch_path(qapp, monkeypatch, tmp_path):
    from src.desktop.app import MainWindow

    monkeypatch.setenv("MOVER_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("MOVER_PROCESSED_DIR", str(tmp_path / "processed"))
    monkeypatch.setenv("MOVER_EMR_DIR", str(tmp_path / "EMR"))
    monkeypatch.setattr(MainWindow, "_startup", lambda self: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: None)
    monkeypatch.setattr("src.desktop.app.CaseFetchWorker", _GatedFetch)
    _GatedFetch.gate = threading.Event()
    _GatedFetch.started_for = []
    win = MainWindow()
    try:
        sample = _cases((9, 100, 25))
        win.cases = sample
        win.corpus_scan = _scan(4)
        win._push_cases_overview()
        win.research.show_cases()
        overview = win.research._cases_overview
        overview.scope_combo.setCurrentIndex(1)
        scan_pid = overview.cell_text(0, 0)
        assert scan_pid.startswith("SCAN")
        overview.table.row_activated.emit(0)
        _pump(qapp, lambda: _GatedFetch.started_for == [scan_pid])
        assert win.research.status_label.text() == f"Loading this case: {scan_pid}…"
        assert overview.status_label.text() == f"Loading this case: {scan_pid}…"
        _GatedFetch.gate.set()
        _pump(qapp, lambda: win.research.stack.currentIndex() == 1)
        assert scan_pid in win.research.detail_title.text()
    finally:
        _GatedFetch.gate.set()
        if win._case_worker is not None:
            win._case_worker.wait(3000)
        win.close()


def test_data_reload_and_preset_change_push_fresh_data(qapp, monkeypatch, tmp_path):
    from src.desktop.app import MainWindow

    monkeypatch.setenv("MOVER_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("MOVER_PROCESSED_DIR", str(tmp_path / "processed"))
    monkeypatch.setattr(MainWindow, "_startup", lambda self: None)
    win = MainWindow()
    try:
        win.cases = _cases((9, 100, 25))
        win._data_changed()
        win.research.show_cases()
        overview = win.research._cases_overview
        assert overview._proxy.rowCount() == 3
        win.cases = _cases((1,))
        win._set_loaded_preset("strict", recorded=True)
        assert overview._proxy.rowCount() == 1
        assert overview._preset == "strict"
        win.corpus_scan = _scan(5)
        win._on_corpus_scan_ok(win.corpus_scan)
        assert overview.scope_combo.isVisibleTo(overview)
    finally:
        win.close()


def test_overview_opens_quickly_on_a_600_case_scan(qapp):
    big = _scan(600)
    view = _workspace(_cases(), big)
    view.show()
    QApplication.processEvents()
    started = time.perf_counter()
    view.show_cases()
    view._cases_overview.scope_combo.setCurrentIndex(1)
    QApplication.processEvents()
    elapsed = time.perf_counter() - started
    assert view._cases_overview._proxy.rowCount() == 600
    assert elapsed < 0.3


def test_table_fits_a_900_px_window(qapp):
    view = ResearchWorkspace()
    view.resize(900, 700)
    view.show()
    view.set_cases_data(_cases(), None, load_rule_catalog("default"))
    view.show_cases()
    QApplication.processEvents()
    overview = view._cases_overview
    assert overview.width() <= 900
    assert overview.table.width() <= 900
    assert overview.back_button.isVisible()


class _ManualFetch(QThread):
    loaded = Signal(object)
    failed = Signal(str)

    def __init__(self, pid, emr, preset="default", parent=None):
        super().__init__(parent)
        self.pid = pid
        self.running = False

    def start(self):
        self.running = True

    def isRunning(self):
        return self.running

    def complete(self, *, failure=None):
        self.running = False
        if failure is None:
            self.loaded.emit(FetchResult(timeseries=_series()))
        else:
            self.failed.emit(failure)
        self.finished.emit()


def _series():
    return pd.DataFrame({"t_min": [0, 1], "PIP": [20.0, 30.0],
                         "ETCO2": [35.0, 36.0], "HR": [70, 71]})


@pytest.fixture
def request_window(qapp, monkeypatch, tmp_path):
    from src.desktop.app import MainWindow

    monkeypatch.setenv("MOVER_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("MOVER_PROCESSED_DIR", str(tmp_path / "processed"))
    monkeypatch.setenv("MOVER_EMR_DIR", str(tmp_path / "EMR"))
    monkeypatch.setattr(MainWindow, "_startup", lambda self: None)
    monkeypatch.setattr("src.desktop.app.CaseFetchWorker", _ManualFetch)
    win = MainWindow()
    win.show()
    view = win.research
    cases = _cases((9, 100, 25))
    view.set_cases_data(cases, None, load_rule_catalog("default"))
    pids = list(cases["PID"])
    view.set_results(RetrievalResult(candidates=tuple(
        SearchCandidate(pid=pid, source_label="indexed surgery", analyzed=False, age=40, gender="M",
                        procedure="Synthetic procedure",
                        source_text=f"PID {pid}", rank_method="keyword", score=0.5)
        for pid in pids[:2]
    )))
    view.question_edit.setText("Where is peak pressure?")
    view.set_answer("The question's answer stays here.")
    view.set_case_data(pids[0], _series(), pd.DataFrame())
    view.show_cases()
    try:
        yield win, pids
    finally:
        for worker in win.findChildren(_ManualFetch):
            worker.running = False
        win.close()


def _board_state(view):
    return (view._primary_pid, view._comparison_pid, view.question_edit.text(),
            view.answer_text.toPlainText(), view.finding_title.text(), view.finding_copy.text(),
            view.source_title.text(), view.source_label.text(), view.comparison_label.text(),
            view.comparison_card.isHidden(), view.comparison_hint.isHidden(),
            view.spark_label.pixmap().cacheKey())


def test_overview_shortlist_open_preserves_the_question_board(request_window):
    win, pids = request_window
    view = win.research
    view.set_case_data(pids[0], _series(), pd.DataFrame(), comparison=True)
    before = _board_state(view)
    view._open_overview_case(pids[1])
    win._case_worker.complete()
    assert _board_state(view) == before
    assert pids[1] in view.detail_title.text()
    assert view.back_button.text() == "← Back to cases"


def test_overview_failure_preserves_the_question_source(request_window):
    win, pids = request_window
    view = win.research
    before = _board_state(view)
    view._open_overview_case(pids[2])
    win._case_worker.complete(failure="Synthetic fetch failed")
    assert _board_state(view) == before
    expected = f"Source unavailable for {pids[2]}: Synthetic fetch failed"
    assert view.status_label.text() == expected
    assert view._cases_overview.status_label.text() == expected
    view.close_cases()
    view._open_or_request(pids[2], comparison=False)
    win._case_worker.complete(failure="Question fetch failed")
    assert view.source_title.text() == pids[2]
    assert view.source_label.text() == "Question fetch failed"


def test_queued_overview_opens_keep_their_return_destination(request_window):
    win, pids = request_window
    view = win.research
    before = _board_state(view)
    view._open_overview_case(pids[1])
    first = win._case_worker
    view._open_overview_case(pids[2])
    first.complete()
    assert pids[1] in view.detail_title.text()
    assert view.back_button.text() == "← Back to cases"
    second = win._case_worker
    assert second is not first
    second.complete()
    assert _board_state(view) == before
    assert pids[2] in view.detail_title.text()
    assert view.back_button.text() == "← Back to cases"
    view.back_button.click()
    assert view.stack.currentIndex() == CASES_PAGE
    assert view.window().focusWidget() is view._cases_overview.table
    view._open_overview_case(pids[1])
    assert view.back_button.text() == "← Back to cases"
    assert _board_state(view) == before
    view._open_or_request(pids[1], comparison=False)
    assert view.back_button.text() == "← Back to evidence workspace"


@pytest.mark.parametrize("overview_first", [False, True])
@pytest.mark.parametrize("comparison", [False, True])
def test_overlapping_same_pid_opens_follow_each_request_origin(request_window, overview_first, comparison):
    win, pids = request_window
    view = win.research
    before = _board_state(view)
    def overview_open():
        view._open_overview_case(pids[1])

    def question_open():
        view._select_candidate(1)
        button = view.compare_button if comparison else view.inspect_button
        button.click()

    first_open, second_open = (overview_open, question_open) if overview_first else (question_open, overview_open)
    first_open()
    first = win._case_worker
    second_open()
    first.complete()
    if overview_first or not comparison:
        assert view.back_button.text() == ("← Back to cases" if overview_first else "← Back to evidence workspace")
    else:
        assert view.stack.currentIndex() == CASES_PAGE
    if overview_first:
        assert _board_state(view) == before
    after_first = _board_state(view)
    win._case_worker.complete()
    question_detail = overview_first and not comparison
    assert view.back_button.text() == ("← Back to evidence workspace" if question_detail else "← Back to cases")
    if not overview_first:
        assert _board_state(view) == after_first
    assert (view._comparison_pid if comparison else view._primary_pid) == pids[1]
    view.back_button.click()
    assert view.stack.currentIndex() == (0 if question_detail else CASES_PAGE)


def test_tab_leaves_the_table_so_the_whole_overview_is_reachable(qapp):
    view = _workspace(_cases((9, 100, 25)), _scan(4))
    view.show_cases()
    overview = view._cases_overview
    assert not overview.table.tabKeyNavigation()
    table, combo, back = overview.table, overview.scope_combo, overview.back_button
    window = view.window()

    table.setFocus()
    row = table.currentIndex().row()
    QTest.keyClick(table, Qt.Key.Key_Down)
    assert table.currentIndex().row() == row + 1
    focus_before = window.focusWidget()
    assert focus_before is table
    QTest.keyClick(table, Qt.Key.Key_Tab)
    assert window.focusWidget() is not table
    assert table.currentIndex().row() == row + 1

    reached = set()
    for key, modifier in ((Qt.Key.Key_Tab, Qt.KeyboardModifier.NoModifier),
                          (Qt.Key.Key_Backtab, Qt.KeyboardModifier.ShiftModifier)):
        table.setFocus()
        for _ in range(8):
            QTest.keyClick(window.focusWidget(), key, modifier)
            reached.add(window.focusWidget())
            if {back, combo, table} <= reached:
                break
    assert {back, combo, table} <= reached


def test_escape_in_f_returns_to_the_overview_with_the_row_selected(qapp):
    view = _workspace(_cases((9, 100, 25)))
    view.show_cases()
    overview = view._cases_overview
    pid = overview.cell_text(1, 0)
    view._open_overview_case(pid)
    view.set_case_data(pid, _series(), pd.DataFrame(), open_detail=True, from_overview=True)
    assert view.stack.currentIndex() == 1
    assert QTest.qWaitForWindowActive(view)
    QTest.keyClick(view.window().focusWidget() or view, Qt.Key.Key_Escape)
    assert view.stack.currentIndex() == CASES_PAGE
    assert view.window().focusWidget() is overview.table
    assert overview.table.currentIndex().row() >= 0


def test_escape_in_f_from_the_question_returns_to_the_canvas(request_window):
    win, pids = request_window
    view = win.research
    view.close_cases()
    view._open_or_request(pids[0], comparison=False)
    assert view.stack.currentIndex() == 1
    assert QTest.qWaitForWindowActive(win)
    QTest.keyClick(view.window().focusWidget(), Qt.Key.Key_Escape)
    assert view.stack.currentIndex() == 0
    assert view.status_label.text() == "Returned to the same question and comparison."


def test_double_click_opens_the_case_once(qapp):
    view = _workspace(_cases((9, 100, 25)))
    requests = []
    view.case_requested.connect(lambda *args: requests.append(args))
    view.show_cases()
    overview = view._cases_overview
    table = overview.table
    center = table.visualRect(table.model().index(1, 0)).center()
    pid = overview.cell_text(1, 0)

    # What Qt delivers for a real double-click: press, release, double-click, release
    for send in (QTest.mouseClick, QTest.mouseDClick, QTest.mouseRelease):
        send(table.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center)
    assert requests == [(pid, False, True)]

    # A later single click is a new action
    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center)
    assert requests == [(pid, False, True)] * 2


def test_equal_scores_break_ties_by_pid_ascending_in_both_directions(qapp):
    frame = _cases((50, 50, 50, 7, 90, 50))
    frame["PID"] = ["SYNe", "SYNb", "SYNd", "SYNa", "SYNc", "SYNf"]
    frame["n_warn"] = [1, 1, 1, 1, 1, 1]
    view = _workspace(frame)
    view.show_cases()
    overview = view._cases_overview
    pids = lambda: [overview.cell_text(r, 0) for r in range(6)]  # noqa: E731

    for _ in range(2):
        overview.table.sortByColumn(SCORE_COLUMN, Qt.SortOrder.DescendingOrder)
        assert pids() == ["SYNc", "SYNb", "SYNd", "SYNe", "SYNf", "SYNa"]
        overview.table.sortByColumn(SCORE_COLUMN, Qt.SortOrder.AscendingOrder)
        assert pids() == ["SYNa", "SYNb", "SYNd", "SYNe", "SYNf", "SYNc"]

    warn_column = next(i for i, c in enumerate(COLUMNS) if c.key == "n_warn")
    overview.table.sortByColumn(warn_column, Qt.SortOrder.DescendingOrder)
    assert pids() == ["SYNa", "SYNb", "SYNc", "SYNd", "SYNe", "SYNf"]


def _luminance(rgb: int) -> float:
    def channel(value: int) -> float:
        value /= 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel((rgb >> 16) & 255) + 0.7152 * channel((rgb >> 8) & 255) + 0.0722 * channel(rgb & 255)


def _contrast(first: int, second: int) -> float:
    high, low = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_sort_arrow_is_visible_in_both_app_themes(qapp, theme):
    from src.desktop.theme import apply_theme

    apply_theme(qapp, theme)
    try:
        view = _workspace(_cases())
        view.show_cases()
        QApplication.processEvents()
        header = view._cases_overview.table.horizontalHeader()
        with_arrow = header.grab().toImage()
        header.setSortIndicatorShown(False)
        QApplication.processEvents()
        without_arrow = header.grab().toImage()
        background = without_arrow.pixel(without_arrow.width() - 2, 2)
        painted = [
            with_arrow.pixel(x, y) & 0xFFFFFF
            for y in range(with_arrow.height()) for x in range(with_arrow.width())
            if with_arrow.pixel(x, y) != without_arrow.pixel(x, y)
        ]
        assert painted, "no sort arrow was painted"
        assert max(_contrast(color, background & 0xFFFFFF) for color in painted) >= 3.0
    finally:
        apply_theme(qapp, "light")


def _column(key: str) -> int:
    return next(i for i, column in enumerate(COLUMNS) if column.key == key)


def test_agent_and_median_columns_show_their_own_values(qapp):
    cases = _cases((9, 100)).assign(primary_agent_name=["sevoflurane", "desflurane"],
                                    median_TV=[410.5, 505.0], median_PIP=[18.0, 24.5],
                                    median_ETCO2=[33.0, 41.5])
    view = _workspace(cases)
    view.show_cases()
    overview = view._cases_overview
    titles = {column.key: column.title for column in COLUMNS}
    assert [titles[key] for key in ("primary_agent_name", "median_TV", "median_PIP", "median_ETCO2")] == [
        "Primary agent", "Median TV (mL)", "Median PIP (cmH₂O)", "Median ETCO₂ (mmHg)"]
    rows = {overview.cell_text(row, 0): row for row in range(2)}
    first, second = rows["SYN0000000000"], rows["SYN0000000001"]
    assert [overview.cell_text(first, _column(key)) for key in
            ("primary_agent_name", "median_TV", "median_PIP", "median_ETCO2")] == [
        "sevoflurane", "410.5", "18", "33"]
    assert [overview.cell_text(second, _column(key)) for key in
            ("primary_agent_name", "median_TV", "median_PIP", "median_ETCO2")] == [
        "desflurane", "505", "24.5", "41.5"]


@pytest.mark.parametrize(("warn", "critical", "flagged"), [
    ([0, 0, 0], [0, 0, 0], 0),
    ([1, 0, 0], [0, 0, 0], 1),
    ([0, 0, 0], [0, 1, 0], 1),
    ([1, 0, 1], [0, 1, 0], 3),
    ([1, 1, 0], [1, 0, 0], 2),
])
def test_with_flags_counts_cases_with_one_or_more_flagged_minutes(qapp, warn, critical, flagged):
    cases = _cases((9, 100, 25)).assign(n_warn=warn, n_critical=critical)
    view = _workspace(cases)
    view.show_cases()
    assert f"with flags: {flagged} ·" in view._cases_overview.scope_label.text()


def test_rule_chart_only_counts_flags_of_the_cases_in_scope(qapp, monkeypatch):
    import src.desktop.cases_overview as module

    rules = Mock(wraps=module.flag_rules_figure)
    monkeypatch.setattr(module, "flag_rules_figure", rules)
    cases = _cases((9, 100))
    scan = _scan(2)
    flags = pd.DataFrame({"PID": [cases.PID[0], cases.PID[1], "OTHER", scan.cases.PID[0]],
                          "rule_id": ["pip_high"] * 4, "severity": ["warn"] * 4})
    episodes = flags.assign(PID=[scan.cases.PID[0], "OTHER", scan.cases.PID[1], cases.PID[0]], duration_min=3)
    view = _workspace(cases, ScanResult(scan.cases, episodes, scan.meta))
    view.set_cases_data(cases, ScanResult(scan.cases, episodes, scan.meta), load_rule_catalog("default"), flags)
    view.show_cases()
    overview = view._cases_overview
    overview.charts_button.click()
    assert sorted(rules.call_args.args[0]["PID"]) == sorted([cases.PID[0], cases.PID[1]])
    overview.charts_button.click()
    overview.scope_combo.setCurrentIndex(1)
    overview.charts_button.click()
    assert sorted(rules.call_args.args[0]["PID"]) == sorted([scan.cases.PID[0], scan.cases.PID[1]])


@pytest.mark.parametrize("width", [900, 1400, 1920])
def test_charts_fit_their_areas_without_cropping(qapp, width):
    rule_ids = [spec.id for spec in load_rule_catalog().specs][:20]
    cases = _cases(tuple(range(1, 19)))
    flags = pd.DataFrame({"PID": [cases.PID[i % 18] for i in range(len(rule_ids))], "rule_id": rule_ids,
                          "severity": ["warn"] * len(rule_ids)})
    view = ResearchWorkspace()
    view.resize(width, 900)
    view.show()
    view.set_cases_data(cases, None, load_rule_catalog("default"), flags)
    view.show_cases()
    overview = view._cases_overview
    overview.charts_button.click()
    QTest.qWait(250)
    for chart in (overview.top_chart, overview.rules_chart):
        viewport = chart._scroll.viewport()
        assert chart._scroll.verticalScrollBar().maximum() == 0
        assert chart._scroll.horizontalScrollBar().maximum() == 0
        assert 0 < chart._label.height() <= viewport.height()
        assert 0 < chart._label.width() <= viewport.width()
        assert chart._label.height() >= viewport.height() * 0.8
    view.close()


def test_charts_render_again_for_a_new_area(qapp, monkeypatch):
    import src.desktop.cases_overview as module

    top = Mock(wraps=module.top_cases_figure)
    monkeypatch.setattr(module, "top_cases_figure", top)
    view = ResearchWorkspace()
    view.resize(900, 900)
    view.show()
    view.set_cases_data(_cases(), None, load_rule_catalog("default"))
    view.show_cases()
    overview = view._cases_overview
    overview.charts_button.click()
    QTest.qWait(250)
    before = top.call_count
    first = top.call_args.kwargs["figsize"]
    view.resize(1500, 900)
    QTest.qWait(300)
    assert top.call_count > before
    assert top.call_args.kwargs["figsize"][0] > first[0] * 1.3
    view.close()


def test_chart_areas_are_named_and_tab_order_follows_the_page(qapp):
    view = _workspace(_cases())
    view.show_cases()
    overview = view._cases_overview
    overview.charts_button.click()
    assert overview.top_chart.accessibleName() == "Top cases by anomaly score chart"
    assert overview.rules_chart.accessibleName() == "Flag counts by rule chart"
    window = view.window()
    overview.back_button.setFocus()
    order = [window.focusWidget()]
    for _ in range(4):
        QTest.keyClick(window.focusWidget(), Qt.Key.Key_Tab)
        order.append(window.focusWidget())
    assert order == [overview.back_button, overview.charts_button, overview.top_chart._scroll,
                     overview.rules_chart._scroll, overview.table]
