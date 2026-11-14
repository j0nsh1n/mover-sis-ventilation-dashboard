"""The research page's own widgets, on their own: scope switch, buttons, model menu, trail pieces."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from src.desktop.evidence_trail import Sparkline, TrailStep
from src.desktop.research_controls import LocalModelButton, ScopeSwitch, TraceButton
from src.desktop.theme import apply_theme, token_color


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True, scope="module")
def light(qapp):
    # Once per file. Tests that switch theme put light back before returning.
    apply_theme(qapp, "light")
    yield
    apply_theme(qapp, "light")


def count_near(image, color: QColor, tolerance: int = 48) -> int:
    total = 0
    for x in range(image.width()):
        for y in range(image.height()):
            pixel = QColor(image.pixel(x, y))
            distance = abs(pixel.red() - color.red()) + abs(pixel.green() - color.green()) + abs(pixel.blue() - color.blue())
            total += distance <= tolerance
    return total


# ----- scope switch -----


def test_the_switch_starts_on_cases_and_reports_only_real_changes(qapp):
    switch = ScopeSwitch()
    seen = []
    switch.changed.connect(seen.append)
    assert switch.scope() == "Cases"
    switch.set_scope("Cases")
    switch.set_scope("Articles", notify=False)
    assert seen == [] and switch.scope() == "Articles"
    switch.set_scope("Cases")
    switch.set_scope("Articles")
    assert seen == ["Cases", "Articles"]
    switch.set_scope("Nonsense")
    assert switch.scope() == "Articles"


def test_only_the_chosen_option_takes_tab_and_the_arrows_move_between_options(qapp):
    host = QWidget()
    layout = QVBoxLayout(host)
    switch = ScopeSwitch()
    layout.addWidget(switch)
    host.show()
    qapp.processEvents()
    try:
        assert switch.accessibleName() == "Question scope"
        cases, articles = switch.option("Cases"), switch.option("Articles")
        cases.setFocus()
        QTest.keyClick(cases, Qt.Key.Key_Right)
        assert articles.isChecked() and not cases.isChecked()
        QTest.keyClick(articles, Qt.Key.Key_Left)
        assert cases.isChecked()
    finally:
        host.close()


# ----- buttons -----


def test_the_trace_button_carries_its_shortcut_in_the_same_button(qapp):
    button = TraceButton()
    assert button.objectName() == "primaryBtn"
    assert button.text() == "Trace the evidence Ctrl+Enter"
    assert button.accessibleName() == "Trace the evidence"
    assert button.accessibleDescription() == "Shortcut Ctrl+Enter"
    plain = TraceButton()
    plain.set_runs([plain.runs()[0]])
    assert button.sizeHint().width() > plain.sizeHint().width(), "the hint takes room of its own"


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_trace_button_text_is_drawn_in_the_on_accent_token(qapp, theme):
    apply_theme(qapp, theme)
    button = TraceButton()
    button.resize(button.sizeHint())
    button.show()
    qapp.processEvents()
    try:
        image = button.grab().toImage()
        assert count_near(image, token_color("on_accent"), 20) > 30
        assert count_near(image, token_color("rule_strong"), 20) < 10, "an enabled button does not use the disabled colour"
        button.setEnabled(False)
        qapp.processEvents()
        disabled = button.grab().toImage()
        assert count_near(disabled, token_color("rule_strong"), 20) > 20, "a disabled button's text is the muted rule colour"
    finally:
        button.close()
        apply_theme(qapp, "light")


# ----- local model button -----


def test_set_models_keeps_the_choice_and_never_counts_as_a_choice(qapp):
    button = LocalModelButton()
    chosen = []
    button.model_chosen.connect(chosen.append)
    button.set_models(["a", "b", "c"])
    assert button.selected_model() == "a"
    button.choose("b")
    button.set_models(["a", "b", "c", "d"])
    assert button.selected_model() == "b", "a refreshed list keeps the person's model"
    button.set_models(["a", "c"])
    assert button.selected_model() == "a", "a model that went away falls back to the first"
    button.set_models(["a", "c"], "c")
    assert button.selected_model() == "c"
    assert chosen == ["b"]
    button.choose("c")
    assert chosen == ["b"], "choosing the model already chosen is not a change"
    button.choose("not installed")
    assert button.selected_model() == "c"


def test_an_empty_list_says_why_and_offers_no_model(qapp):
    button = LocalModelButton()
    button.set_models([], placeholder="(Ollama stopped — choose Start Ollama)")
    assert button.selected_model() is None and button.model_actions() == []
    assert button.placeholder_text() == "(Ollama stopped — choose Start Ollama)"
    shown = [action for action in button.menu_widget.actions() if action.text() == "(Ollama stopped — choose Start Ollama)"]
    assert len(shown) == 1 and not shown[0].isEnabled()
    button.set_models([])
    assert button.placeholder_text() == "(AI unavailable · search still works)"


def _groups():
    from src.llm.cloud import ChoiceGroup, Choice

    return [
        ChoiceGroup("claude_api", "Claude API", (Choice("claude_api", "claude-opus-5-5", "Claude Opus 5.5"),)),
        ChoiceGroup("claude_cli", "Claude Code (your login)", (Choice("claude_cli", "", "Default model"),
                                                               Choice("claude_cli", "opus", "Opus"))),
        ChoiceGroup("codex_cli", "Codex (your login)", (Choice("codex_cli", "", "", "The 'codex' command was not found"),)),
    ]


def _menu_texts(button) -> list[str]:
    texts = []
    for action in button.menu_widget.actions():
        widget = getattr(action, "defaultWidget", lambda: None)()
        heading = widget.findChild(QLabel, "stepHeading") if widget is not None else None
        texts.append(f"# {heading.text()}" if heading is not None else action.text())
    return texts


def test_the_menu_groups_every_service_in_order_with_greyed_rows_saying_why(qapp):
    button = LocalModelButton()
    button.set_models(["gemma4:latest"], "gemma4:latest")
    button.set_cloud_groups(_groups())
    texts = _menu_texts(button)
    rows = texts[texts.index("# On this computer (Ollama)"):texts.index(button.unload_action.text())]
    assert [t for t in rows if t] == [
        "# On this computer (Ollama)", "gemma4:latest",
        "# Claude API", "Claude Opus 5.5",
        "# Claude Code (your login)", "Default model", "Opus",
        "# Codex (your login)", "The 'codex' command was not found",
    ]
    codex = button.cloud_actions()[-1]
    assert not codex.isEnabled() and not codex.isCheckable()
    button.set_models(["gemma4:latest", "qwen3:14b"], "gemma4:latest")
    assert _menu_texts(button).index("qwen3:14b") < _menu_texts(button).index("# Claude API"), \
        "a new Ollama list stays above the cloud rows"


def test_a_cloud_pick_is_asked_for_and_an_ollama_pick_takes_it_back(qapp):
    button = LocalModelButton()
    button.set_models(["gemma4:latest"], "gemma4:latest")
    button.set_cloud_groups(_groups())
    made, chosen = [], []
    button.choice_made.connect(lambda backend, model: made.append((backend, model)))
    button.model_chosen.connect(chosen.append)
    opus = next(action for action in button.cloud_actions() if action.text() == "Opus")
    opus.trigger()
    assert made == [("claude_cli", "opus")]
    button.set_cloud_groups(_groups(), ("claude_cli", "opus"), "Claude Code · opus")
    assert button.selected_model() == "Claude Code · opus"
    assert [a.text() for a in button.cloud_actions() if a.isChecked()] == ["Opus"]
    assert not any(a.isChecked() for a in button.model_actions())
    assert button.text().startswith("Claude Code · opus")
    opus.trigger()
    assert made == [("claude_cli", "opus")], "the row already answering is not a change"
    button.choose("gemma4:latest")
    assert made[-1] == ("ollama", "gemma4:latest") and chosen == ["gemma4:latest"], \
        "the Ollama model already marked still switches back from a cloud service"
    assert button.selected_model() == "gemma4:latest"


def test_a_chosen_service_that_cannot_answer_names_itself_but_offers_no_model(qapp):
    button = LocalModelButton()
    button.set_models(["gemma4:latest"], "gemma4:latest")
    button.set_cloud_groups(_groups(), ("claude_api", "claude-opus-5-5"), "Claude API · claude-opus-5-5",
                            usable=False)
    button.set_state("blocked")
    assert button.selected_model() is None
    assert button.text().startswith("Claude API · claude-opus-5-5") and "off for real cases" in button.text()


def test_a_disabled_action_says_why_and_an_enabled_one_says_what_it_does(qapp):
    button = LocalModelButton()
    assert not button.stop_action.isEnabled()
    assert button.stop_action.toolTip() == "Ollama is not running."
    button.stop_action.setEnabled(True)
    assert button.stop_action.toolTip() == "Stop Ollama and release its model memory."
    button.start_action.setEnabled(False)
    assert button.start_action.toolTip() == "Ollama is working. Try again when it finishes."


def test_the_menu_button_is_accessible_by_name_and_describes_its_state(qapp):
    button = LocalModelButton()
    button.set_models(["qwen3:14b"])
    button.set_state("ready")
    assert button.accessibleName() == "AI"
    assert button.menu_widget.accessibleName() == "AI menu"
    assert button.accessibleDescription() == "qwen3:14b ready"
    button.set_state("stopped")
    assert button.accessibleDescription() == "AI stopped"


def _heading(action) -> str:
    default = getattr(action, "defaultWidget", None)
    widget = default() if default is not None else None
    if widget is None:
        return ""
    heading = widget.findChild(QLabel, "stepHeading")
    return heading.text() if heading is not None else ""


def _long_api(names: list[str]):
    from src.llm.cloud import Choice, ChoiceGroup

    api = tuple(Choice("claude_api", f"id-{index}", name) for index, name in enumerate(names, start=1))
    reason = Choice("claude_api", "", "", "Could not list models: boom")
    return [
        ChoiceGroup("claude_api", "Claude API", api + (reason,)),
        ChoiceGroup("claude_cli", "Claude Code (your login)", (Choice("claude_cli", "sonnet", "Sonnet"),)),
    ]


def test_a_long_claude_api_list_keeps_four_inline_and_the_rest_in_a_submenu(qapp):
    from src.llm.cloud import service_note

    names = [f"Model {index}" for index in range(1, 7)]
    button = LocalModelButton()
    button.set_cloud_groups(_long_api(names))
    assert button.menu_widget.toolTipsVisible()
    more = next(action for action in button.menu_widget.actions() if action.menu() is not None)
    assert more.text() == "More Claude API models"
    assert [action.text() for action in more.menu().actions()] == ["Model 5", "Model 6"]
    inline = [action.text() for action in button.menu_widget.actions() if action.text().startswith("Model ")]
    assert inline == ["Model 1", "Model 2", "Model 3", "Model 4"]
    main_texts = [action.text() for action in button.menu_widget.actions()]
    assert "Could not list models: boom" in main_texts
    assert "Could not list models: boom" not in [action.text() for action in more.menu().actions()]
    assert [action.text() for action in button.cloud_actions() if action.text().startswith("Model ")] == names
    inline_row = next(action for action in button.menu_widget.actions() if action.text() == "Model 1")
    submenu_row = more.menu().actions()[0]
    assert submenu_row.isCheckable() and submenu_row.actionGroup() is inline_row.actionGroup()
    made = []
    button.choice_made.connect(lambda backend, model: made.append((backend, model)))
    more.menu().actions()[1].trigger()
    assert made == [("claude_api", "id-6")]

    button.set_cloud_groups(_long_api(names), ("claude_api", "id-6"), "Claude API · id-6")
    more = next(action for action in button.menu_widget.actions() if action.menu() is not None)
    assert [action.text() for action in more.menu().actions()] == ["Model 5"]
    inline = [action.text() for action in button.menu_widget.actions() if action.text().startswith("Model ")]
    assert inline == ["Model 1", "Model 2", "Model 3", "Model 4", "Model 6"]
    actions = list(button.menu_widget.actions())
    headings = [_heading(action) for action in actions]
    for title in ("Claude API", "Claude Code (your login)"):
        index = headings.index(title)
        assert actions[index - 1].isSeparator(), title
    assert next(a for a in button.cloud_actions() if a.text() == "Model 1").toolTip() == service_note("claude_api")
    assert next(a for a in button.cloud_actions() if a.text() == "Sonnet").toolTip() == service_note("claude_cli")
    greyed = next(a for a in button.cloud_actions() if a.text() == "Could not list models: boom")
    assert not greyed.isEnabled()


# ----- trail pieces -----


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_a_trail_step_draws_a_numbered_pin_and_a_filled_limit_pin(qapp, theme):
    apply_theme(qapp, theme)
    numbered, limit = TrailStep(2), TrailStep(None)
    for step in (numbered, limit):
        step.body.addWidget(QLabel("content"))
        step.resize(300, 120)
        step.show()
    qapp.processEvents()
    try:
        ink = token_color("ink")
        pin = (0, 0, 24, 24)
        outlined = numbered.grab().toImage().copy(*pin)
        filled = limit.grab().toImage().copy(*pin)
        assert count_near(filled, ink, 30) > count_near(outlined, ink, 30) * 2, "the limit pin is filled"
        spine = numbered.grab().toImage().copy(0, 40, 24, 60)
        paper = token_color("paper")
        drawn = sum(
            1 for x in range(spine.width()) for y in range(spine.height())
            if abs(QColor(spine.pixel(x, y)).red() - paper.red()) > 25
        )
        assert drawn > 40, "the spine runs down from the pin"
    finally:
        numbered.close()
        limit.close()
        apply_theme(qapp, "light")


def test_a_gap_in_the_samples_breaks_the_line_instead_of_joining_across_it(qapp):
    spark = Sparkline("waiting")
    spark.resize(400, 72)
    values = [20.0, 22.0, 21.0, None, None, None, None, None, 23.0, 22.0, 21.0, 20.0]
    spark.set_series(values, None)
    spark.show()
    qapp.processEvents()
    try:
        image = spark.grab().toImage()
        color = token_color("s_pip")
        width = image.width()
        gap = image.copy(int(width * 4 / 11) - 6, 0, int(width * 2 / 11), image.height())
        assert count_near(gap, color) == 0, "no line is drawn where samples are missing"
        assert count_near(image.copy(0, 0, int(width * 2 / 11), image.height()), color) > 10
        assert count_near(image.copy(int(width * 9 / 11), 0, int(width * 2 / 11), image.height()), color) > 10
    finally:
        spark.close()


def test_the_minute_line_shows_where_the_value_is_missing_and_the_trace_takes_its_signal_colour(qapp):
    spark = Sparkline("waiting")
    spark.resize(400, 72)
    spark.set_series([35.0, 36.0, None, 37.0, 35.0], 2, label="ETCO₂", color="s_etco2")
    spark.show()
    qapp.processEvents()
    try:
        image = spark.grab().toImage()
        x = round(2 + 2 * (image.width() - 4) / 4)
        line = image.copy(x - 2, 16, 5, image.height() - 24)
        assert count_near(line, token_color("accent")) > 20, "the observed minute is marked although its value is missing"
        assert count_near(image, token_color("s_etco2")) > 10
        assert count_near(image, token_color("s_pip")) == 0, "an ETCO2 trace is not drawn in the PIP colour"
        assert spark.signal() == ("ETCO₂", "s_etco2")
    finally:
        spark.close()


def test_a_sparkline_without_samples_shows_its_sentence(qapp):
    spark = Sparkline("PIP / source samples load on demand")
    assert not spark.has_series() and spark.state()[0] == "PIP / source samples load on demand"
    spark.set_series([1.0, 2.0], 1, "PIP over the case")
    assert spark.has_series() and spark.accessibleDescription() == "PIP over the case"
    spark.clear("PIP / no observed samples")
    assert not spark.has_series() and spark.state() == ("PIP / no observed samples", (), None)
