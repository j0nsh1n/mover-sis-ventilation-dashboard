"""The research page's question block, local model menu, evidence trail and conversation panel."""

from __future__ import annotations

import os

import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QPushButton

from src.desktop.app import MainWindow
from src.desktop.command_palette import ACTION_ROLE, HINT_ROLE
from src.desktop.research_workspace import ResearchWorkspace
from src.desktop.theme import apply_theme
from src.llm.service import OllamaStatus
from src.services.retrieval import RetrievalResult, SearchCandidate


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def view(qapp):
    apply_theme(qapp, "light")
    workspace = ResearchWorkspace()
    workspace.resize(1316, 800)
    workspace.show()
    qapp.processEvents()
    yield workspace
    workspace.close()
    workspace.deleteLater()
    qapp.processEvents()


@pytest.fixture
def window(qapp, monkeypatch, tmp_path):
    monkeypatch.setenv("MOVER_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("MOVER_PROCESSED_DIR", str(tmp_path / "processed"))
    monkeypatch.setattr(MainWindow, "_startup", lambda self: None)
    win = MainWindow()
    win.show()
    qapp.processEvents()
    yield win
    win.close()
    qapp.processEvents()


def candidate(pid: str, procedure: str = "Laparoscopic procedure", label: str = "analyzed case") -> SearchCandidate:
    return SearchCandidate(
        pid=pid, source_label=label, analyzed=label == "analyzed case", procedure=procedure,
        age=55, gender="M", source_text=pid, rank_method="keyword", score=0.5,
    )


def series(peak_at: int = 3, missing_etco2_at: int | None = 4) -> pd.DataFrame:
    frame = pd.DataFrame({
        "t_min": range(8), "PIP": [20.0, 21.0, 22.0, 31.4, 23.0, 22.0, 21.0, 20.0],
        "ETCO2": [35.0] * 8, "HR": [70.0] * 8,
    })
    frame.loc[peak_at, "PIP"] = 31.4
    if missing_etco2_at is not None:
        frame.loc[missing_etco2_at, "ETCO2"] = float("nan")
    return frame


def board_with_case(view, count: int = 5) -> None:
    pids = [f"SYN-{i:03d}" for i in range(count)]
    view.set_results(RetrievalResult(
        candidates=tuple(candidate(pid, label="analyzed case" if i < 2 else "indexed surgery")
                         for i, pid in enumerate(pids)),
        indexed_count=19114, analyzed_count=28,
    ))
    view.set_case_data(pids[0], series(), pd.DataFrame())


# ----- question block -----


def test_the_question_is_the_largest_text_and_set_in_the_serif_face(view, qapp):
    """The top question field is gone. The ask box is the composer under the thread."""
    qapp.processEvents()
    assert view.findChildren(QLineEdit, "questionField") == []
    assert [edit for edit in view.findChildren(QLineEdit) if edit.accessibleName() == "Research question"] == []
    assert view.composer.objectName() == "threadComposer"
    assert view.composer.accessibleName() == "Ask a question"
    assert view.composer.placeholderText().startswith("Ask a question")
    assert view.composer.isVisible()


def test_only_the_trace_button_is_filled(view, qapp):
    board_with_case(view)
    view.begin_question("Is it?", 1)
    qapp.processEvents()
    # One filled button per view: the research page has the Trace button; the case page has its own Ask button.
    filled = [button for button in view.canvas_scroll.findChildren(QPushButton) if button.objectName() == "primaryBtn"]
    assert filled == [view.ask_button]
    # A running question turns Send into Stop. Idle, it is Trace the evidence again.
    assert view.ask_button.accessibleName() == "Stop"
    view.set_searching(False)
    qapp.processEvents()
    assert view.ask_button.accessibleName() == "Trace the evidence"
    assert view.ask_button.text() == "Trace the evidence"
    assert "Ctrl+Enter" not in view.ask_button.text()
    assert "Ctrl+Enter" in view.ask_button.accessibleDescription() and "Ctrl+Enter" in view.ask_button.toolTip()


def test_new_question_and_add_comparison_are_quiet_text_buttons(view):
    assert view.new_question_button.objectName() == "textBtn"
    assert view.add_comparison_button.objectName() == "textBtn"
    assert view.new_question_button.text() == "Clear board"
    assert view.new_question_button.accessibleName() == "Clear board"
    assert view.add_comparison_button.accessibleName() == "Add comparison"


def test_ctrl_enter_traces_from_anywhere_on_the_board_and_not_while_searching(view, qapp):
    asked = []
    view.question_submitted.connect(asked.append)
    view.composer.setText("Where is peak pressure?")
    view.scope_switch.option("Cases").setFocus()
    qapp.processEvents()
    QTest.keyClick(view.scope_switch.option("Cases"), Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert asked == ["Where is peak pressure?"]
    assert view.composer.text() == ""
    view.composer.setText("Where is peak pressure?")
    view.composer.setFocus()
    QTest.keyClick(view.composer, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    assert view.composer.toPlainText().endswith("\n")
    assert asked == ["Where is peak pressure?"]
    QTest.keyClick(view.composer, Qt.Key.Key_Return)
    assert asked == ["Where is peak pressure?"] * 2
    assert view.composer.text() == ""
    view.composer.setText("kept draft")
    view.set_searching(True)
    view.scope_switch.option("Cases").setFocus()
    QTest.keyClick(view.scope_switch.option("Cases"), Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert len(asked) == 2, "a running search blocks a second one"
    assert not view.trace_action.isEnabled()
    assert view.ask_button.accessibleName() == "Stop"
    assert not view.composer.isEnabled() and view.composer.text() == "kept draft"


def test_changing_the_scope_clears_the_board_once_and_a_silent_set_does_not(view):
    changes = []
    view.scope_switch.changed.connect(changes.append)
    view.scope_switch.set_scope("Articles", notify=False)
    assert changes == []
    view.scope_switch.set_scope("Articles")
    assert changes == []  # already there: no change, no signal
    view.scope_switch.set_scope("Cases")
    assert changes == ["Cases"]
    assert view.scope == "Cases"


def _composer_row_span(view) -> int:
    bar = view.conversation.composer_bar
    tops, bottoms = [], []
    for control in (view.scope_switch, view.model_menu, view.ask_button):
        origin = control.mapTo(bar, control.rect().topLeft())
        tops.append(origin.y())
        bottoms.append(origin.y() + control.height())
    return max(bottoms) - min(tops)


def test_trace_and_stop_keep_the_composer_row_height(view, qapp):
    """At the default panel width the button stays on one row, idle and running, at the same height."""
    view.resize(1400, 800)
    qapp.processEvents()
    assert view.conversation.width() <= 520
    assert view.ask_button.text() == "Trace the evidence"
    idle = _composer_row_span(view)
    idle_y = view.ask_button.mapTo(view.conversation, view.ask_button.rect().topLeft()).y()
    scope_y = view.scope_switch.mapTo(view.conversation, view.scope_switch.rect().topLeft()).y()
    assert abs(idle_y - scope_y) < 8
    view.set_searching(True)
    qapp.processEvents()
    assert view.ask_button.accessibleName() == "Stop"
    running = _composer_row_span(view)
    running_y = view.ask_button.mapTo(view.conversation, view.ask_button.rect().topLeft()).y()
    scope_y = view.scope_switch.mapTo(view.conversation, view.scope_switch.rect().topLeft()).y()
    assert running == idle
    assert abs(running_y - scope_y) < 8


def test_the_controls_wrap_inside_a_narrow_board(view, qapp):
    """Evidence actions stay on the board. Scope, AI and Send stay inside the thread panel."""
    view.resize(336, 700)
    qapp.processEvents()
    viewport = view.board_scroll.viewport()
    for control in (view.new_question_button, view.add_comparison_button):
        left = control.mapTo(viewport, control.rect().topLeft()).x()
        assert control.isVisible() and left >= 0 and left + control.width() <= viewport.width(), control.accessibleName()
    panel = view.conversation
    for control in (view.scope_switch, view.model_menu, view.ask_button, view.composer):
        left = control.mapTo(panel, control.rect().topLeft()).x()
        assert control.isVisible() and 0 <= left and left + control.width() <= panel.width(), control.accessibleName()
    view.resize(1400, 800)
    qapp.processEvents()
    assert abs(view.model_menu.y() - view.scope_switch.y()) < 8  # one row again when it fits


def test_the_board_never_scrolls_sideways_while_it_scrolls_down(view, qapp):
    """The board and the thread never scroll sideways, including at 900, 1400 and 1920."""
    board_bar = view.board_scroll.horizontalScrollBar()
    panel_bar = view.conversation.horizontalScrollBar()
    shown_bar = False
    for width in (*range(336, 760, 8), 900, 1400, 1920):
        view.resize(width, 400)
        qapp.processEvents()
        viewport = view.board_scroll.viewport()
        shown_bar |= view.board_scroll.verticalScrollBar().isVisible()
        assert not board_bar.isVisible(), f"the board scrolls sideways at {width} px"
        assert not panel_bar.isVisible(), f"the thread scrolls sideways at {width} px"
        for control in (view.new_question_button, view.add_comparison_button):
            left = control.mapTo(viewport, control.rect().topLeft()).x()
            assert left >= 0 and left + control.width() <= viewport.width(), (width, control.accessibleName())
        panel = view.conversation
        for control in (view.composer, view.scope_switch, view.ask_button):
            left = control.mapTo(panel, control.rect().topLeft()).x()
            assert left >= 0 and left + control.width() <= panel.width(), (width, control.accessibleName())
    assert shown_bar, "the test never gave the board a vertical scroll bar"


def test_the_tab_order_follows_the_screen_question_switch_model_menu_controls_then_the_trail(view, qapp):
    board_with_case(view)
    view.begin_question("Where?", 1)
    view.set_results(RetrievalResult(candidates=(candidate("SYN-000"),)))
    view.set_answer("SYN-000 is on the board.", 1)
    view.set_searching(False)
    view.activateWindow()
    view.conversation.reset_button.setFocus()
    qapp.processEvents()
    order = [view.focusWidget()]
    for _ in range(24):
        QTest.keyClick(view.focusWidget(), Qt.Key.Key_Tab)
        order.append(view.focusWidget())
    widget = view.conversation.turn_widgets[1]
    # The turn on the board reads On board and is disabled, so Tab skips it.
    assert widget.show_button.text() == "On board" and not widget.show_button.isEnabled()
    wanted = [
        view.conversation.reset_button, widget.question_label, widget.answer_text,
        widget.copy_button, widget.ask_button,
        view.composer, view.scope_switch.option("Cases"), view.model_menu, view.ask_button,
        view.new_question_button, view.add_comparison_button,
    ]
    positions = [order.index(item) for item in wanted]
    assert positions == sorted(positions)
    assert view.candidate_table in order and order.index(view.candidate_table) > order.index(view.ask_button)


# ----- local model menu -----


def test_the_model_menu_button_names_the_model_and_its_state(view):
    view.set_models(["qwen3:14b", "gemma4:latest"], "qwen3:14b")
    assert view.model_menu.text() == "AI not checked"
    view.set_model_state("ready")
    assert view.model_menu.text() == "qwen3:14b ready"
    view.set_model_state("busy:starting")
    assert view.model_menu.text() == "AI starting"
    view.set_model_state("failed")
    assert view.model_menu.text() == "AI failed"
    view.set_models([])
    view.set_model_state("ready")
    assert view.model_menu.text() == "No model installed"


def test_the_menu_holds_every_former_model_control(view):
    view.set_models(["qwen3:14b", "gemma4:latest"], "qwen3:14b")
    texts = [action.text() for action in view.model_menu.menu_widget.actions() if action.text()]
    for expected in ("qwen3:14b", "gemma4:latest", "Unload Ollama model", "Stop Ollama", "Start Ollama",
                     "Refresh model list"):
        assert expected in texts, expected
    # Data setup moved to Settings and Ctrl+K
    assert "Data setup" not in texts
    checked = [action.text() for action in view.model_menu.model_actions() if action.isChecked()]
    assert checked == ["qwen3:14b"]


def test_menu_actions_do_what_the_buttons_did(view):
    seen = []
    view.model_start_requested.connect(lambda: seen.append("start"))
    view.model_stop_requested.connect(lambda: seen.append("stop"))
    view.model_unload_requested.connect(lambda: seen.append("unload"))
    view.model_refresh_requested.connect(lambda: seen.append("refresh"))
    view.setup_requested.connect(lambda: seen.append("setup"))
    chosen = []
    view.model_changed.connect(chosen.append)
    view.set_models(["a", "b"], "a")
    for action in (view.start_action, view.refresh_action, view.setup_action):
        action.trigger()
    view.stop_action.setEnabled(True)
    view.unload_action.setEnabled(True)
    view.stop_action.trigger()
    view.unload_action.trigger()
    assert seen == ["start", "refresh", "setup", "stop", "unload"]
    view.model_menu.model_actions()[1].trigger()
    assert chosen == ["b"] and view.selected_model() == "b"


def test_stop_and_unload_stay_off_until_the_model_service_runs_and_all_three_pause_while_busy(window, qapp):
    research = window.research
    assert research.start_action.isEnabled()
    assert not research.stop_action.isEnabled() and not research.unload_action.isEnabled()
    window._apply_ollama_status(OllamaStatus(True, "ready", ["a"], "http://127.0.0.1:11434"))
    assert research.stop_action.isEnabled() and research.unload_action.isEnabled()
    window._apply_ollama_status(OllamaStatus(False, "stopped", [], "http://127.0.0.1:11434"))
    assert not research.stop_action.isEnabled() and not research.unload_action.isEnabled()
    assert "stopped" in research.model_menu.text()
    assert research.stop_action.toolTip() == "Ollama is not running."


def test_the_ollama_reason_stays_visible_when_the_model_is_not_ready_and_hides_when_it_is(window):
    research = window.research
    assert research.model_status.isVisibleTo(research)
    window._apply_ollama_status(OllamaStatus(False, "Cannot reach Ollama at http://127.0.0.1:11434. Start it with: ollama serve",
                                             [], "http://127.0.0.1:11434"))
    research.set_status("Cannot reach Ollama at http://127.0.0.1:11434. Start it with: ollama serve")
    assert research.model_status.isVisibleTo(research) and "stopped" in research.model_status.text()
    assert "ollama serve" in research.status_label.text() and research.status_label.isVisibleTo(research)
    window._apply_ollama_status(OllamaStatus(True, "ready", ["a"], "http://127.0.0.1:11434"))
    assert not research.model_status.isVisibleTo(research)


def test_ctrl_k_lists_the_model_actions_and_the_page_actions(window, qapp):
    window._apply_ollama_status(OllamaStatus(True, "ready", ["a", "b"], "http://127.0.0.1:11434"))
    window._open_palette()
    qapp.processEvents()
    try:
        palette = window._palette
        rows = {palette.results.item(row).text(): palette.results.item(row).data(ACTION_ROLE)
                for row in range(palette.results.count())}
        for title in ("Start Ollama", "Stop Ollama", "Unload Ollama model", "Refresh model list", "Trace the evidence",
                      "Clear board", "Add comparison", "New thread",
                      "Ask about cases", "Ask about articles", "a", "b"):
            assert title in rows, title
        assert rows["Trace the evidence"].shortcut == "Ctrl+Enter"
    finally:
        palette.close()
        qapp.processEvents()


def test_a_disabled_action_still_shows_in_ctrl_k_with_its_reason(window, qapp):
    window._open_palette()
    qapp.processEvents()
    try:
        palette = window._palette
        palette.search.setText("unload")
        qapp.processEvents()
        item = palette.results.item(0)
        assert item.text() == "Unload Ollama model"
        assert not item.flags() & Qt.ItemFlag.ItemIsEnabled
        assert item.data(HINT_ROLE) == "Ollama is not running, so no model is loaded."
    finally:
        palette.close()
        qapp.processEvents()


def test_the_model_menu_button_opens_on_down_and_its_items_are_reachable_by_arrow_keys(view, qapp):
    view.set_models(["a", "b"], "a")
    view.set_model_state("ready")
    view.model_menu.setFocus()
    qapp.processEvents()
    opened = []
    view.model_menu.menu_widget.aboutToShow.connect(lambda: opened.append(True))
    QTest.keyClick(view.model_menu, Qt.Key.Key_Down)
    qapp.processEvents()
    try:
        assert opened == [True]
        assert view.model_menu.menu_widget.isVisible()
    finally:
        view.model_menu.menu_widget.close()


# ----- evidence trail -----


def test_source_row_columns_fit_the_table(view, qapp):
    """Min, PIP, ETCO2 and HR all end inside the table at 900, 1400 and 1920."""
    board_with_case(view)
    for width in (900, 1400, 1920):
        view.resize(width, 800)
        qapp.processEvents()
        table = view.source_table
        assert table.isVisible()
        viewport = table.viewport().width()
        header = table.horizontalHeader()
        for column in range(table.columnCount()):
            right = header.sectionViewportPosition(column) + header.sectionSize(column)
            name = table.horizontalHeaderItem(column).text()
            assert right <= viewport + 1, (width, name, right, viewport)


def test_nothing_sits_above_the_evidence_heading(view, qapp):
    view.resize(1400, 800)
    qapp.processEvents()
    page = view._board_page
    heading = page.findChild(QLabel, "boardHeading")
    assert heading is not None and heading.text() == "Evidence"
    heading_top = heading.mapTo(page, heading.rect().topLeft()).y()
    for label in page.findChildren(QLabel):
        if not label.isVisible() or label is heading:
            continue
        top = label.mapTo(page, label.rect().topLeft()).y()
        assert top >= heading_top - 1, label.text()
    assert view.conversation.composer_bar.isAncestorOf(view.model_status)
    assert view.status_label.mapTo(page, view.status_label.rect().topLeft()).y() > heading_top


def test_the_board_names_the_question_it_shows(view, qapp):
    assert not view.board_question.isVisible()
    view.begin_question("Where does pressure peak?", 1)
    qapp.processEvents()
    assert not view.board_question.isVisible(), "a clear board names no question"
    view.set_results(RetrievalResult(candidates=(candidate("SYN-000"),)))
    view.set_answer("SYN-000 peaks.", 1)
    view.set_searching(False)
    qapp.processEvents()
    assert view.board_question.full_text() == "Question 1 · Where does pressure peak?"
    assert view.board_question.isVisible()
    assert view.board_question.height() <= view.board_question.fontMetrics().lineSpacing() + 8
    first = view.conversation.turn_widgets[1]
    assert first.show_button.text() == "On board"
    assert not first.show_button.isEnabled()
    assert first.show_button.accessibleName() == "On board for question 1"
    long = "pressure " * 80
    view.begin_question(long, 2)
    view.set_results(RetrievalResult(candidates=(candidate("SYN-000"),)))
    view.set_answer("SYN-000 again.", 2)
    view.set_searching(False)
    qapp.processEvents()
    assert view.board_question.full_text() == f"Question 2 · {long}"
    assert view.board_question.text().endswith("…")
    assert len(view.board_question.text()) < len(view.board_question.full_text())
    assert first.show_button.text() == "Show on board" and first.show_button.isEnabled()
    second = view.conversation.turn_widgets[2]
    assert second.show_button.text() == "On board" and not second.show_button.isEnabled()
    view.new_question_button.click()
    qapp.processEvents()
    assert not view.board_question.isVisible()


def test_the_narrow_thread_viewport_shows_more_than_one_turn(view, qapp):
    view.resize(1000, 900)
    view.show()
    qapp.processEvents()
    assert view.board_splitter.orientation() == Qt.Orientation.Vertical
    assert view.conversation.height() >= 432
    assert view.conversation.viewport().height() >= 220


def test_the_trail_shows_the_observation_its_rows_and_where_they_sit(view, qapp):
    board_with_case(view)
    qapp.processEvents()
    assert view.finding_title.text().startswith("Peak sampled PIP 31.4")
    assert view.finding_open.text() == "Open at minute 3"
    assert view.finding_context.text().startswith("SYN-000 · one case, 8 minute rows, minutes 0–7")
    assert view.sparkline.has_series() and view.sparkline.state()[2] == 3
    rows = [[view.source_table.item(r, c).text() for c in range(4)] for r in range(view.source_table.rowCount())]
    assert rows == [["2", "22", "35", "70"], ["3", "31.4", "35", "70"], ["4", "23", "missing", "70"]]
    assert view.source_table.currentRow() == 1
    assert "Rows 3–5 of 8" in view.source_label.text()
    assert "Missing stays missing; nothing is filled in." in view.source_label.text()
    assert view.source_title.text() == "SYN-000"


def test_a_missing_etco2_observation_draws_the_etco2_trace_with_its_gap_and_minute(view, qapp):
    view.begin_question("Where is ETCO2 missing?", 1)
    board_with_case(view)
    qapp.processEvents()
    assert view.finding_title.text() == "ETCO₂ missing at 4 min"
    assert view.sparkline.signal() == ("ETCO₂", "s_etco2")
    _placeholder, values, marker = view.sparkline.state()
    assert marker == 4 and values[4] is None and values[3] == 35.0, "the trace is ETCO2, with the gap at the minute"
    assert view.sparkline.accessibleDescription() == "ETCO₂ over the case, observation at minute 4"


def test_a_pip_observation_draws_the_pip_trace(view, qapp):
    board_with_case(view)
    assert view.sparkline.signal() == ("PIP", "s_pip")
    assert view.sparkline.state()[1][3] == 31.4


def test_an_empty_board_shows_no_blank_shortlist_and_new_question_hides_it_again(view, qapp):
    view.show()
    qapp.processEvents()
    assert not view.candidate_table.isVisibleTo(view), "no empty table on first open"
    board_with_case(view)
    qapp.processEvents()
    assert view.candidate_table.isVisibleTo(view)
    view.new_question_button.click()
    qapp.processEvents()
    assert view.candidate_table.rowCount() == 0
    assert not view.candidate_table.isVisibleTo(view), "New question leaves no blank block"


def test_trail_tables_draw_no_column_lines_and_headings_sit_over_their_cells(view):
    for table, right in ((view.candidate_table, {0, 3}), (view.source_table, {0, 1, 2, 3})):
        assert not table.showGrid(), table.objectName()
        for column in range(table.columnCount()):
            side = table.horizontalHeaderItem(column).textAlignment() & 0x0F
            expected = Qt.AlignmentFlag.AlignRight if column in right else Qt.AlignmentFlag.AlignLeft
            assert side == expected.value, (table.objectName(), column)


def test_a_missing_source_cell_is_hatched_with_its_word(view, qapp):
    from src.desktop.marks import MISSING_ROLE

    board_with_case(view)
    flagged = [(r, c) for r in range(view.source_table.rowCount()) for c in range(4)
               if view.source_table.item(r, c).data(MISSING_ROLE)]
    assert flagged == [(2, 2)]
    assert view.source_table.item(2, 2).text() == "missing"


def test_the_shortlist_shows_three_rows_and_the_selected_one_and_show_all_reveals_the_rest(view, qapp):
    board_with_case(view, count=6)
    qapp.processEvents()
    table = view.candidate_table
    hidden = [row for row in range(table.rowCount()) if table.isRowHidden(row)]
    assert hidden == [3, 4, 5]
    assert view.show_all_button.text() == "Show all 6" and view.show_all_button.isVisible()
    view.show_all_button.click()
    assert [row for row in range(table.rowCount()) if table.isRowHidden(row)] == []
    assert view.show_all_button.text() == "Show fewer"
    view.show_all_button.click()
    # A selected row below the third stays in view.
    view._select_candidate(4)
    table.setCurrentCell(4, 0)
    view._fit_shortlist()
    assert not table.isRowHidden(4) and table.isRowHidden(5)


def test_the_shortlist_columns_are_case_procedure_duration_and_status(view):
    board_with_case(view, count=3)
    headers = [view.candidate_table.horizontalHeaderItem(c).text() for c in range(view.candidate_table.columnCount())]
    assert headers == ["#", "Case", "Procedure", "Duration", "Status"]
    assert view.candidate_table.item(0, 3).text() == "7 min"  # from the opened case's own minutes
    assert view.candidate_table.item(2, 3).text() == "—"
    assert view.candidate_table.item(2, 4).text() == "indexed surgery"
    assert "Indexed surgeries: 19,114 · analyzed cases: 28" in view.scope_label.text()


def test_the_empty_board_keeps_its_wording(view):
    assert view.finding_title.text() == "Select a retrieved case"
    assert view.finding_copy.text() == "Detailed measurements load when you inspect a case."
    assert view.source_title.text() == "Choose a case" and view.source_label.text() == "No case selected."
    assert view.selection_label.text() == "Choose a case with one click or Enter."
    assert view.sparkline.state()[0] == "PIP / source samples load on demand"
    assert view.scope_label.text() == "Indexed surgeries: — · analyzed cases: —"
    assert view.limit_label.text().startswith("A retrieved case can show a pattern")
    assert view.status_label.text() == "Set up an EMR folder to search indexed surgeries."


def test_the_limit_step_is_always_on_the_cases_board_and_hidden_for_articles(view):
    assert view.limit_step.isVisible()
    view.scope_switch.set_scope("Articles")
    assert not view.limit_step.isVisible() and view.article_card.isVisible()
    view.scope_switch.set_scope("Cases")
    assert view.limit_step.isVisible() and not view.article_card.isVisible()


def test_the_comparison_step_appears_with_a_comparison_and_the_buttons_open_it(view, qapp):
    board_with_case(view)
    assert not view.comparison_card.isVisible()
    view.set_case_data("SYN-001", series(), pd.DataFrame(), comparison=True)
    qapp.processEvents()
    assert view.comparison_card.isVisible() and "SYN-001" in view.comparison_label.text()
    requested = []
    view.case_requested.connect(lambda *args: requested.append(args))
    view.comparison_card.findChildren(QPushButton)[0].click()
    assert requested == []  # the case is already loaded, so it opens at once
    assert view.stack.currentIndex() == 1


def test_all_rows_opens_the_source_table_dialog(view, monkeypatch):
    from PySide6.QtWidgets import QDialog

    board_with_case(view)
    opened = []
    monkeypatch.setattr(QDialog, "exec", lambda self: opened.append(self.windowTitle()) or 0)
    view.all_rows_button.click()
    assert opened == ["Source samples · SYN-000"]


def test_open_at_minute_opens_the_case(view):
    board_with_case(view)
    view.finding_open.click()
    assert view.stack.currentIndex() == 1 and view.minute_slider.value() == 3


# ----- conversation -----


def test_each_question_shows_its_number_model_and_extract_count(view):
    view.set_models(["m1"], "m1")
    view.begin_question("First?", 1)
    view.set_progress("Loading 3 case extract(s)…", "fetching", 3)
    view.set_answer("An answer.", 1)
    view.set_searching(False)
    widget = view.conversation.turn_widgets[1]
    assert widget.badge_label.text() == "Question 1 · m1 · 3 extracts"
    assert "1 question, each answered from its own extracts" in view.conversation.empty_label.text()
    view.begin_question("Second?", 2)
    assert view.conversation.turn_widgets[2].badge_label.text() == "Question 2 · m1"
    assert "2 questions" in view.conversation.empty_label.text()


def test_each_answer_keeps_the_name_of_the_data_it_was_asked_on(view):
    from src.desktop.shell import DEMO_STAMP, RESEARCH_STAMP

    view.data_is_demo = True
    view.begin_question("Asked on the demo?", 1)
    view.set_answer("An answer.", 1)
    view.set_searching(False)
    first = view.conversation.turn_widgets[1]
    assert first.data_label.text() == DEMO_STAMP
    assert first.copy_text(view.conversation.turn(1)).endswith(DEMO_STAMP), "the label goes with copied text"
    view.data_is_demo = False
    view.begin_question("Asked on real data?", 2)
    second = view.conversation.turn_widgets[2]
    assert second.data_label.text() == RESEARCH_STAMP
    assert second.copy_text(view.conversation.turn(2)).endswith(RESEARCH_STAMP)
    assert first.data_label.text() == DEMO_STAMP, "an earlier answer keeps the name of its own data"


def test_a_running_question_shows_named_steps_a_bar_and_a_cancel_that_cancels(view):
    cancelled = []
    view.cancel_requested.connect(lambda: cancelled.append(True))
    view.begin_question("Running?", 1)
    view.set_progress("Preparing indexed surgery search…")
    view.set_progress("Ranking indexed surgeries…", "ranking")
    view.set_progress("Loading 3 case extract(s)…", "fetching", 3)
    widget = view.conversation.turn_widgets[1]
    assert widget.progress_bar.isVisibleTo(widget) and widget.progress_bar.value() == 70
    assert widget.steps_label.text().splitlines() == [
        "✓ Preparing indexed surgery search", "✓ Ranking indexed surgeries", "Loading 3 case extract(s)…"]
    assert widget.cancel_button.isVisibleTo(widget)
    widget.cancel_button.click()
    assert cancelled == [True]
    # The bar only moves forward, and a finished question shows no bar or steps.
    view.set_progress("Embedding the question…", "embedding_query")
    assert widget.progress_bar.value() == 70
    view.set_answer("Done.", 1)
    assert not widget.progress_bar.isVisibleTo(widget) and not widget.steps_label.isVisibleTo(widget)
    assert not widget.cancel_button.isVisibleTo(widget)


def test_answers_cite_numbered_sources_that_open_their_case(view):
    board_with_case(view, count=3)
    view.begin_question("Where?", 1)
    view.set_results(RetrievalResult(candidates=(candidate("SYN-000", "Chole"), candidate("SYN-001", "Appendectomy"))))
    view.set_answer("SYN-000 peaks. SYN-001 does not.", 1)
    widget = view.conversation.turn_widgets[1]
    assert [button.text() for button in widget.citation_buttons] == ["1  SYN-000", "2  SYN-001"]
    assert widget.citation_buttons[0].accessibleName() == "Open cited case SYN-000 in signal view"
    details = [label.text() for label in widget.findChildren(QLabel) if label.objectName() == "quietText"]
    assert "Chole" in details and "Appendectomy" in details
    requested = []
    view.conversation.case_requested.connect(lambda *args: requested.append(args))
    widget.citation_buttons[1].click()
    assert requested == [(1, "SYN-001")]


def test_copy_with_sources_puts_the_question_answer_and_numbered_sources_on_the_clipboard(view, qapp):
    view.begin_question("Where?", 1)
    view.set_results(RetrievalResult(candidates=(candidate("SYN-000", "Chole"),)))
    view.set_answer("SYN-000 peaks at minute 3.", 1)
    widget = view.conversation.turn_widgets[1]
    widget.copy_button.click()
    assert QApplication.clipboard().text() == (
        "Where?\n\nSYN-000 peaks at minute 3.\n\nSources:\n1  SYN-000 · Chole"
        "\n\nDe-identified research data · not for patient care")
    view.begin_question("Pending?", 2)
    assert not view.conversation.turn_widgets[2].copy_button.isEnabled(), "nothing to copy before an answer"


def test_copy_with_sources_copies_the_answer_as_read_without_markdown_marks(view, qapp):
    view.begin_question("Where?", 1)
    view.set_results(RetrievalResult(candidates=(candidate("SYN-000", "Chole"),)))
    view.set_answer("SYN-000 peaks at minute 3.\n\n**Verification note:** kept *as written*.", 1)
    view.conversation.turn_widgets[1].copy_button.click()
    copied = QApplication.clipboard().text()
    assert "SYN-000 peaks at minute 3.\n\nVerification note: kept as written." in copied, "paragraphs stay apart"
    assert "*" not in copied


def test_copy_with_sources_keeps_list_numbers_and_bullets(view, qapp):
    view.begin_question("Which?", 1)
    view.set_results(RetrievalResult(candidates=(candidate("SYN-000", "Chole"),)))
    view.set_answer("Cases:\n\n1. SYN-000 at minute 3\n2. SYN-001 at minute 5\n\n- PIP high\n- ETCO2 missing", 1)
    view.conversation.turn_widgets[1].copy_button.click()
    copied = QApplication.clipboard().text()
    assert ("Which?\n\nCases:\n\n1. SYN-000 at minute 3\n2. SYN-001 at minute 5\n- PIP high\n- ETCO2 missing"
            "\n\nSources:\n1  SYN-000 · Chole\n\nDe-identified research data · not for patient care") == copied


def test_the_conversation_keeps_its_actions(view):
    view.begin_question("One?", 1)
    view.set_answer("Answer.", 1)
    view.set_searching(False)
    widget = view.conversation.turn_widgets[1]
    assert (widget.show_button.text(), widget.copy_button.text(), widget.ask_button.text()) == (
        "Show on board", "Copy with sources", "Ask again")
    assert widget.show_button.isEnabled() and widget.ask_button.isEnabled()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_sparkline_and_the_spine_are_drawn_in_the_theme_tokens(qapp, theme):
    from PySide6.QtGui import QColor

    from src.desktop.theme import token_color

    apply_theme(qapp, theme)
    workspace = ResearchWorkspace()
    workspace.resize(1316, 800)
    workspace.show()
    try:
        board_with_case(workspace)
        qapp.processEvents()

        def near(image, color, tolerance=48):
            count = 0
            for x in range(image.width()):
                for y in range(image.height()):
                    pixel = QColor(image.pixel(x, y))
                    if (abs(pixel.red() - color.red()) + abs(pixel.green() - color.green())
                            + abs(pixel.blue() - color.blue())) <= tolerance:
                        count += 1
            return count

        spark = workspace.sparkline.grab().toImage()
        assert near(spark, token_color("s_pip")) > 20, "the line is the PIP signal colour of this theme"
        assert near(spark, token_color("accent")) > 5, "the observed minute is marked in the accent"
        step = workspace.observation_step.grab().toImage()
        assert near(step, token_color("rule_strong")) > 20, "the spine is a theme rule colour"
    finally:
        workspace.close()
        apply_theme(qapp, "light")
