"""The Cases page's three summary figures: what they draw, in which tokens, and that nothing is cropped."""

from __future__ import annotations

import os

import matplotlib.pyplot as plt
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from matplotlib.colors import to_hex  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from src.desktop.charts import (  # noqa: E402
    SUMMARY_CHART_HEIGHT,
    duration_ticks_figure,
    histogram_edges,
    rule_minutes_figure,
    score_histogram_figure,
    tick_values,
)
from src.desktop.theme import tokens  # noqa: E402

THEMES = ("light", "dark")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def close_figures():
    yield
    plt.close("all")


def _texts(fig) -> list[str]:
    return [t.get_text() for t in fig.axes[0].texts]


@pytest.mark.parametrize(("values", "edges"), [
    ([0, 1, 6, 7, 7, 12], [0, 2, 4, 6, 8, 10, 12, 14]),
    ([50, 30, 29, 24, 20, 16, 0], [0, 10, 20, 30, 40, 50, 60]),
    ([0, 0], [0, 1]),
    ([], [0, 1]),
    ([0.2, 0.9], [0, 0.2, 0.4, 0.6, 0.8, 1]),
])
def test_histogram_edges_are_round_numbers_from_zero_past_the_highest_value(values, edges):
    assert histogram_edges(values) == pytest.approx(edges)


def test_the_edges_never_exceed_eight_bins_and_hold_every_value():
    for top in (3, 9, 47, 130, 1234):
        edges = histogram_edges([0, top])
        assert 2 <= len(edges) <= 9 and edges[0] <= 0 and edges[-1] > top


@pytest.mark.parametrize(("low", "high", "ticks"), [(60, 300, [0, 100, 200, 300]), (61, 240, [50, 100, 150, 200, 250]),
                                                    (5, 5, [5, 6])])
def test_axis_ticks_cover_the_range_with_round_values(low, high, ticks):
    assert tick_values(low, high) == pytest.approx(ticks)


@pytest.mark.parametrize("theme", THEMES)
def test_the_histogram_draws_ink_bars_in_proportion_to_the_counts_on_the_papers_ground(qapp, theme):
    palette = tokens(theme)
    fig = score_histogram_figure([0, 1, 6, 7, 7, 12], width=250, theme=theme)
    assert to_hex(fig.get_facecolor()) == palette["paper"].lower()
    bars = fig.axes[0].patches
    # bins of two from 0: counts 2, 0, 0, 3, 0, 0, 1; empty bins draw nothing
    heights = [bar.get_height() for bar in bars]
    unit = heights[2] / 1
    assert heights == pytest.approx([2 * unit, 3 * unit, 1 * unit])
    assert {to_hex(bar.get_facecolor()) for bar in bars} == {palette["ink_2"].lower()}
    assert all(bar.get_edgecolor()[3] == 0 for bar in bars)
    # the tallest bar's count at the top left, and each range's lower edge under its bar
    texts = _texts(fig)
    assert texts[-1] == "3" and texts[:-1] == ["0", "2", "4", "6", "8", "10", "12", "14"]
    assert {to_hex(t.get_color()) for t in fig.axes[0].texts} == {palette["ink_3"].lower()}


@pytest.mark.parametrize("theme", THEMES)
def test_the_rule_chart_draws_ink_bars_with_the_number_at_each_bars_end(qapp, theme):
    palette = tokens(theme)
    rows = [("High end-tidal CO2", 74.0), ("Rising PIP", 49.0), ("Agent drift", 34.0)]
    fig = rule_minutes_figure(rows, width=320, theme=theme)
    ax = fig.axes[0]
    bars = ax.patches
    assert [text for text in _texts(fig) if text not in {name for name, _ in rows}] == ["74", "49", "34"]
    assert [name for name, _ in rows] == [t.get_text() for t in ax.texts[::2]]
    # one bar per rule, in the order given, as long as its number says
    assert [round(bar.get_width() / bars[-1].get_width() * 34) for bar in bars] == [74, 49, 34]
    assert {to_hex(bar.get_facecolor()) for bar in bars} == {palette["ink_2"].lower()}
    renderer = fig.canvas.get_renderer()
    for bar, number in zip(bars, ax.texts[1::2], strict=True):
        assert number.get_window_extent(renderer).x0 >= bar.get_window_extent(renderer).x1 - 0.5
    assert {to_hex(t.get_color()) for t in ax.texts[::2]} == {palette["ink_2"].lower()}
    assert {to_hex(t.get_color()) for t in ax.texts[1::2]} == {palette["ink_3"].lower()}


@pytest.mark.parametrize("theme", THEMES)
def test_the_duration_chart_has_one_ink_tick_per_case_and_a_median_line_named_median(qapp, theme):
    palette = tokens(theme)
    minutes = [60.0, 90.0, 96.0, 100.0, 300.0]
    fig = duration_ticks_figure(minutes, width=250, theme=theme)
    ax = fig.axes[0]
    ticks = [c for c in ax.collections if to_hex(c.get_edgecolor()[0]) == palette["ink_2"].lower()]
    median = [line for line in ax.lines if to_hex(line.get_color()) == palette["ink"].lower()]
    assert len(ticks) == 1 and len(median) == 1
    segments = ticks[0].get_segments()
    assert len(segments) == len(minutes)
    xs = [segment[0][0] for segment in segments]
    assert xs == sorted(xs)
    # the median line sits on the median case's tick
    assert median[0].get_xdata()[0] == pytest.approx(xs[2])
    assert median[0].get_linewidth() > ticks[0].get_linewidth()[0]
    assert "median" in _texts(fig) and "min" in _texts(fig)
    # ink only: the accent is for what can be acted on
    assert palette["accent"].lower() not in {to_hex(line.get_color()) for line in ax.lines}
    assert palette["accent"].lower() not in {to_hex(c.get_edgecolor()[0]) for c in ax.collections}


@pytest.mark.parametrize("width", [150, 180, 250, 400, 700])
def test_nothing_the_charts_write_is_cut_by_the_edge_of_the_picture(qapp, width):
    long_name = "Prolonged zero PEEP while the case is ventilated, a long rule name"
    figures = [
        score_histogram_figure([0, 3, 9, 12, 20, 33, 47, 49, 50], width=width),
        rule_minutes_figure([(long_name, 1234.0), ("Rising PIP", 49.0), ("A", 1.0)], width=width),
        duration_ticks_figure([61.0, 90.0, 240.0, 250.0], width=width),
    ]
    for fig in figures:
        fig.canvas.draw()
        renderer = fig.canvas.get_renderer()
        box = fig.bbox
        for text in fig.axes[0].texts:
            extent = text.get_window_extent(renderer)
            assert extent.x0 >= box.x0 - 0.5 and extent.x1 <= box.x1 + 0.5, (width, text.get_text())
            assert extent.y0 >= box.y0 - 0.5 and extent.y1 <= box.y1 + 0.5, (width, text.get_text())


def test_a_long_rule_name_ends_in_an_ellipsis_instead_of_running_under_its_bar(qapp):
    fig = rule_minutes_figure([("Prolonged zero PEEP while the case is ventilated", 10.0)], width=250)
    name = fig.axes[0].texts[0].get_text()
    assert name.endswith("…") and len(name) < 30
    short = rule_minutes_figure([("Rising PIP", 10.0)], width=250)
    assert short.axes[0].texts[0].get_text() == "Rising PIP"


def test_the_figures_are_the_height_the_page_gives_them(qapp):
    fig = score_histogram_figure([1, 2, 3], width=250)
    assert fig.get_size_inches()[1] * fig.dpi == pytest.approx(SUMMARY_CHART_HEIGHT)
    assert fig.get_size_inches()[0] * fig.dpi == pytest.approx(250)
