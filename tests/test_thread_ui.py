"""Thread UI: bubbles, folded checks, the composer, and minute questions in the thread."""

from __future__ import annotations

import os
import re

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit

from src.desktop.conversation_panel import split_verification_notes
from src.desktop.research_workspace import ResearchWorkspace
from src.desktop.theme import apply_theme, token_color
from src.llm.prompts import VERIFICATION_FOOTER
from src.services.retrieval import RetrievalResult, SearchCandidate

PANEL = os.path.join(os.path.dirname(__file__), "..", "src", "desktop", "conversation_panel.py")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def view(qapp):
    apply_theme(qapp, "light")
    widget = ResearchWorkspace()
    widget.resize(1400, 900)
    widget.show()
    qapp.processEvents()
    yield widget
    widget.close()
    qapp.processEvents()


def _candidate(pid: str) -> SearchCandidate:
    return SearchCandidate(
        pid=pid, source_label="analyzed case", analyzed=True, procedure="Chole",
        age=55, gender="F", source_text=f"PID {pid}.", rank_method="keyword", score=0.5,
    )


def _contrast(first: QColor, second: QColor) -> float:
    def luminance(color: QColor) -> float:
        channels = []
        for value in (color.redF(), color.greenF(), color.blueF()):
            channels.append(value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4)
        return sum(channel * weight for channel, weight in zip(channels, (0.2126, 0.7152, 0.0722), strict=True))

    high, low = sorted((luminance(first), luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_verification_notes_leave_the_body_and_the_footer(view, qapp):
    prose = 'The record says "Verification note" in a quoted sentence.'
    answer = (
        f"Body line.\n\n{prose}\n\n"
        "**Verification note:** first check found a problem.\n\n"
        "**Verification note:** second check.\n"
        f"{VERIFICATION_FOOTER}"
    )
    body, notes = split_verification_notes(answer)
    assert notes == ("first check found a problem.", "second check.")
    assert "Verification note" in body and prose in body
    assert "not for clinical care" in body
    assert "**Verification note:**" not in body
    view.begin_question("Checked?", 1)
    view.set_answer(answer, 1)
    view.set_searching(False)
    qapp.processEvents()
    widget = view.conversation.turn_widgets[1]
    assert "first check" not in widget.answer_text.toPlainText()
    assert "not for clinical care" in widget.answer_text.toPlainText()
    assert widget.checks_toggle.text() == "Checks: 2 notes"
    assert widget.checks_toggle.property("amber") == "true"
    assert "collapsed" in widget.checks_toggle.accessibleDescription()
    assert not widget.checks_container.isVisible()
    widget.checks_toggle.click()
    qapp.processEvents()
    assert widget.checks_container.isVisible()
    assert [label.text() for label in widget.check_note_labels] == list(notes)
    assert "expanded" in widget.checks_toggle.accessibleDescription()
    copied = widget.copy_text(view.conversation.turn(1))
    assert "first check found a problem." in copied and "not for clinical care" in copied


def test_one_note_and_no_notes(view):
    view.begin_question("One?", 1)
    view.set_answer("Fine.\n\n**Verification note:** only one.", 1)
    view.set_searching(False)
    widget = view.conversation.turn_widgets[1]
    assert widget.checks_toggle.text() == "Checks: 1 note"
    view.begin_question("None?", 2)
    view.set_answer("Fine, with no check.", 2)
    view.set_searching(False)
    assert not view.conversation.turn_widgets[2].checks_toggle.isVisible()


def test_sources_start_folded(view, qapp):
    view.begin_question("Where?", 1)
    view.set_results(RetrievalResult(candidates=(_candidate("SYN-000"), _candidate("SYN-001"))))
    view.set_answer("SYN-000 and SYN-001.", 1)
    view.set_searching(False)
    qapp.processEvents()
    widget = view.conversation.turn_widgets[1]
    assert widget.sources_toggle.text() == "Sources (2)"
    assert "collapsed" in widget.sources_toggle.accessibleDescription()
    assert widget.sources_toggle.accessibleName().startswith("Sources")
    assert not widget.citation_buttons[0].isVisible()
    assert widget.citation_buttons[0].focusPolicy() == Qt.FocusPolicy.NoFocus
    widget.sources_toggle.click()
    qapp.processEvents()
    assert widget.citation_buttons[0].isVisible()
    assert "expanded" in widget.sources_toggle.accessibleDescription()
    view.begin_question("Empty?", 2)
    view.set_answer("No case id here.", 2)
    view.set_searching(False)
    assert not view.conversation.turn_widgets[2].sources_toggle.isVisible()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_bubble_is_right_aligned_plain_and_wraps(view, qapp, theme):
    apply_theme(qapp, theme)
    view.begin_question("<b>x</b> & <script>", 1)
    widget = view.conversation.turn_widgets[1]
    qapp.processEvents()
    assert widget.question_label.text() == "<b>x</b> & <script>"
    assert "<b>" in widget.question_label.toPlainText()
    for width in (360, 480, 700):
        view.conversation.resize(width, 600)
        qapp.processEvents()
        widget.resize(width - 40, widget.height())
        widget._fit_bubble()
        qapp.processEvents()
        content_right = widget.rect().right()
        bubble_right = widget.bubble.mapTo(widget, widget.bubble.rect().bottomRight()).x()
        assert abs(bubble_right - content_right) <= 1, (width, bubble_right, content_right)
        assert widget.bubble.width() <= int(widget.width() * 0.80) + 1
    long_word = "W" * 300
    view.conversation.turns[0].question = long_word
    widget.update_turn(view.conversation.turns[0])
    widget.resize(480, 400)
    widget._fit_bubble()
    qapp.processEvents()
    assert widget.question_label.horizontalScrollBar().maximum() == 0
    assert widget.question_label.horizontalScrollBar().isVisible() is False
    ink = token_color("ink", theme)
    surface = token_color("sel", theme)
    assert _contrast(ink, surface) >= 4.5
    apply_theme(qapp, "light")


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_checks_use_the_warning_token(qapp, theme):
    warn = token_color("warn", theme)
    ground = token_color("warn_bg", theme)
    assert _contrast(warn, ground) >= 4.5
    text = open(PANEL, encoding="utf-8").read()
    assert re.search(r"#[0-9A-Fa-f]{3,8}", text) is None


def test_composer_keys_placeholder_and_stop(view, qapp, monkeypatch):
    monkeypatch.setattr(ResearchWorkspace, "_confirm_reset", lambda self: True)
    asked = []
    view.question_submitted.connect(asked.append)
    assert view.composer.placeholderText().startswith("Ask a question")
    view.composer.setText("   ")
    QTest.keyClick(view.composer, Qt.Key.Key_Return)
    assert asked == []
    view.composer.setText("line one")
    QTest.keyClick(view.composer, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    QTest.keyClicks(view.composer, "line two")
    assert "\n" in view.composer.text()
    QTest.keyClick(view.composer, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert asked == ["line one\nline two"]
    assert view.composer.text() == ""
    view.begin_question("Running?", 1)
    view.composer.setText("draft")
    view.set_searching(True)
    qapp.processEvents()
    assert not view.composer.isEnabled()
    assert view.composer.text() == "draft"
    assert view.ask_button.accessibleName() == "Stop"
    assert view.ask_button.text().startswith("Stop")
    QTest.keyClick(view.composer, Qt.Key.Key_Return)
    assert asked == ["line one\nline two"]
    view.set_searching(False)
    assert view.composer.isEnabled()
    assert view.composer.placeholderText().startswith("Ask a follow-up")
    assert view.composer.accessibleName() == "Ask a follow-up"
    view.conversation.reset_button.click()
    assert view.composer.placeholderText().startswith("Ask a question")


def test_splitter_clamps_and_the_top_field_is_gone(view, qapp):
    view.resize(1400, 900)
    qapp.processEvents()
    assert abs(view.conversation.width() - 480) <= 20
    view.board_splitter.setSizes([2000, 100])
    qapp.processEvents()
    assert view.conversation.width() >= 360
    view.board_splitter.setSizes([100, 5000])
    qapp.processEvents()
    assert view.conversation.width() <= view.board_splitter.width() // 2 + 2
    assert view.board_scroll.width() >= 360
    assert view.findChild(QLineEdit, "questionField") is None


def test_new_thread_clears_history_and_clear_board_does_not(view, qapp, monkeypatch):
    view.begin_question("first", 1)
    view.set_results(RetrievalResult(candidates=(_candidate("SYN-000"),)))
    view.set_answer("done", 1)
    view.set_searching(False)
    view.composer.setText("draft")
    view.new_question_button.click()
    qapp.processEvents()
    assert [turn.question for turn in view.conversation.turns] == ["first"]
    assert view.result is None
    assert view.composer.text() == "draft"
    monkeypatch.setattr(ResearchWorkspace, "_confirm_reset", lambda self: True)
    view.conversation.reset_button.click()
    assert view.conversation.turns == []
    assert view.composer.text() == ""
    assert view.conversation.history_before() == ()


def test_keyboard_order_goes_backwards_too(view, qapp):
    view.begin_question("Where?", 1)
    view.set_answer("An answer.", 1)
    view.set_searching(False)
    view.activateWindow()
    view.ask_button.setFocus()
    qapp.processEvents()
    QTest.keyClick(view.ask_button, Qt.Key.Key_Tab, Qt.KeyboardModifier.ShiftModifier)
    assert view.focusWidget() is view.model_menu
    QTest.keyClick(view.focusWidget(), Qt.Key.Key_Tab, Qt.KeyboardModifier.ShiftModifier)
    assert view.focusWidget() is view.scope_switch.option("Cases")
    QTest.keyClick(view.focusWidget(), Qt.Key.Key_Tab, Qt.KeyboardModifier.ShiftModifier)
    assert view.focusWidget() is view.composer
