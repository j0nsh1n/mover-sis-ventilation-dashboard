"""The research page's question block, local model menu, evidence trail and conversation panel."""

from __future__ import annotations

import os

import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

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
    view.question_edit.setFocus()
    qapp.processEvents()
    font = view.question_edit.font()
    assert font.pixelSize() == 28 and font.family() == "Charter"
    board_sizes = [label.font().pixelSize() for label in view._board_page.findChildren(QLabel) if label.isVisible()]
    assert board_sizes and max(board_sizes) < font.pixelSize()
    assert view.question_edit.accessibleName() == "Research question"


def test_only_the_trace_button_is_filled(view, qapp):
    board_with_case(view)
    view.begin_question("Is it?", 1)
    qapp.processEvents()
    # One filled button per view: the research page has the Trace button; the case page has its own Ask button.
    filled = [button for button in view.canvas_scroll.findChildren(QPushButton) if button.objectName() == "primaryBtn"]
    assert filled == [view.ask_button]
    assert view.ask_button.accessibleName() == "Trace the evidence"
    assert "Ctrl+Enter" in view.ask_button.text() and "Ctrl+Enter" in view.ask_button.accessibleDescription()


def test_new_question_and_add_comparison_are_quiet_text_buttons(view):
    assert view.new_question_button.objectName() == "textBtn"
    assert view.add_comparison_button.objectName() == "textBtn"
    assert view.new_question_button.accessibleName() == "New question"
    assert view.add_comparison_button.accessibleName() == "Add comparison"


def test_ctrl_enter_traces_from_anywhere_on_the_board_and_not_while_searching(view, qapp):
    asked = []
    view.question_submitted.connect(asked.append)
    view.question_edit.setText("Where is peak pressure?")
    view.scope_switch.option("Cases").setFocus()
    qapp.processEvents()
    QTest.keyClick(view.scope_switch.option("Cases"), Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert asked == ["Where is peak pressure?"]
    # Plain Enter in the field still submits, as before.
    view.question_edit.setFocus()
    QTest.keyClick(view.question_edit, Qt.Key.Key_Return)
    assert asked == ["Where is peak pressure?"] * 2
    view.set_searching(True)
    view.scope_switch.option("Cases").setFocus()
    QTest.keyClick(view.scope_switch.option("Cases"), Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert len(asked) == 2, "a running search blocks a second one"
    assert not view.trace_action.isEnabled()


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


def test_the_controls_wrap_inside_a_narrow_board(view, qapp):
    """Scope and the model menu stack when they are too wide together; nothing is cut off at the board's edge."""
    view.resize(336, 700)
    qapp.processEvents()
    viewport = view.board_scroll.viewport()
    controls = [view.scope_switch, view.model_menu, view.new_question_button, view.add_comparison_button,
                view.ask_button]
    for control in controls:
        left = control.mapTo(viewport, control.rect().topLeft()).x()
        assert control.isVisible() and left >= 0 and left + control.width() <= viewport.width(), control.accessibleName()
    assert view.model_menu.y() > view.scope_switch.y()  # stacked: the menu is under the switch, not clipped beside it
    view.resize(1316, 800)
    qapp.processEvents()
    assert abs(view.model_menu.y() - view.scope_switch.y()) < 8  # one row again when it fits


def test_the_board_never_scrolls_sideways_while_it_scrolls_down(view, qapp):
    """A short window gives the board a vertical scroll bar, which narrows its room; the controls must fit beside it."""
    bar = view.board_scroll.horizontalScrollBar()
    controls = [view.scope_switch, view.model_menu, view.new_question_button, view.add_comparison_button,
                view.ask_button]
    shown_bar = False
    for width in range(336, 760, 8):
        view.resize(width, 400)
        qapp.processEvents()
        viewport = view.board_scroll.viewport()
        shown_bar |= view.board_scroll.verticalScrollBar().isVisible()
        assert not bar.isVisible(), f"the board scrolls sideways at {width} px"
        for control in controls:
            left = control.mapTo(viewport, control.rect().topLeft()).x()
            assert left >= 0 and left + control.width() <= viewport.width(), (width, control.accessibleName())
    assert shown_bar, "the test never gave the board a vertical scroll bar"


def test_the_tab_order_follows_the_screen_question_switch_model_menu_controls_then_the_trail(view, qapp):
    board_with_case(view)
    view.activateWindow()
    view.question_edit.setFocus()
    qapp.processEvents()
    order = []
    for _ in range(14):
        QTest.keyClick(view.focusWidget(), Qt.Key.Key_Tab)
        order.append(view.focusWidget())
    names = [widget for widget in order if widget in (
        view.scope_switch.option("Cases"), view.model_menu, view.new_question_button,
        view.add_comparison_button, view.ask_button, view.candidate_table, view.inspect_button)]
    assert names[:5] == [view.scope_switch.option("Cases"), view.model_menu, view.new_question_button,
                         view.add_comparison_button, view.ask_button]
    assert names.index(view.candidate_table) > names.index(view.ask_button)


# ----- local model menu -----


def test_the_model_menu_button_names_the_model_and_its_state(view):
    view.set_models(["qwen3:14b", "gemma4:latest"], "qwen3:14b")
    assert view.model_menu.text() == "Local AI not checked"
    view.set_model_state("ready")
    assert view.model_menu.text() == "qwen3:14b ready"
    view.set_model_state("busy:starting")
    assert view.model_menu.text() == "Local AI starting"
    view.set_model_state("failed")
    assert view.model_menu.text() == "Local AI failed"
    view.set_models([])
    view.set_model_state("ready")
    assert view.model_menu.text() == "No model installed"


def test_the_menu_holds_every_former_model_control(view):
    view.set_models(["qwen3:14b", "gemma4:latest"], "qwen3:14b")
    texts = [action.text() for action in view.model_menu.menu_widget.actions() if action.text()]
    for expected in ("qwen3:14b", "gemma4:latest", "Unload model", "Stop AI", "Start AI",
                     "Refresh model list", "Data setup"):
        assert expected in texts, expected
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
    assert research.stop_action.toolTip() == "Local AI is not running."


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
        for title in ("Start AI", "Stop AI", "Unload model", "Refresh model list", "Trace the evidence",
                      "New question", "Add comparison", "Reset conversation and board",
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
        assert item.text() == "Unload model"
        assert not item.flags() & Qt.ItemFlag.ItemIsEnabled
        assert item.data(HINT_ROLE) == "Local AI is not running, so no model is loaded."
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
    assert QApplication.clipboard().text() == "Where?\n\nSYN-000 peaks at minute 3.\n\nSources:\n1  SYN-000 · Chole"
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
            "\n\nSources:\n1  SYN-000 · Chole") == copied


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
