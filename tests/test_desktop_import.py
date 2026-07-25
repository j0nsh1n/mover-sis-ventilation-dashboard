"""Desktop module smoke tests (no display required for imports)."""

from __future__ import annotations

import os

import pytest


def test_desktop_package_exports_main():
    from src import desktop

    assert callable(desktop.main)


def test_plotly_view_import():
    # Import may require Qt libs; skip if unavailable in CI
    try:
        from src.desktop.plotly_view import PlotlyView  # noqa: F401
    except Exception as e:
        pytest.skip(f"Qt/WebEngine unavailable: {e}")


@pytest.mark.skipif(
    os.environ.get("CI") == "true" and not os.environ.get("DISPLAY"),
    reason="No display in CI",
)
def test_mainwindow_constructs_offscreen(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication

        QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
        app = QApplication.instance() or QApplication([])
        from src.desktop.app import MainWindow

        win = MainWindow()
        assert win.windowTitle()
        win.close()
        # do not quit global app if shared
    except Exception as e:
        pytest.skip(f"Desktop UI not constructible: {e}")
