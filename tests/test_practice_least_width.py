"""Medication and vitals and the WHO checklist inside the real window at its least width."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QWidget

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


def _settle(qapp) -> None:
    for _ in range(5):
        qapp.processEvents()


def test_the_new_practice_pages_do_not_raise_the_windows_least_width_above_the_handoff_pages(window, qapp):
    least = {}
    for name in ("handoff", "medications", "who"):
        window._go(name)
        _settle(qapp)
        window.resize(100, 700)
        _settle(qapp)
        least[name] = window.width()
    assert least["medications"] == least["who"] == least["handoff"] <= 400


@pytest.mark.parametrize("name", ["medications", "who"])
def test_each_page_fits_the_least_window_width_without_a_cut_off_edge(window, qapp, name):
    window._go(name)
    window.resize(100, 700)
    _settle(qapp)
    page = window.module_stack.currentWidget()
    assert page.width() <= 300, "this is the narrowest page the app can give it"
    if hasattr(page, "chart"):
        page.chart.redraw()
        _settle(qapp)
    assert page.page_scroll.horizontalScrollBar().maximum() == 0
    body = page.page_scroll.widget()
    assert body.width() <= page.page_scroll.viewport().width()
    for widget in body.findChildren(QWidget):
        if widget.isVisibleTo(body) and widget.width() > 0 and widget.parent() is not None:
            right = widget.mapTo(body, QPoint(widget.width(), 0)).x()
            assert right <= body.width() + 1, (name, widget.objectName() or type(widget).__name__, right, body.width())
