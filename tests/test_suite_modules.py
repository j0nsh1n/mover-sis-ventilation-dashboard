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
