"""The app frame: left rail, running head with the breadcrumb, footer fields, and no menu bar."""

from __future__ import annotations

import pandas as pd
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDockWidget, QLabel, QPushButton, QToolBar

from src.__version__ import get_version
from src.desktop.app import MainWindow
from src.desktop.suite_chrome import install_suite_chrome
from src.llm.service import OllamaStatus
from src.services.case_fetch import FetchResult
from src.services.retrieval import RetrievalResult, SearchCandidate


def _suite_title() -> str:
    return f"MOVER Suite v{get_version()}"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, monkeypatch, tmp_path):
    monkeypatch.setenv("MOVER_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("MOVER_EMR_DIR", str(tmp_path / "EMR"))
    monkeypatch.setenv("MOVER_PROCESSED_DIR", str(tmp_path / "processed"))
    monkeypatch.setattr(MainWindow, "_startup", lambda self: None)
    win = MainWindow()
    win.show()
    qapp.processEvents()
    yield win
    win.close()
    win.deleteLater()
    qapp.processEvents()


def _open_case(window, qapp, pid="SYN-DEMO-014", *, from_overview=False):
    """Show a synthetic case on the Signal page, as a click on Open at its peak minute would."""
    candidate = SearchCandidate(pid, "analyzed case", True, "Synthetic procedure", 55, "F",
                                "Synthetic source", "keyword", 1.0)
    window.research.set_results(RetrievalResult(candidates=(candidate,), indexed_count=19_114))
    ts = pd.DataFrame({"t_min": [0.0, 1.0, 2.0], "PIP": [20.0, 31.4, 21.0], "ETCO2": [35.0, 36.0, 37.0],
                       "HR": [70.0, 71.0, 72.0]})
    fetched = FetchResult(pd.DataFrame({"PID": [pid], "Age": [55]}), ts, pd.DataFrame(),
                          pd.DataFrame(), pd.DataFrame())
    window.research.set_case_data(pid, ts, pd.DataFrame(), fetched=fetched, open_detail=True,
                                  from_overview=from_overview)
    qapp.processEvents()


def _checked(window) -> list[str]:
    return [key for key, button in window.rail._buttons.items() if button.isChecked()]


def test_main_window_has_the_rail_the_head_and_no_menu_bar(window):
    assert window.windowTitle() == _suite_title()
    assert "MOVER SIS Monitor" not in window.windowTitle()
    assert window.module_stack.currentWidget() is window.research
    assert window.centralWidget().isAncestorOf(window.module_stack)
    # menuBar() would create one, so ask for the widget that exists only when something made it
    assert window.menuWidget() is None
    assert window.findChildren(QToolBar) == []
    assert window.findChildren(QDockWidget) == []

    rail = window.rail
    assert rail.accessibleName() == "Main navigation"
    assert rail.width() == 64
    top = [button.text() for button in rail.findChildren(type(rail.button("research")))]
    assert top == ["Research", "Cases", "Signal", "Practice", "Rules", "Settings"]
    for key, name in (("research", "Research"), ("cases", "Cases"), ("signal", "Signal"),
                      ("practice", "Practice"), ("rules", "Rules"), ("settings", "Settings")):
        button = rail.button(key)
        assert button.accessibleName() == name
        assert button.focusPolicy() == Qt.FocusPolicy.StrongFocus
        assert button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonTextUnderIcon
        assert not button.icon().isNull()
    assert [key for key, button in rail._buttons.items() if button.isCheckable()] == [
        "research", "cases", "signal", "practice"
    ]
    assert _checked(window) == ["research"]

    head = window.head
    assert head.crumb_text() == "Research"
    assert head.crumbs.accessibleName() == "Current place: Research"
    assert head.stamp.text().endswith("De-identified research data · not for patient care")
    assert head.stamp.isVisibleTo(window) and not head.fiction.isVisibleTo(window)
    assert head.find_button.accessibleName() == "Find"
    assert "Ctrl+K" in head.find_button.text()
    assert not any(label.text() == "Monitoring" for label in window.findChildren(QLabel))


def test_exactly_one_place_is_marked_and_every_item_is_a_tab_stop(window, qapp):
    for key in ("cases", "signal", "practice", "research"):
        if key == "signal":
            continue  # disabled until a case is open
        window.rail.button(key).click()
        qapp.processEvents()
        assert _checked(window) == [key]
    # a second click on the current place keeps its mark
    window.rail.button("research").click()
    assert _checked(window) == ["research"]
    # Qt would drop the unchecked members of an exclusive group from the Tab order; none may be dropped
    _open_case(window, qapp)
    assert all(button.focusPolicy() == Qt.FocusPolicy.StrongFocus for button in window.rail._buttons.values())


def test_rail_head_and_footer_stay_inside_a_900_pixel_window(window, qapp):
    window.resize(900, 640)
    window.show()
    qapp.processEvents()

    assert window.width() <= 900
    for widget in (window.rail, window.head, window.head.crumbs, window.head.find_button, window.head.stamp):
        assert widget.isVisible() and widget.width() > 0 and widget.height() > 0
        top_left = widget.mapTo(window, widget.rect().topLeft())
        assert 0 <= top_left.x() and top_left.x() + widget.width() <= 900
    assert window.head.crumbs.geometry().right() < window.head.find_button.geometry().left()
    assert window.head.find_button.geometry().right() < window.head.stamp.geometry().left()
    footer = window._footer
    assert footer.model.isVisible()
    assert footer.model.mapTo(window, footer.model.rect().bottomRight()).x() <= 900


def test_repeated_install_leaves_one_rail_and_the_same_title(window, qapp):
    title = window.windowTitle()
    rail, head, research = window.rail, window.head, window.research

    install_suite_chrome(window, get_version())
    install_suite_chrome(window, get_version())
    qapp.processEvents()

    assert window.windowTitle() == title == _suite_title()
    assert window.rail is rail and window.head is head
    assert len(window.findChildren(type(rail))) == 1
    assert window.module_stack.currentWidget() is research


def test_rail_places_open_their_pages_and_the_breadcrumb_follows(window, qapp):
    window.rail.button("cases").click()
    qapp.processEvents()
    assert window.research.stack.currentIndex() == 2
    assert _checked(window) == ["cases"]
    assert window.head.crumb_text() == "Cases"
    assert window.windowTitle() == f"Cases — {_suite_title()}"

    window.rail.button("research").click()
    qapp.processEvents()
    assert window.research.stack.currentIndex() == 0
    assert _checked(window) == ["research"]
    assert window.head.crumb_text() == "Research"
    assert window.windowTitle() == _suite_title()


def test_signal_is_disabled_with_a_reason_until_a_case_is_open(window, qapp):
    button = window.rail.button("signal")
    assert not button.isEnabled()
    assert "open case" in button.toolTip().lower()
    assert button.accessibleDescription() == button.toolTip()
    assert not window._nav_signal.isEnabled()

    _open_case(window, qapp)
    assert window.research.stack.currentIndex() == 1
    assert button.isEnabled() and _checked(window) == ["signal"]
    assert window._nav_signal.isEnabled()
    assert window.head.crumb_text() == "Research / SYN-DEMO-014 / Minute 1"
    assert window.windowTitle() == f"Case SYN-DEMO-014 — {_suite_title()}"

    window.rail.button("research").click()
    qapp.processEvents()
    assert window.research.stack.currentIndex() == 0
    assert button.isEnabled()  # the case stays open behind the board
    window.rail.button("signal").click()
    qapp.processEvents()
    assert window.research.stack.currentIndex() == 1
    assert _checked(window) == ["signal"]

    window.research.invalidate_case_data()
    qapp.processEvents()
    assert not button.isEnabled() and window.research.stack.currentIndex() == 0


def test_breadcrumb_names_the_question_the_case_and_the_minute(window, qapp):
    research = window.research
    assert window.head.crumb_text() == "Research"
    research.begin_question("First question", 1)
    research.set_answer("First answer", 1)
    research.set_searching(False)
    research.begin_question("Second question", 2)
    qapp.processEvents()
    assert window.head.crumb_text() == "Research / Question 2 of 2"
    research.set_answer("Second answer", 2)
    research.set_searching(False)
    research.show_turn(1)
    qapp.processEvents()
    assert window.head.crumb_text() == "Research / Question 1 of 2"

    _open_case(window, qapp)
    assert window.head.crumb_text() == "Research / Question 1 / SYN-DEMO-014 / Minute 1"
    research.minute_slider.setValue(2)
    qapp.processEvents()
    assert window.head.crumb_text() == "Research / Question 1 / SYN-DEMO-014 / Minute 2"
    assert window.head.crumbs.parts()[-1] == "Minute 2"


def test_a_case_opened_from_the_cases_overview_is_named_under_cases(window, qapp):
    _open_case(window, qapp, from_overview=True)
    assert window.head.crumb_text() == "Cases / SYN-DEMO-014 / Minute 1"


def test_practice_is_one_rail_item_with_the_three_tools_as_tabs(window, qapp):
    tabs = window.practice_tabs
    assert not tabs.isVisibleTo(window)

    window.rail.button("practice").click()
    qapp.processEvents()
    assert window.module_stack.currentWidget().objectName() == "handoff-practice"
    assert _checked(window) == ["practice"]
    assert tabs.isVisibleTo(window)
    assert [tabs.bar.tabText(index) for index in range(tabs.bar.count())] == [
        "Handoff practice", "Medication and vitals", "WHO checklist",
    ]
    assert tabs.bar.accessibleName() == "Practice tools"
    assert window.head.crumb_text() == "Practice / Handoff practice"
    assert window.head.fiction.isVisibleTo(window) and not window.head.stamp.isVisibleTo(window)
    assert window.head.property("practice") is True

    tabs.bar.setCurrentIndex(1)
    qapp.processEvents()
    assert window.module_stack.currentWidget().objectName() == "medication-vitals"
    assert window.head.crumb_text() == "Practice / Medication and vitals"
    assert window.windowTitle() == f"Medication and vitals — {_suite_title()}"
    assert _checked(window) == ["practice"]

    window.rail.button("research").click()
    qapp.processEvents()
    assert window.module_stack.currentWidget() is window.research
    assert not tabs.isVisibleTo(window)
    assert window.head.stamp.isVisibleTo(window)
    # Practice remembers its last tool
    window.rail.button("practice").click()
    qapp.processEvents()
    assert window.head.crumb_text() == "Practice / Medication and vitals"


def test_rules_item_opens_the_drawer_from_any_page_and_settings_opens_settings(window, qapp, monkeypatch):
    window.rail.button("practice").click()
    qapp.processEvents()
    window.rail.button("rules").click()
    qapp.processEvents()
    assert window.module_stack.currentWidget() is window.research
    assert window.research.rules_drawer.is_open
    assert not window.rail.button("rules").isCheckable()

    opened = []
    monkeypatch.setattr(MainWindow, "_open_settings", lambda self: opened.append(True))
    window._go("settings")
    assert opened == [True]


def test_footer_shows_source_counts_rules_and_model_and_keeps_status_messages(window, qapp):
    footer = window._footer
    assert footer.source.text() == "MOVER SIS local copy"
    assert "EMR:" in footer.source.toolTip() and "Processed:" in footer.source.toolTip()
    assert "no analyzed cases loaded" in footer.counts.text()
    assert "rules <b>default</b>" in footer.rules.text()
    assert "not checked" in footer.model.text()

    window.cases = pd.DataFrame({"PID": [f"SYN-{n}" for n in range(28)]})
    window.research.set_results(RetrievalResult(candidates=(), indexed_count=19_114))
    window._thresholds_sha256 = "c66420" + "0" * 58
    window._refresh_footer()
    assert footer.counts.text() == "<b>28</b> analyzed of <b>19,114</b> indexed"
    assert footer.counts.accessibleName() == "28 analyzed of 19,114 indexed"
    assert footer.rules.text() == "rules <b>default</b> @c66420"
    assert footer.counts.font().featureValue(QFont.Tag("tnum")) == 1
    assert not footer.rules.font().featureTags()

    window._apply_ollama_status(OllamaStatus(True, "ready", ["demo-model:3b"], "http://127.0.0.1:11434"))
    assert footer.model.text() == "local model <b>demo-model:3b</b> ready"
    window._apply_ollama_status(OllamaStatus(False, "stopped", [], "http://127.0.0.1:11434"))
    assert footer.model.text() == "local model <b>stopped</b>"

    window.statusBar().showMessage("A temporary message")
    assert window.statusBar().currentMessage() == "A temporary message"
    buttons = window.statusBar().findChildren(QPushButton)
    assert window._update_button in buttons and window._scan_cancel_button in buttons


def test_practice_footer_says_it_reads_no_data_folder(window, qapp):
    window.rail.button("practice").click()
    qapp.processEvents()
    footer = window._footer
    assert footer.source.accessibleName() == "Practice reads no data folder"
    assert not footer.counts.isVisibleTo(window) and not footer.model.isVisibleTo(window)
    assert footer.practice_note.isVisibleTo(window)
    window.rail.button("research").click()
    qapp.processEvents()
    assert footer.counts.isVisibleTo(window) and not footer.practice_note.isVisibleTo(window)


def test_rail_item_is_reachable_and_activates_by_keyboard(window, qapp):
    button = window.rail.button("cases")
    button.setFocus()
    qapp.processEvents()
    assert button.hasFocus()
    QTest.keyClick(button, Qt.Key.Key_Space)
    qapp.processEvents()
    assert window.research.stack.currentIndex() == 2
    assert _checked(window) == ["cases"]


def _ink_of(button) -> str:
    from PySide6.QtGui import QIcon

    image = button.icon().pixmap(22, QIcon.Mode.Normal, QIcon.State.Off).toImage()
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if color.alpha() == 255:
                return color.name().upper()
    raise AssertionError("the icon draws nothing")


def test_rail_icons_follow_the_theme(window, qapp):
    from src.desktop.theme import apply_theme, tokens

    try:
        apply_theme(qapp, "light")
        qapp.processEvents()
        assert _ink_of(window.rail.button("cases")) == tokens("light")["ink_3"]
        apply_theme(qapp, "dark")
        qapp.processEvents()
        assert _ink_of(window.rail.button("cases")) == tokens("dark")["ink_3"]
        assert _ink_of(window.rail.button("settings")) == tokens("dark")["ink_3"]
    finally:
        apply_theme(qapp, "light")
