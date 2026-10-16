"""Conversation turns keep answers, request ownership, and restorable evidence."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pandas as pd
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from src.desktop.app import MainWindow
from src.desktop.conversation_panel import NO_MODEL_ANSWER
from src.desktop.research_workspace import ResearchWorkspace
from src.desktop.theme import apply_theme
from src.services.retrieval import RetrievalResult, SearchCandidate


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def settings(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("MOVER_CONFIG_DIR", str(tmp_path / "config" / "mover-sis-monitor"))
    monkeypatch.setenv("OLLAMA_MODELS", str(tmp_path / "models"))


@pytest.fixture
def view(qapp):
    widget = ResearchWorkspace()
    widget.resize(1400, 900)
    widget.show()
    qapp.processEvents()
    yield widget
    widget.close()
    qapp.processEvents()


@pytest.fixture
def window(qapp, monkeypatch, synthetic_emr):
    monkeypatch.setenv("MOVER_EMR_DIR", str(synthetic_emr))
    monkeypatch.setattr(MainWindow, "_startup", lambda self: None)
    monkeypatch.setattr("src.desktop.app.ResearchWorker.start", lambda self: None)
    monkeypatch.setattr(MainWindow, "_load_research_case", lambda *args: None)
    win = MainWindow()
    win._research_engine = SimpleNamespace(emr=synthetic_emr.resolve())
    win.research.set_models(["test-model"])
    yield win
    win.close()
    qapp.processEvents()


def candidate(pid):
    return SearchCandidate(
        pid=pid, source_label="indexed surgery", analyzed=False,
        procedure="Synthetic procedure", age=55, gender="M", source_text=pid,
        rank_method="keyword", score=0.5,
    )


def result(*pids):
    return RetrievalResult(candidates=tuple(candidate(pid) for pid in pids), indexed_count=30)


@pytest.mark.parametrize("state,answer,failure", [
    ("running", "", ""),
    ("answered", "caseA has recorded samples.\n**Verification note:** preserved exactly.", ""),
    ("no_model", NO_MODEL_ANSWER, ""),
    ("cancelled", "", ""),
    ("failed", "Research request failed: synthetic timeout", "synthetic timeout"),
])
def test_turn_states_and_exact_gated_text(view, state, answer, failure):
    view.set_models(["test-model"])
    view.begin_question("Where does pressure peak?", 1)
    view.set_results(result("caseA"))
    if state == "cancelled":
        view.cancel_question()
    elif state != "running":
        view.set_answer(answer, 1, state=state, failure=failure)
    turn = view.conversation.turns[0]
    widget = view.conversation.turn_widgets[1]
    assert turn.state == state
    assert turn.model == "test-model"
    assert widget.badge_label.text() == "test-model"
    assert widget.answer_text.toPlainText() == answer
    if failure:
        view.set_status("Unrelated status update")
        assert turn.failure == failure
        assert failure in widget.state_label.text()


def test_stale_callbacks_cannot_mutate_any_turn_or_the_board(window):
    win = window
    win._start_research_search("old")
    old = win._active_research_request
    win._cancel_research()
    win._start_research_search("new")
    current = win._active_research_request
    win._on_research_shortlist(result("caseA"), current)
    win._on_research_answer(SimpleNamespace(answer="caseA current answer"), current)
    before = [replace(turn) for turn in win.research.conversation.turns]
    board = win.research.result
    win._on_research_shortlist(result("caseB"), old)
    win._on_research_answer(SimpleNamespace(answer="stale"), old)
    win._on_research_fail("stale failure", old)
    win._on_research_done(old)
    assert win.research.conversation.turns == before
    assert win.research.result is board
    assert win.research.answer_text.toPlainText() == "caseA current answer"
    assert win.research._searching


def test_no_model_search_has_a_turn_and_filled_board(window):
    win = window
    win.research.set_models([])
    win._start_research_search("pressure")
    request = win._active_research_request
    win._on_research_shortlist(result("caseA"), request)
    win._on_research_done(request)
    turn = win.research.conversation.turns[0]
    assert turn.state == "no_model"
    assert turn.answer == NO_MODEL_ANSWER
    assert turn.badge == "No AI answer"
    assert win.research.candidate_table.rowCount() == 1


def test_show_on_board_restores_shortlist_scope_selection_and_comparison(view):
    view.begin_question("first", 1)
    first = result("caseA", "caseB")
    view.set_results(first)
    ts = pd.DataFrame({"t_min": [0, 1], "PIP": [20, 35]})
    view.set_case_data("caseA", ts, pd.DataFrame())
    view.set_case_data("caseB", ts, pd.DataFrame(), comparison=True)
    view._select_candidate(1)
    view.set_answer("caseA and caseB have samples.", 1)
    view.set_searching(False)
    scope = view.scope_label.text()
    view.begin_question("second", 2)
    assert view.result is None
    assert view.candidate_table.rowCount() == 0
    assert "caseB" not in view.selection_label.text()
    view.set_results(result("caseC"))
    view.set_answer("caseC has samples.", 2)
    view.set_searching(False)
    view.conversation.turn_widgets[1].show_button.click()
    assert view.result is first
    assert [view.candidate_table.item(i, 0).text() for i in range(2)] == ["caseA", "caseB"]
    assert view.scope_label.text() == scope
    assert view._selected_pid == "caseB"
    assert view.candidate_table.currentRow() == 1
    assert view._primary_pid == "caseA"
    assert view._comparison_pid == "caseB"
    assert view.comparison_card.isVisible()
    assert "35" in view.finding_title.text()
    assert view.answer_text.toPlainText() == view.conversation.turns[0].answer
    assert len(view.conversation.turns) == 2


def test_adding_an_already_loaded_case_as_comparison_is_saved_with_the_turn(view):
    view.begin_question("first", 1)
    view.set_results(result("caseA", "caseB"))
    ts = pd.DataFrame({"t_min": [0, 1], "PIP": [20, 35]})
    view.set_case_data("caseB", ts, pd.DataFrame())
    view.set_case_data("caseA", ts, pd.DataFrame())
    view.set_answer("caseA has samples.", 1)
    view.set_searching(False)
    view._open_or_request("caseB", comparison=True, open_detail=False)
    assert view._comparison_pid == "caseB"
    view.new_question_button.click()
    assert view._comparison_pid is None
    view.conversation.turn_widgets[1].show_button.click()
    assert view._primary_pid == "caseA"
    assert view._comparison_pid == "caseB"
    assert view.comparison_card.isVisible()


def test_ask_again_appends_a_new_turn_with_the_same_question(window):
    win = window
    win._start_research_search("repeat this question")
    first = win.research.conversation.turns[0]
    win._on_research_answer(SimpleNamespace(answer="first answer"), first.request_id)
    win._on_research_done(first.request_id)
    win.research.conversation.turn_widgets[first.request_id].ask_button.click()
    turns = win.research.conversation.turns
    assert len(turns) == 2
    assert turns[1].question == first.question
    assert turns[1].request_id != first.request_id
    assert turns[1].state == "running"
    assert turns[0].answer == "first answer"


def test_cancel_keeps_turn_and_restores_previous_board(window):
    win = window
    win._start_research_search("first")
    first = win._active_research_request
    win._on_research_shortlist(result("caseA"), first)
    win._on_research_answer(SimpleNamespace(answer="caseA answer"), first)
    win._on_research_done(first)
    scope = win.research.scope_label.text()
    win._start_research_search("cancel me")
    win._on_research_shortlist(result("caseB"), win._active_research_request)
    win._cancel_research()
    assert [turn.state for turn in win.research.conversation.turns] == ["answered", "cancelled"]
    assert win.research.question_edit.text() == "first"
    assert win.research.result.candidates[0].pid == "caseA"
    assert win.research.scope_label.text() == scope


def test_failure_reason_stays_in_turn_after_other_status_updates(window):
    win = window
    win._start_research_search("failing")
    win._on_research_fail("synthetic failure", win._active_research_request)
    win.research.set_status("Another status")
    turn = win.research.conversation.turns[0]
    assert turn.state == "failed"
    assert turn.failure == "synthetic failure"
    assert "synthetic failure" in win.research.conversation.turn_widgets[turn.request_id].state_label.text()


@pytest.mark.parametrize("width,orientation", [(1000, Qt.Orientation.Vertical), (1400, Qt.Orientation.Horizontal)])
def test_splitter_stacks_below_board_under_1100(view, qapp, width, orientation):
    view.resize(width, 900)
    qapp.processEvents()
    assert view.width() == width
    assert view.board_splitter.orientation() == orientation
    assert view.board_splitter.widget(0) is view.board_scroll
    assert view.board_splitter.widget(1) is view.conversation
    assert view.conversation.isVisible()
    assert view.board_scroll.isVisible()
    if orientation == Qt.Orientation.Vertical:
        panel_top = view.conversation.mapTo(view, QPoint(0, 0)).y()
        board_bottom = view.board_scroll.mapTo(view, QPoint(0, 0)).y() + view.board_scroll.height()
        assert panel_top >= board_bottom - 2
    else:
        panel_left = view.conversation.mapTo(view, QPoint(0, 0)).x()
        board_right = view.board_scroll.mapTo(view, QPoint(0, 0)).x() + view.board_scroll.width()
        assert panel_left >= board_right - 2
        assert 300 <= view.conversation.width() <= 380


def test_reset_clears_thread_and_board_and_drops_late_answers(window, monkeypatch):
    win = window
    monkeypatch.setattr(ResearchWorkspace, "_confirm_reset", lambda self: True)
    win._start_research_search("reset this")
    request = win._active_research_request
    win.research.reset_button.click()
    win._on_research_answer(SimpleNamespace(answer="late answer"), request)
    assert win.research.conversation.turns == []
    assert win.research.conversation.turn_widgets == {}
    assert win.research.result is None
    assert win.research.answer_text.toPlainText() == ""


def test_cited_case_link_uses_case_requested_even_after_another_question(view):
    requests = []
    view.case_requested.connect(lambda *args: requests.append(args))
    view.begin_question("first", 1)
    view.set_results(result("caseA", "caseB"))
    view.set_answer("caseA has samples. NotcaseB is not a citation.", 1)
    view.set_searching(False)
    view.begin_question("second", 2)
    view.set_results(result("caseC"))
    view.set_answer("caseC has samples.", 2)
    widget = view.conversation.turn_widgets[1]
    assert view.conversation.turns[0].cited_pids == ("caseA",)
    widget.citation_buttons[0].click()
    assert requests == [("caseA", False, True)]
    assert view.candidate_for("caseA").pid == "caseA"


def test_updates_retain_other_turn_widgets(view):
    for request in range(1, 4):
        view.begin_question(str(request), request)
        view.set_answer(str(request), request)
        view.set_searching(False)
    widgets = dict(view.conversation.turn_widgets)
    view.set_answer("updated third answer", 3)
    assert view.conversation.turn_widgets == widgets
    assert widgets[1].answer_text.toPlainText() == "1"
    assert widgets[3].answer_text.toPlainText() == "updated third answer"


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_turn_controls_are_keyboard_reachable_with_contrasting_text(view, qapp, theme):
    apply_theme(qapp, theme)
    view.begin_question("question", 1)
    view.set_results(result("caseA"))
    view.set_answer("caseA has samples.", 1)
    view.set_searching(False)
    view.activateWindow()
    qapp.processEvents()
    widget = view.conversation.turn_widgets[1]
    controls = [widget.show_button, widget.ask_button, *widget.citation_buttons]
    reached = set()
    view.question_edit.setFocus()
    for _ in range(100):
        QTest.keyClick(view.focusWidget(), Qt.Key.Key_Tab)
        reached.add(view.focusWidget())
    assert all(control in reached for control in controls)
    assert "QWidget#conversationPage QPushButton:focus" in view.styleSheet()

    def luminance(color):
        rgb = [color.redF(), color.greenF(), color.blueF()]
        channels = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
        return sum(c * weight for c, weight in zip(channels, (0.2126, 0.7152, 0.0722), strict=True))

    for control, fg, bg in [
        (widget.question_label, QPalette.ColorRole.WindowText, QPalette.ColorRole.Window),
        (widget.answer_text, QPalette.ColorRole.Text, QPalette.ColorRole.Base),
        (widget.show_button, QPalette.ColorRole.ButtonText, QPalette.ColorRole.Button),
    ]:
        control.ensurePolished()
        background = widget.palette().color(bg) if control is widget.question_label else control.palette().color(bg)
        colors = sorted([luminance(control.palette().color(fg)), luminance(background)])
        assert (colors[1] + 0.05) / (colors[0] + 0.05) >= 4.5
    apply_theme(qapp, "light")


def _answered_turn(view, request, question, *pids, peak=20):
    view.begin_question(question, request)
    view.set_results(result(*pids))
    view.set_case_data(pids[0], pd.DataFrame({"t_min": [0, 1], "PIP": [10, peak]}), pd.DataFrame())
    view.set_answer(f"{pids[0]} has samples.", request)
    view.set_searching(False)


def test_reload_drops_saved_case_data_but_keeps_the_conversation(view):
    requests = []
    view.case_requested.connect(lambda *args: requests.append(args))
    _answered_turn(view, 1, "first", "caseA", peak=19.95)
    _answered_turn(view, 2, "second", "caseB")
    old = view.conversation.turns[0]
    assert "19.95" in old.board.labels[2]
    view.invalidate_case_data()
    for turn in view.conversation.turns:
        assert turn.board.case_views == {}
        assert turn.board.primary_pid is None and turn.board.comparison_pid is None
        assert turn.note == "Processed data was reloaded. Reopen a case to see current data."
        assert view.conversation.turn_widgets[turn.request_id].note_label.text() == turn.note
    assert (old.question, old.answer, old.cited_pids) == ("first", "caseA has samples.", ("caseA",))
    assert [c.pid for c in old.board.result.candidates] == ["caseA"]
    view.conversation.turn_widgets[1].show_button.click()
    assert view._case_views == {} and view._primary_pid is None
    assert "19.95" not in view.finding_title.text()
    view.conversation.turn_widgets[1].citation_buttons[0].click()
    assert requests == [("caseA", False, True)]
    view.set_case_data("caseA", pd.DataFrame({"t_min": [0, 1], "PIP": [10, 999]}), pd.DataFrame())
    assert "999" in view.finding_title.text()


def test_reload_during_a_running_question_fails_it_and_frees_the_buttons(view):
    cancelled = []
    view.board_restore_requested.connect(lambda: cancelled.append(True))
    view.begin_question("slow", 1)
    view.set_results(result("caseA"))
    view.invalidate_case_data()
    turn = view.conversation.turns[0]
    widget = view.conversation.turn_widgets[1]
    assert turn.state == "failed"
    assert turn.failure == "Processed data was reloaded during this question."
    assert turn.failure in widget.state_label.text()
    assert widget.show_button.isEnabled() and widget.ask_button.isEnabled()
    assert cancelled == [True]
    assert not view._searching
    view.set_answer("late answer", 1)
    assert turn.answer == "" and turn.state == "failed"
    asked = []
    view.question_submitted.connect(asked.append)
    widget.ask_button.click()
    assert asked == ["slow"]


def test_citation_in_an_older_turn_is_saved_into_that_turn(view):
    requests = []
    view.case_requested.connect(lambda *args: requests.append(args))
    view.begin_question("first", 1)
    view.set_results(result("caseA"))
    view.set_answer("caseA has samples.", 1)
    view.set_searching(False)
    _answered_turn(view, 2, "second", "caseC", peak=77)
    second = view.conversation.turns[1]
    view.conversation.turn_widgets[1].citation_buttons[0].click()
    assert requests == [("caseA", False, True)]
    assert view._board_request == 1
    view.set_case_data("caseA", pd.DataFrame({"t_min": [0, 1], "PIP": [10, 55]}), pd.DataFrame())
    first = view.conversation.turns[0]
    assert first.board.primary_pid == "caseA" and "caseA" in first.board.case_views
    assert second.board.primary_pid == "caseC" and list(second.board.case_views) == ["caseC"]


def test_new_question_clears_the_board_and_keeps_the_thread(view, qapp):
    _answered_turn(view, 1, "first", "caseA")
    _answered_turn(view, 2, "second", "caseB")
    view.activateWindow()
    view.new_question_button.click()
    qapp.processEvents()
    assert [turn.question for turn in view.conversation.turns] == ["first", "second"]
    assert view.result is None and view.candidate_table.rowCount() == 0
    assert view.question_edit.text() == ""
    assert view.focusWidget() is view.question_edit
    assert view.conversation.turns[1].board.result is not None
    view.conversation.turn_widgets[1].show_button.click()
    assert [view.candidate_table.item(0, 0).text()] == ["caseA"]


def _answer_box(monkeypatch, button):
    boxes = []

    def exec_box(self):
        boxes.append((self.text(), sorted(b.text() for b in self.buttons()), self.defaultButton().text()))
        next(b for b in self.buttons() if b.text() == button).click()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", exec_box)
    return boxes


def test_reset_asks_first_and_cancel_keeps_everything(view, monkeypatch):
    _answered_turn(view, 1, "first", "caseA")
    boxes = _answer_box(monkeypatch, "Cancel")
    view.reset_button.click()
    assert boxes == [("Clear the conversation and the board?", ["Cancel", "Clear"], "Cancel")]
    assert len(view.conversation.turns) == 1
    assert view.result is not None and view.candidate_table.rowCount() == 1
    assert view._primary_pid == "caseA"
    boxes = _answer_box(monkeypatch, "Clear")
    view.reset_button.click()
    assert len(boxes) == 1
    assert view.conversation.turns == [] and view.conversation.turn_widgets == {}
    assert view.result is None and view.candidate_table.rowCount() == 0


def test_reset_with_an_empty_thread_does_not_ask(view, monkeypatch):
    boxes = _answer_box(monkeypatch, "Cancel")
    view.question_edit.setText("typed only")
    view.reset_button.click()
    assert boxes == []
    assert view.question_edit.text() == ""


def test_long_answer_grows_the_turn_instead_of_scrolling_inside_it(view, qapp):
    long_answer = "\n".join(f"Line {i}: caseA has recorded samples for this question." for i in range(60))
    view.begin_question("long", 1)
    view.set_results(result("caseA"))
    view.set_answer(long_answer, 1)
    view.set_searching(False)
    QTest.qWait(50)
    text = view.conversation.turn_widgets[1].answer_text
    assert text.height() > 600
    assert text.verticalScrollBar().maximum() == 0
    assert text.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert text.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse
    assert view.conversation.verticalScrollBar().maximum() > 0
    view.resize(1000, 900)
    QTest.qWait(50)
    assert text.verticalScrollBar().maximum() == 0
    assert view.conversation.verticalScrollBar().maximum() > 0
