"""Backward-compatible re-export — charts moved to ``src.desktop.charts``. """

from src.desktop.charts import (  # noqa: F401
    ChartView,
    LegacyChartView,
    case_timeline_figure,
    flag_rules_figure,
    top_cases_figure,
)

# Historical names
PlotlyView = ChartView
figure_from_timeseries = case_timeline_figure
