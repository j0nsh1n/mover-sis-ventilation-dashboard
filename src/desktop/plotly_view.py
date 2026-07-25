"""Embed Plotly figures in a Qt WebEngine view."""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QVBoxLayout, QWidget
import plotly.graph_objects as go
import plotly.io as pio


class PlotlyView(QWidget):
    """Widget that renders a Plotly figure as interactive HTML."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._view = QWebEngineView(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._view)
        self.clear()

    def clear(self, message: str = "No chart to display.") -> None:
        html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body {{
    margin: 0; font-family: system-ui, sans-serif;
    display: flex; align-items: center; justify-content: center;
    height: 100vh; color: #666; background: #fafafa;
  }}
</style></head>
<body><p>{message}</p></body></html>"""
        self._view.setHtml(html)

    def set_figure(self, fig: go.Figure | None) -> None:
        if fig is None:
            self.clear()
            return
        # Full HTML bundle with plotly.js included for offline use
        html = pio.to_html(
            fig,
            include_plotlyjs=True,
            full_html=True,
            config={
                "displayModeBar": True,
                "responsive": True,
                "displaylogo": False,
            },
        )
        self._view.setHtml(html, QUrl("about:blank"))
