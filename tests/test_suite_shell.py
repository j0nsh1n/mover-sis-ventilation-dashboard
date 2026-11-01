"""Suite title and the single Monitoring label on the desktop shell."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDockWidget, QLabel, QToolBar

from src.__version__ import get_version
from src.desktop.app import MainWindow
from src.desktop.suite_chrome import install_suite_chrome


def _suite_title() -> str:
    return f"MOVER Suite — MOVER SIS Monitor v{get_version()} — Ventilation & Anesthesia"


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


def _monitoring_labels(win: MainWindow) -> list[QLabel]:
    return [
        child
        for child in win.menuBar().findChildren(QLabel)
        if child.text() == "Monitoring"
    ]


def test_main_window_shows_the_suite_title_and_one_monitoring_label(window):
    assert window.windowTitle() == _suite_title()
    assert "MOVER Suite" in window.windowTitle()
    assert "MOVER SIS" in window.windowTitle()
    assert window.centralWidget() is window.module_stack
    assert window.module_stack.currentWidget() is window.research

    labels = _monitoring_labels(window)
    assert len(labels) == 1
    label = labels[0]
    assert label is window.menuBar().cornerWidget(Qt.Corner.TopLeftCorner)
    assert label.accessibleName() == "Monitoring"
    assert label.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert label.textInteractionFlags() == Qt.TextInteractionFlag.NoTextInteraction

    gear = window.menuBar().cornerWidget(Qt.Corner.TopRightCorner)
    assert gear is not None
    assert gear.defaultAction().text() == "Settings…"
    titles = [
        action.text().replace("&", "")
        for action in window.menuBar().actions()
        if action.menu() is not None
    ]
    assert titles == ["File", "View", "Help"]
    assert window.findChildren(QToolBar) == []
    assert window.findChildren(QDockWidget) == []
    status_labels = window.statusBar().findChildren(QLabel)
    assert not any(child.text() == "Monitoring" for child in status_labels)


def test_monitoring_label_is_visible_in_a_900_pixel_window(window, qapp):
    window.resize(900, 640)
    window.show()
    qapp.processEvents()

    label = window.menuBar().cornerWidget(Qt.Corner.TopLeftCorner)
    assert isinstance(label, QLabel)
    assert label.text() == "Monitoring"
    assert label.isVisible()
    assert label.isVisibleTo(window)
    assert label.width() > 0
    assert label.height() > 0
    assert window.width() <= 900

    bar = window.menuBar()
    in_window = label.mapTo(window, label.rect().topLeft())
    in_bar = label.mapTo(bar, label.rect().topLeft())
    assert 0 <= in_window.x() < 900
    assert in_window.x() + label.width() <= 900
    assert 0 <= in_bar.y()
    assert in_bar.y() + label.height() <= bar.height()
    gear = bar.cornerWidget(Qt.Corner.TopRightCorner)
    assert label.geometry().right() < gear.geometry().left()


def test_repeated_install_leaves_one_label_and_the_same_title(window, qapp):
    title = window.windowTitle()
    menus = [action.text() for action in window.menuBar().actions()]
    gear = window.menuBar().cornerWidget(Qt.Corner.TopRightCorner)
    research = window.research

    install_suite_chrome(window, get_version())
    install_suite_chrome(window, get_version())
    qapp.processEvents()

    assert window.windowTitle() == title == _suite_title()
    assert [action.text() for action in window.menuBar().actions()] == menus
    assert window.menuBar().cornerWidget(Qt.Corner.TopRightCorner) is gear
    assert window.centralWidget() is window.module_stack
    assert window.module_stack.currentWidget() is research
    labels = _monitoring_labels(window)
    assert labels == [window.menuBar().cornerWidget(Qt.Corner.TopLeftCorner)]
    assert len(labels) == 1
