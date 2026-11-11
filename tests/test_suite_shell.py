"""The app frame: left rail, running head with the breadcrumb, footer fields, and no menu bar."""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDockWidget, QLabel, QPushButton, QToolBar

from src.__version__ import get_version
from src.desktop.app import MainWindow
from src.desktop.shell import DEMO_SOURCE, DEMO_STAMP, PRACTICE_SOURCE, RESEARCH_STAMP
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
def reloads(monkeypatch):
    """Calls of the Reload action. The window binds its handlers while it is built, so this patches first."""
    calls: list[bool] = []
    monkeypatch.setattr(MainWindow, "_autoload_processed", lambda self: calls.append(True))
    return calls


@pytest.fixture
def window(qapp, monkeypatch, tmp_path, reloads):
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
    # Settings is a page of the window, so it can be the marked place; Rules only opens the drawer
    assert [key for key, button in rail._buttons.items() if button.isCheckable()] == [
        "research", "cases", "signal", "practice", "settings"
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
    # Qt would drop the unchecked members of an exclusive group from the Tab order: press Tab and see where focus goes
    def tab_order(first: str, count: int) -> list[str]:
        start = window.rail.button(first)
        start.setFocus()
        qapp.processEvents()
        assert qapp.focusWidget() is start
        reached = [first]
        for _ in range(count - 1):
            QTest.keyClick(qapp.focusWidget(), Qt.Key.Key_Tab)
            qapp.processEvents()
            reached.append(next((key for key, button in window.rail._buttons.items()
                                 if button is qapp.focusWidget()), "<outside the rail>"))
        return reached

    window.rail.button("research").click()
    assert tab_order("research", 5) == ["research", "cases", "practice", "rules", "settings"]
    _open_case(window, qapp)
    assert tab_order("research", 6) == ["research", "cases", "signal", "practice", "rules", "settings"]
    # the same holds with another place marked
    window.rail.button("practice").click()
    qapp.processEvents()
    assert _checked(window) == ["practice"]
    assert tab_order("research", 6) == ["research", "cases", "signal", "practice", "rules", "settings"]


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


HEAD_LINES = {
    # the page: full form, short form, the sentence the accessible name and tooltip keep, the label's object name
    "research": ("○ De-identified research data · not for patient care", "○ Not for patient care",
                 "De-identified research data · not for patient care", "stamp"),
    "practice": ("Fictional practice · invented data, not patient records", "Fictional practice",
                 "Fictional practice: invented data, not patient records", "fictionLabel"),
    # research pages while the demo data is in use: never "research data", short form unchanged
    "demo": ("○ Fictional demo data · not for patient care", "○ Not for patient care",
             "Fictional demo data · not for patient care", "stamp"),
}


@pytest.mark.parametrize("mode", ["research", "practice", "demo"])
def test_the_head_line_is_never_cut_and_keeps_its_full_form_while_that_fits(qapp, mode):
    from PySide6.QtGui import QFontMetrics
    from PySide6.QtWidgets import QLabel, QWidget

    from src.desktop.shell import RunningHead, nav_icon
    from src.desktop.theme import apply_theme

    apply_theme(qapp, "light")
    practice = mode == "practice"
    full, short, sentence, name = HEAD_LINES[mode]
    holder = QWidget()  # a child is not held to its layout's least width the way a window is
    holder.resize(2000, 60)
    head = RunningHead(holder)
    head.set_demo(mode == "demo")
    head.set_place(["Research", "A long page path that has to shorten"], practice=practice)
    holder.show()
    # What the full form needs, measured on stand-ins so the test does not lean on the head's own arithmetic:
    # the line's text, Find as its icon alone, the least breadcrumb, and the head's margins and gaps.
    reference = QLabel(full, head)
    reference.setObjectName(name)
    reference.ensurePolished()
    line_width = QFontMetrics(reference.font()).horizontalAdvance(full)
    icon_only = QPushButton("", head)
    icon_only.setObjectName("findButton")
    icon_only.setIcon(nav_icon("find", 15))
    icon_only.ensurePolished()
    margins = head.layout().contentsMargins()
    full_fits_from = (margins.left() + margins.right() + 2 * head.layout().spacing() + head.crumbs.minimumSizeHint().width()
                      + icon_only.sizeHint().width() + line_width)
    line = head.fiction if practice else head.stamp
    seen = set()
    for width in range(head.minimumSizeHint().width(), full_fits_from + 240, 3):
        head.setGeometry(0, 0, width, 40)
        qapp.processEvents()
        text = line.text()
        seen.add(text)
        assert text in (full, short), (width, text)
        assert line.isVisible() and line.accessibleName() == sentence and line.toolTip() == sentence
        assert line.width() >= QFontMetrics(line.font()).horizontalAdvance(text), f"{text!r} is cut at {width}"
        assert 0 <= line.geometry().left() and line.geometry().right() < head.width()
        assert head.find_button.isVisible() and head.find_button.accessibleName() == "Find"
        assert head.crumbs.geometry().right() <= head.find_button.geometry().left()
        assert head.find_button.geometry().right() < line.geometry().left()
        if width >= full_fits_from + 8:
            assert text == full, f"the full form was dropped at {width} where it fits (from {full_fits_from})"
        if width < full_fits_from - 8:
            assert text == short, f"{text!r} shown at {width}, narrower than the full form needs ({full_fits_from})"
    assert seen == {full, short}
    holder.close()


FOOTER_FIELDS = {
    "counts": ("<b>19,114</b> indexed · <b>1,200</b> analyzed", "19,114 indexed, 1,200 analyzed"),
    "rules": ("rules <b>default</b> @0123456789ab", "rules default"),
    "model": ("local model <b>demo-model:3b-instruct-q4</b> ready", "local model demo-model ready"),
}


def _footer_state(field, where) -> str:
    """"hidden", "whole" or "shortened"; fails on a fragment: cut text, a missing ellipsis or a cut inside a word."""
    from PySide6.QtGui import QFontMetrics, QTextDocument

    if not field.isVisible():
        return "hidden"
    document = QTextDocument()
    document.setDefaultFont(field.font())
    document.setHtml(field._rich)
    full = document.toPlainText().split()
    text = field.text()
    if text == field._rich:
        assert field.width() >= document.idealWidth(), f"{full} is cut to {field.width()} px {where}"
        return "whole"
    assert text.endswith("…"), f"fragment without an ellipsis {where}: {text!r}"
    kept = text[:-1].split()
    assert kept and kept == full[:len(kept)] and len(kept) < len(full), f"cut inside a word {where}: {text!r}"
    assert " ".join(full) in field.toolTip(), "the full text is lost"
    plain = QTextDocument()
    plain.setHtml(text)
    assert field.width() >= QFontMetrics(field.font()).horizontalAdvance(plain.toPlainText()), \
        f"{text!r} does not fit its field {where}"
    return "shortened"


def test_a_footer_field_is_whole_or_shortened_to_whole_words_with_an_ellipsis_and_never_a_fragment(qapp):
    from PySide6.QtWidgets import QStatusBar, QWidget

    from src.desktop.shell import FooterFields
    from src.desktop.theme import apply_theme

    apply_theme(qapp, "light")
    holder = QWidget()  # a status bar of any width, not held to a window's least width
    bar = QStatusBar(holder)
    holder.resize(1500, 60)
    footer = FooterFields(bar)
    holder.show()
    footer.show_research(source_tip="EMR: /synthetic", **FOOTER_FIELDS)
    shown = [footer.source, footer.counts, footer.rules, footer.model]
    kinds = set()
    for width in range(40, 1400, 9):
        bar.setGeometry(0, 0, width, 40)
        qapp.processEvents()
        kinds |= {_footer_state(field, f"at {width} px") for field in shown}
    assert kinds == {"whole", "shortened", "hidden"}, kinds
    # where it is shown whole it keeps its bold parts
    bar.setGeometry(0, 0, 1400, 40)
    qapp.processEvents()
    assert footer.counts.text() == FOOTER_FIELDS["counts"][0] and "<b>" in footer.counts.text()
    assert "EMR: /synthetic" in footer.source.toolTip()
    holder.close()


def test_the_footer_has_no_fragment_when_the_window_first_appears(qapp, monkeypatch, tmp_path):
    """The status bar's one resize comes before it counts as visible; showing it must fit the fields as well."""
    from src.desktop.theme import apply_theme

    apply_theme(qapp, "light")
    for name, folder in (("MOVER_CONFIG_DIR", "config"), ("MOVER_EMR_DIR", "EMR"), ("MOVER_PROCESSED_DIR", "processed")):
        monkeypatch.setenv(name, str(tmp_path / folder))
    monkeypatch.setattr(MainWindow, "_startup", lambda self: None)
    win = MainWindow()
    win._footer.show_research(source_tip="EMR: /synthetic", **FOOTER_FIELDS)
    win.resize(700, 600)
    win.show()  # nothing resizes the footer after this
    qapp.processEvents()
    footer = win._footer
    states = {_footer_state(field, "when first shown at 700 px")
              for field in (footer.source, footer.counts, footer.rules, footer.model)}
    assert "shortened" in states, "700 px is too narrow for the long footer, so some field must shorten"
    win.close()


def test_the_window_can_shrink_to_a_double_scaled_screen_with_the_longest_footer(window, qapp):
    """A 2x screen is a few hundred logical pixels wide: the footer and head shorten their text, never widen the window."""
    window._footer.show_research(
        source_tip="",
        counts=("<b>19,114</b> indexed · <b>1,200</b> analyzed", "19,114 indexed, 1,200 analyzed"),
        rules=("rules default @0123456789ab", "rules default"),
        model=("demo-model:3b-instruct-q4 ready", "model ready"),
    )
    qapp.processEvents()
    assert window.minimumSizeHint().width() <= 400
    window.rail.button("practice").click()  # the fiction label is the longer line on practice pages
    qapp.processEvents()
    assert window.minimumSizeHint().width() <= 400
    assert window.head.stamp.toolTip() == window.head.stamp.accessibleName()
    assert window.head.fiction.toolTip() == window.head.fiction.accessibleName()


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


def _open_medication_page(window, qapp, monkeypatch, *, pid=None):
    """Open Practice / Medication and vitals, with ``pid`` as the case open in Research (or none)."""
    import pandas as pd

    medications = pd.DataFrame(
        [{"Drug_name": "Drug A", "Dose": 5, "Drug_units": "mg", "Start_time": "2000-01-01 08:10:00", "End_time": r"\N"}]
    )
    monkeypatch.setattr("src.llm.case_context.load_case_medications", lambda _pid: medications)
    window.research._current_pid = pid
    if pid is not None:
        window.research._case_views[pid] = SimpleNamespace(
            timeseries=pd.DataFrame([{"t_min": 0, "HR": 70}]), cases=pd.DataFrame())
    window.rail.button("practice").click()
    qapp.processEvents()
    window.practice_tabs.bar.setCurrentIndex(1)
    qapp.processEvents()
    return window.module_stack.currentWidget()


def test_medication_page_with_an_open_case_is_not_called_invented_data(window, qapp, monkeypatch):
    """The page then shows the open case's drugs and vitals, so the head and footer name the data in use."""
    panel = _open_medication_page(window, qapp, monkeypatch, pid="SYN-1")
    head, footer = window.head, window._footer
    assert panel.shows_open_case() and "SYN-1 · open case · de-identified research data" in panel.case_line.text()
    assert head.stamp.isVisibleTo(window) and not head.fiction.isVisibleTo(window)
    assert head.property("practice") is False and head.stamp.accessibleName() == RESEARCH_STAMP
    assert footer.source.accessibleName() != PRACTICE_SOURCE and not footer.practice_note.isVisibleTo(window)
    assert footer.counts.isVisibleTo(window)
    # the other practice tools still hold only invented material
    window.practice_tabs.bar.setCurrentIndex(0)
    qapp.processEvents()
    assert head.fiction.isVisibleTo(window) and not head.stamp.isVisibleTo(window)
    assert footer.source.accessibleName() == PRACTICE_SOURCE and footer.practice_note.isVisibleTo(window)


def test_medication_page_without_an_open_case_shows_its_invented_record_under_the_fiction_line(window, qapp, monkeypatch):
    panel = _open_medication_page(window, qapp, monkeypatch, pid=None)
    assert not panel.shows_open_case() and panel.case_line.text().endswith("invented record")
    assert window.head.fiction.isVisibleTo(window) and not window.head.stamp.isVisibleTo(window)
    assert window._footer.source.accessibleName() == PRACTICE_SOURCE


def test_medication_page_names_an_open_demo_case_as_fictional_demo_data(window, qapp, monkeypatch):
    monkeypatch.setattr("src.desktop.app.demo_data_in_use", lambda: True)
    # a reload drops the loaded data, and from then on the window names what it shows after the folder in use
    window._clear_loaded_sample()
    panel = _open_medication_page(window, qapp, monkeypatch, pid="SYN-1")
    assert "SYN-1 · open case · fictional demo data, not for patient care" in panel.case_line.text()
    assert "research data" not in panel.case_line.text()
    assert window.head.stamp.isVisibleTo(window) and window.head.stamp.accessibleName() == DEMO_STAMP
    assert window._footer.source.accessibleName() == DEMO_SOURCE[1]


def test_medication_page_follows_the_open_case_when_it_closes_while_the_page_shows(window, qapp, monkeypatch):
    panel = _open_medication_page(window, qapp, monkeypatch, pid="SYN-1")
    assert panel.shows_open_case()
    # a data reload closes the open case while Medication and vitals is still the page on screen
    window.research.invalidate_case_data()
    qapp.processEvents()
    assert not panel.shows_open_case() and panel.case_line.text().endswith("invented record")
    assert window.head.fiction.isVisibleTo(window) and not window.head.stamp.isVisibleTo(window)
    assert window._footer.source.accessibleName() == PRACTICE_SOURCE


def test_a_folder_change_does_not_rename_the_data_still_on_screen(window, qapp, monkeypatch):
    """Between saving another data folder and the end of its reload, the case on screen is still the old data."""
    panel = _open_medication_page(window, qapp, monkeypatch, pid="SYN-1")
    monkeypatch.setattr("src.desktop.app.demo_data_in_use", lambda: True)
    window._go("settings")
    qapp.processEvents()
    window._go("medications")
    qapp.processEvents()
    assert "de-identified research data" in panel.case_line.text()
    assert window.head.stamp.accessibleName() == RESEARCH_STAMP
    assert window._footer.source.accessibleName() != DEMO_SOURCE[1]
    # another case of the old data opened meanwhile is named as the old data too
    window.research._case_views["SYN-2"] = window.research._case_views["SYN-1"]
    window.research._current_pid = "SYN-2"
    window._sync_frame()
    assert panel.case_line.text().startswith("SYN-2 · open case · de-identified research data")
    # the reload drops the old data: the case closes, and what loads next is named as demo data
    window._clear_loaded_sample()
    qapp.processEvents()
    assert not panel.shows_open_case() and panel.case_line.text().endswith("invented record")
    window.rail.button("research").click()
    qapp.processEvents()
    assert window.head.stamp.accessibleName() == DEMO_STAMP and window._footer.source.accessibleName() == DEMO_SOURCE[1]


def test_the_same_case_number_after_a_reload_is_read_again(window, qapp, monkeypatch):
    """Another data folder can hold a case with the same number; its drugs are that folder's, not the old one's."""
    import pandas as pd

    panel = _open_medication_page(window, qapp, monkeypatch, pid="SYN-1")
    assert "Drug A" in " ".join(panel.table.item(row, 1).text() for row in range(panel.table.rowCount()))
    window.rail.button("research").click()
    qapp.processEvents()
    window._clear_loaded_sample()  # the reload after a folder change
    other = pd.DataFrame(
        [{"Drug_name": "Drug Z", "Dose": 1, "Drug_units": "mg", "Start_time": "2000-01-01 08:10:00", "End_time": r"\N"}]
    )
    monkeypatch.setattr("src.llm.case_context.load_case_medications", lambda _pid: other)
    window.research._current_pid = "SYN-1"
    window.research._case_views["SYN-1"] = SimpleNamespace(
        timeseries=pd.DataFrame([{"t_min": 0, "HR": 70}]), cases=pd.DataFrame())
    window._go("medications")
    qapp.processEvents()
    drugs = " ".join(panel.table.item(row, 1).text() for row in range(panel.table.rowCount()))
    assert "Drug Z" in drugs and "Drug A" not in drugs


def test_the_medication_page_reads_a_case_from_disk_once_not_on_every_frame_update(window, qapp, monkeypatch):
    panel = _open_medication_page(window, qapp, monkeypatch, pid="SYN-1")
    calls = []
    monkeypatch.setattr("src.llm.case_context.load_case_medications", lambda pid: calls.append(pid) or None)
    for _ in range(3):
        window._sync_frame()
    assert calls == [] and panel.shows_open_case()


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


def _ink_of(button, size: int = 22) -> str:
    from PySide6.QtGui import QIcon

    image = button.icon().pixmap(size, QIcon.Mode.Normal, QIcon.State.Off).toImage()
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
        assert _ink_of(window.head.find_button, 15) == tokens("dark")["ink_3"]
        apply_theme(qapp, "light")
        qapp.processEvents()
        assert _ink_of(window.head.find_button, 15) == tokens("light")["ink_3"]
    finally:
        apply_theme(qapp, "light")


def test_first_focus_is_the_question_field(window, qapp):
    window.activateWindow()
    qapp.processEvents()
    assert qapp.focusWidget() is window.research.question_edit


def test_ctrl_r_opens_the_rules_from_a_rail_button_and_does_not_reload(window, qapp, reloads):
    window.activateWindow()
    for widget in (window.rail.button("research"), window.rail.button("cases"), window.head.find_button):
        window.research.rules_drawer.close_drawer()
        window.research.show_canvas()
        widget.setFocus()
        qapp.processEvents()
        assert qapp.focusWidget() is widget
        QTest.keyClick(widget, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier)
        qapp.processEvents()
        assert window.research.rules_drawer.is_open, type(widget).__name__
    assert reloads == []


def test_ctrl_r_from_the_rail_works_on_the_cases_and_signal_pages_too(window, qapp, reloads):
    window.activateWindow()
    window.rail.button("cases").click()
    window.rail.button("cases").setFocus()
    QTest.keyClick(window.rail.button("cases"), Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier)
    qapp.processEvents()
    assert window.research.rules_drawer.is_open
    window.research.rules_drawer.close_drawer()

    _open_case(window, qapp)
    window.rail.button("signal").setFocus()
    QTest.keyClick(window.rail.button("signal"), Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier)
    qapp.processEvents()
    assert window.research.rules_drawer.is_open
    assert reloads == []


def test_ctrl_r_on_a_practice_page_still_reloads(window, qapp, reloads):
    window.activateWindow()
    window.rail.button("practice").click()
    qapp.processEvents()
    window.rail.button("practice").setFocus()
    QTest.keyClick(window.rail.button("practice"), Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier)
    qapp.processEvents()
    assert reloads == [True]
    assert not window.research.rules_drawer.is_open


def test_escape_on_the_case_page_works_with_focus_on_a_rail_button(window, qapp):
    window.activateWindow()
    _open_case(window, qapp)
    assert window.research.stack.currentIndex() == 1
    button = window.rail.button("signal")
    button.setFocus()
    qapp.processEvents()
    QTest.keyClick(button, Qt.Key.Key_Escape)
    qapp.processEvents()
    assert window.research.stack.currentIndex() == 0


def test_load_confirmation_is_short_clears_and_holds_no_paths(window, qapp, tmp_path):
    bar = window.statusBar()
    shown: list[tuple[str, int]] = []
    real = bar.showMessage
    bar.showMessage = lambda message, timeout=0: (shown.append((message, timeout)), real(message, timeout))[1]
    try:
        for practice in (False, True):
            if practice:
                window.rail.button("practice").click()
                qapp.processEvents()
            shown.clear()
            window._on_pipeline_ok(tuple(pd.DataFrame() for _ in range(5)))
            qapp.processEvents()
            assert shown == [("Loaded 0 cases", 6000)]
            message = bar.currentMessage()
            assert str(tmp_path) not in message and "EMR:" not in message and "Processed:" not in message
            assert "min rows" not in message and "flags" not in message, "the footer fields show the counts"
    finally:
        del bar.showMessage
    # the folders stay in the source field's tooltip
    window.rail.button("research").click()
    qapp.processEvents()
    assert str(tmp_path / "EMR") in window._footer.source.toolTip()


def test_the_rail_runs_the_full_window_height_and_the_footer_starts_right_of_it(window, qapp):
    bar = window.statusBar()
    assert window.centralWidget().isAncestorOf(bar)
    rail_top = window.rail.mapTo(window, window.rail.rect().topLeft())
    rail_bottom = window.rail.mapTo(window, window.rail.rect().bottomLeft())
    bar_top_left = bar.mapTo(window, bar.rect().topLeft())
    bar_bottom_right = bar.mapTo(window, bar.rect().bottomRight())
    assert rail_top.y() == 0 and rail_bottom.y() + 1 == window.centralWidget().height() == window.height()
    assert bar_top_left.x() >= window.rail.width()
    assert bar_bottom_right.y() + 1 == window.height(), "the footer is the bottom edge of the window"
    assert bar_bottom_right.x() + 1 == window.width()
    assert bar.isVisible() and bar.height() > 0


def _left_strip(button, x: int) -> str:
    image = button.grab().toImage()
    return image.pixelColor(x, image.height() // 2).name().upper()


def test_the_current_place_keeps_its_mark_while_it_has_keyboard_focus(window, qapp):
    from src.desktop.theme import tokens

    window.activateWindow()
    accent = tokens("light")["accent"].upper()
    current = window.rail.button("research")
    other = window.rail.button("cases")
    assert _left_strip(current, 1) == accent  # the mark alone
    current.setFocus()
    qapp.processEvents()
    assert current.hasFocus()
    # ring (2 px) and mark (5 px on the left) are both there
    assert _left_strip(current, 1) == accent and _left_strip(current, 4) == accent
    assert current.grab().toImage().pixelColor(32, 0).name().upper() == accent, "the ring runs along the top"
    # a focused item that is not the current place has the ring only
    other.setFocus()
    qapp.processEvents()
    assert _left_strip(other, 1) == accent and _left_strip(other, 4) != accent


def test_ctrl_1_returns_to_where_research_was_while_the_rail_item_opens_the_board(window, qapp):
    window.activateWindow()
    research = window.research
    _open_case(window, qapp)
    research.minute_slider.setValue(2)
    research.selected_rule_id = "demo-rule"
    qapp.processEvents()
    crumbs = window.head.crumb_text()
    assert crumbs.endswith("Minute 2")

    for key in ("handoff", "medications", "who"):
        window._go(key)
        qapp.processEvents()
        assert window.module_stack.currentWidget() is not research
        QTest.keyClick(window, Qt.Key.Key_1, Qt.KeyboardModifier.ControlModifier)
        qapp.processEvents()
        assert window.module_stack.currentWidget() is research
        assert research.stack.currentIndex() == 1, "the case page, not the question board"
        assert window.head.crumb_text() == crumbs
        assert research.selected_rule_id == "demo-rule"
        assert _checked(window) == ["signal"]

    window._go("cases")
    window._go("handoff")
    QTest.keyClick(window, Qt.Key.Key_1, Qt.KeyboardModifier.ControlModifier)
    qapp.processEvents()
    assert research.stack.currentIndex() == 2 and window.head.crumb_text() == "Cases"

    window._go("handoff")
    window.rail.button("research").click()
    qapp.processEvents()
    assert research.stack.currentIndex() == 0, "the rail item still opens the question board"


def test_escape_from_rules_opened_on_the_rail_returns_focus_to_the_rail_button(window, qapp):
    rules = window.rail.button("rules")
    rules.setFocus()
    rules.click()
    qapp.processEvents()
    drawer = window.research.rules_drawer
    assert drawer.is_open and drawer.search_edit.hasFocus()
    QTest.keyClick(drawer.search_edit, Qt.Key.Key_Escape)
    qapp.processEvents()
    assert not drawer.is_open
    assert qapp.focusWidget() is rules
