"""The shell opens a working practice module and can return to Monitoring."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from src.desktop.app import MainWindow


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


def test_view_menu_opens_handoff_practice_and_monitoring_returns(window, qapp):
    assert window.module_stack.currentWidget() is window.research
    window._module_actions["handoff"].trigger()
    qapp.processEvents()
    assert window.module_stack.currentWidget().objectName() == "handoff-practice"
    assert window.menuBar().cornerWidget(Qt.Corner.TopLeftCorner).text() == "Handoff"
    assert window._module_actions["handoff"].shortcut().toString() == "Ctrl+2"

    QTest.keyClick(window, Qt.Key.Key_1, Qt.KeyboardModifier.ControlModifier)
    qapp.processEvents()
    assert window.module_stack.currentWidget() is window.research
    assert window.menuBar().cornerWidget(Qt.Corner.TopLeftCorner).text() == "Monitoring"


def test_medication_page_uses_the_fictional_demo_when_no_case_is_open(window, qapp):
    from PySide6.QtWidgets import QLabel

    window._module_actions["medications"].trigger()
    qapp.processEvents()
    page = window.module_stack.currentWidget()
    assert page.objectName() == "medication-vitals"
    assert page.table.rowCount() > 0
    assert "Fictional demonstration" in page.note.text()
    joined = " ".join(
        page.table.item(row, 1).text()
        for row in range(page.table.rowCount())
        if page.table.item(row, 1) is not None
    )
    assert "Phenylephrine" in joined
    notices = [label.text() for label in page.findChildren(QLabel)]
    assert any("do not say why a drug was given" in text for text in notices)


def _accessible(widget):
    from PySide6.QtGui import QAccessible

    return QAccessible.queryAccessibleInterface(widget)


def test_tab_leaves_the_handoff_text_and_the_medication_table(window, qapp):
    window._module_actions["handoff"].trigger()
    qapp.processEvents()
    handoff = window.module_stack.currentWidget().handoff
    handoff.setFocus()
    QTest.keyClick(handoff, Qt.Key.Key_Tab)
    qapp.processEvents()
    assert "\t" not in handoff.toPlainText()
    assert not handoff.hasFocus()

    window._module_actions["medications"].trigger()
    qapp.processEvents()
    table = window.module_stack.currentWidget().table
    assert table.rowCount() > 1
    table.setFocus()
    table.setCurrentCell(0, 0)
    QTest.keyClick(table, Qt.Key.Key_Tab)
    qapp.processEvents()
    # Tab no longer walks cell by cell (arrows do). The table is the page's only focus stop, so focus may wrap to it.
    assert (table.currentRow(), table.currentColumn()) == (0, 0)
    assert not table.tabKeyNavigation()


def test_practice_combos_announce_their_purpose_and_labels_their_text(window, qapp):
    from PySide6.QtGui import QAccessible

    window._module_actions["handoff"].trigger()
    qapp.processEvents()
    handoff = window.module_stack.currentWidget()
    combo = _accessible(handoff.case_box)
    assert combo.text(QAccessible.Text.Name) == "Fictional scenario"
    assert combo.text(QAccessible.Text.Value) == handoff.case_box.currentText()
    history = _accessible(handoff.history_label).text(QAccessible.Text.Name)
    assert history == handoff.history_label.text() and history

    window._module_actions["who"].trigger()
    qapp.processEvents()
    who = window.module_stack.currentWidget()
    assert _accessible(who.scenario_box).text(QAccessible.Text.Name) == "Fictional checklist card"
    stage = _accessible(who.stage_label).text(QAccessible.Text.Name)
    assert stage == who.stage_label.text() and stage

    window._module_actions["medications"].trigger()
    qapp.processEvents()
    meds = window.module_stack.currentWidget()
    note = _accessible(meds.note).text(QAccessible.Text.Name)
    assert note == meds.note.text() and note


def test_the_window_title_names_the_open_page(window, qapp):
    monitoring = window.windowTitle()
    for name, page in (("handoff", "Handoff practice"), ("medications", "Medication and vitals"),
                       ("who", "WHO checklist")):
        window._module_actions[name].trigger()
        qapp.processEvents()
        assert window.windowTitle() == f"{page} — {monitoring}"
    window._module_actions["monitoring"].trigger()
    qapp.processEvents()
    assert window.windowTitle() == monitoring
