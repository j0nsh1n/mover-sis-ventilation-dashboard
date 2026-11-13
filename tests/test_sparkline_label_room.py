"""A trace keeps clear of its own label in the Cases preview, and the research page's sparkline is unchanged.

The reference painter below is a frozen copy of what ``Sparkline.paintEvent`` drew before the label room was
added (grid, trace, observed-minute mark; the name is left out so the picture depends only on geometry). The
default sparkline, the one the research page uses, must match it pixel for pixel.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtGui import QImage, QPainter, QPainterPath, QPen  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from src.desktop.cases_preview import CasePreview, PreviewCase  # noqa: E402
from src.desktop.evidence_trail import Sparkline  # noqa: E402
from src.desktop.theme import apply_theme, token_color  # noqa: E402

HEIGHT = 56
BAND = 16  # rows 0 to 15: the 14 px line the signal's name is written in, and a little air under it


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(params=["light", "dark"], autouse=True, scope="module")
def theme(qapp, request):
    # Once per file per theme. Every test in this file checks both themes; repeating the switch per test
    # restyles every widget still alive.
    apply_theme(qapp, request.param)
    yield request.param
    apply_theme(qapp, "light")


def _picture(spark: Sparkline, width: int | None = None, height: int | None = None) -> QImage:
    if width is not None and height is not None:
        spark.setFixedHeight(height)
        spark.resize(width, height)
    return spark.grab().toImage().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)


def _reference(values, marker, color, width: int, height: int) -> QImage:
    """The drawing before the change, on the background an empty sparkline gives."""
    image = _picture(Sparkline(""), width, height)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(QPen(token_color("grid"), 1))
    for step in range(9):
        x = round(step * (width - 1) / 8) + 0.5
        painter.drawLine(QPointF(x, 0), QPointF(x, height))
    known = [value for value in values if value is not None]
    low, high = (min(known), max(known)) if known else (0.0, 1.0)
    span = max(high - low, 1.0)
    top, bottom = 8.0, height - 8.0
    last = max(len(values) - 1, 1)

    def point(index: int, value: float) -> QPointF:
        return QPointF(2 + index * (width - 4) / last, bottom - (value - low) / span * (bottom - top))

    path = QPainterPath()
    drawing = False
    for index, value in enumerate(values):
        if value is None:
            drawing = False
            continue
        if drawing:
            path.lineTo(point(index, value))
        else:
            path.moveTo(point(index, value))
            drawing = True
    painter.setPen(QPen(token_color(color), 1.5))
    painter.drawPath(path)
    if marker is not None:
        x = round(2 + marker * (width - 4) / last) + 0.5
        painter.setPen(QPen(token_color("accent"), 1.5))
        painter.drawLine(QPointF(x, 0), QPointF(x, height))
        value = values[marker]
        if value is not None:
            painter.setBrush(token_color("paper"))
            painter.drawEllipse(point(marker, value), 4.0, 4.0)
    painter.end()
    return image


SERIES = {
    "rising": [20.0, 21.0, 23.0, 26.0, 30.0, 31.0],
    "gap": [30.0, None, 22.0, 24.0, None, None, 40.0],
    "flat": [70.0] * 12,
    "single": [5.0],
    "noisy": [20 + (i * 7) % 13 for i in range(90)],
}


@pytest.mark.parametrize("width", [160, 270, 523])
@pytest.mark.parametrize("name", SERIES)
@pytest.mark.parametrize("marker", [None, 3])
def test_the_default_sparkline_is_pixel_identical_to_the_drawing_before_the_change(qapp, name, width, marker):
    values = SERIES[name]
    marker = marker if marker is not None and marker < len(values) else None
    spark = Sparkline("waiting")
    spark.set_series(values, marker, "", label="", color="s_etco2")
    assert _picture(spark, width, 72) == _reference(values, marker, "s_etco2", width, 72)


def _trace_only(values, width: int, *, label_room: bool) -> QImage:
    spark = Sparkline("", label_room=label_room)
    spark.set_series(values, None, "", label="", color="s_pip")
    return _picture(spark, width, HEIGHT)


def _grid_only(width: int, *, label_room: bool) -> QImage:
    spark = Sparkline("", label_room=label_room)
    spark.set_series([None, None], None, "", label="", color="s_pip")
    return _picture(spark, width, HEIGHT)


@pytest.mark.parametrize("name", SERIES)
def test_with_label_room_no_trace_reaches_the_band_the_signals_name_is_written_in(qapp, name):
    width = 270
    drawn = _trace_only(SERIES[name], width, label_room=True)
    bare = _grid_only(width, label_room=True)
    for y in range(BAND):
        for x in range(width):
            assert drawn.pixel(x, y) == bare.pixel(x, y), (name, x, y)
    # The trace is there, below the band (one sample is a point with no line, so it draws nothing, as before).
    assert name == "single" or any(drawn.pixel(x, y) != bare.pixel(x, y) for y in range(BAND, HEIGHT) for x in range(width))


def test_without_label_room_the_trace_does_run_through_the_band_so_the_check_above_can_fail(qapp):
    width = 270
    drawn = _trace_only(SERIES["rising"], width, label_room=False)
    bare = _grid_only(width, label_room=False)
    assert any(drawn.pixel(x, y) != bare.pixel(x, y) for y in range(BAND) for x in range(width))


def test_the_cases_preview_keeps_every_trace_clear_of_its_label(qapp):
    preview = CasePreview()
    preview.resize(270, 700)
    values = (20.0, None, 22.0, 31.0, 40.0, 25.0)
    preview.show_case(PreviewCase("SYN1", "Craniotomy", "", {"PIP": values, "ETCO2": values, "HR": values}, (), ()))
    preview.show()
    qapp.processEvents()
    for column, line in preview.traces.items():
        assert line.has_series(), column
        label, color = line.signal()
        assert label
        line.set_series(values, None, "", label="", color=color)
        drawn = _picture(line)
        line.set_series([None, None], None, "", label="", color=color)
        bare = _picture(line)
        for y in range(BAND):
            for x in range(line.width()):
                assert drawn.pixel(x, y) == bare.pixel(x, y), (column, x, y)
        assert any(drawn.pixel(x, y) != bare.pixel(x, y) for y in range(BAND, line.height()) for x in range(line.width()))
    preview.close()


def test_the_research_pages_sparkline_does_not_ask_for_label_room(qapp):
    from src.desktop.research_workspace import ResearchWorkspace

    workspace = ResearchWorkspace()
    spark = workspace.sparkline
    spark.set_series([20.0, 25.0, 31.0], 1, "", label="PIP", color="s_pip")
    assert spark._top == 8.0
    workspace.close()
