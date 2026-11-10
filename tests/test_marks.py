"""Flag marks (one ink, shape plus word) and the hatched missing-data cell."""

from __future__ import annotations

import os

import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import to_rgba  # noqa: E402
from PySide6.QtGui import QColor, QFont, QFontMetrics  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QHeaderView,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
)

from src.desktop.charts import flag_rules_figure, focused_signals_figure  # noqa: E402
from src.desktop.marks import (  # noqa: E402
    _CELL_PADDING,
    MISSING_ROLE,
    SEVERITY_GLYPHS,
    MissingCellDelegate,
    mark_missing,
    severity_glyph,
    severity_label,
)
from src.desktop.theme import tokens  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _close_figures():
    yield
    plt.close("all")


def test_severity_is_a_shape_and_the_rule_files_word():
    assert severity_label("info") == "○ info"
    assert severity_label("warn") == "△ warn"
    assert severity_label("critical") == "◆ critical"
    assert len(set(SEVERITY_GLYPHS.values())) == 3
    assert severity_glyph("unheard-of") == ""
    assert severity_label("unheard-of") == "unheard-of"


def _flags() -> pd.DataFrame:
    return pd.DataFrame(
        {"t_min": [1.0, 2.0, 3.0], "severity": ["info", "warn", "critical"], "rule_id": ["a", "b", "c"]}
    )


def test_the_flag_lane_follows_the_fill_rule_in_one_ink(qapp, applied_theme):
    ts = pd.DataFrame({"t_min": range(5), "PIP": [10, 11, 12, 13, 14], "ETCO2": [30] * 5, "HR": [70] * 5})
    lane = focused_signals_figure(ts, None, flags=_flags()).axes[-1]
    marks = {line.get_label(): line for line in lane.lines}
    assert set(marks) == {"info", "warn", "critical"}
    # info is a hollow circle (a circle filled with the ground), warn a half-filled triangle, critical a solid diamond
    assert [(marks[s].get_marker(), marks[s].get_fillstyle()) for s in ("info", "warn", "critical")] == [
        ("o", "full"), ("^", "left"), ("D", "full")]
    # The lane is drawn in the active theme, so its ink is that theme's ink.
    palette = tokens(applied_theme)
    ink, paper = to_rgba(palette["ink"]), to_rgba(palette["paper"])
    for severity, line in marks.items():
        assert to_rgba(line.get_markeredgecolor()) == pytest.approx(ink), severity
    # a hollow shape is filled with the ground so the band's hatch does not run through it
    assert to_rgba(marks["info"].get_markerfacecolor()) == pytest.approx(paper)
    for severity in ("warn", "critical"):
        assert to_rgba(marks[severity].get_markerfacecolor()) == pytest.approx(ink), severity
    # the half-filled triangle's other half stays open
    assert to_rgba(marks["warn"].get_markerfacecoloralt()) == pytest.approx(paper)


def test_the_fallback_chart_colors_come_from_the_light_tokens(qapp, monkeypatch):
    import src.desktop.theme as theme
    from src.desktop.charts import _style

    def broken():
        raise RuntimeError("no theme")

    monkeypatch.setattr(theme, "chart_style", broken)
    light = tokens("light")
    style = _style()
    assert style["figure_facecolor"] == light["paper"]
    assert style["text"] == light["ink"]
    assert style["muted"] == light["ink_3"]
    assert style["grid"] == light["grid"]
    assert style["spine"] == light["rule"]
    assert style["bar"] == light["ink_2"]
    assert {style["info"], style["warn"], style["critical"]} == {light["ink"]}


def test_stacked_flag_counts_use_the_ink_and_tell_severities_apart_by_fill(qapp, applied_theme):
    flags = pd.DataFrame({"rule_id": ["a", "a", "a"], "severity": ["info", "warn", "critical"]})
    ax = flag_rules_figure(flags).axes[0]
    ink = to_rgba(tokens(applied_theme)["ink"])
    bars = {container.get_label(): container.patches[0] for container in ax.containers}
    assert set(bars) == {"info", "warn", "critical"}
    for severity, bar in bars.items():
        assert tuple(bar.get_edgecolor()) == pytest.approx(ink), severity
    assert bars["info"].get_facecolor()[3] == 0
    assert tuple(bars["critical"].get_facecolor()) == pytest.approx(ink)
    assert bars["warn"].get_hatch()
    assert not bars["info"].get_hatch() and not bars["critical"].get_hatch()


def _paint(table: QTableWidget):
    table.resize(320, 120)
    table.show()
    QApplication.processEvents()
    return table.viewport().grab().toImage()


def _table(text: str, *, missing: bool) -> QTableWidget:
    table = QTableWidget(1, 1)
    table.setItemDelegate(MissingCellDelegate(table))
    item = QTableWidgetItem(text)
    mark_missing(item, missing)
    table.setItem(0, 0, item)
    table.horizontalHeader().setStretchLastSection(True)
    return table


def _painted(image, background: QColor) -> set[tuple[int, int]]:
    return {
        (x, y)
        for y in range(image.height())
        for x in range(image.width())
        if QColor(image.pixel(x, y)) != background
    }


def test_a_missing_cell_is_hatched_with_diagonal_lines_and_keeps_its_text(qapp, applied_theme):
    plain = _table("", missing=False)
    background = QColor(_paint(plain).pixel(150, 20))
    hatched_empty = _table("", missing=True)
    lines = _painted(_paint(hatched_empty), background)
    assert len(lines) > 200
    # a diagonal line has its neighbour one pixel to the right and one up
    diagonal = sum(1 for x, y in lines if (x + 1, y - 1) in lines)
    assert diagonal / len(lines) > 0.6
    with_text = _table("missing", missing=True)
    assert with_text.item(0, 0).text() == "missing"
    assert _paint(with_text) != _paint(hatched_empty), "the cell's text must still be drawn on the hatch"
    plain_text = _table("missing", missing=False)
    assert len(_painted(_paint(plain_text), background)) < len(lines)


@pytest.mark.parametrize("text", ["missing", "not recorded"])
def test_a_content_sized_column_has_room_for_the_whole_missing_word(qapp, applied_theme, word_is_whole, text):
    table = QTableWidget(2, 2)
    table.setItemDelegate(MissingCellDelegate(table))
    for row, (label, value) in enumerate([("Signal", text), ("Other", "12")]):
        table.setItem(row, 0, QTableWidgetItem(label))
        cell = QTableWidgetItem(value)
        mark_missing(cell, value == text)
        table.setItem(row, 1, cell)
    table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
    table.show()
    QApplication.processEvents()
    upright = QFontMetrics(table.font()).horizontalAdvance(text)
    assert table.columnWidth(1) > upright  # sized by the cell, not left at the default
    assert word_is_whole(table, 0, 1), text
    index = table.model().index(0, 1)
    option = QStyleOptionViewItem()
    option.widget = table
    table.itemDelegate().initStyleOption(option, index)
    italic = QFont(table.font())
    italic.setItalic(True)
    assert table.itemDelegate().sizeHint(option, index).width() >= QFontMetrics(italic).horizontalAdvance(text)
    # a cell that is not missing keeps the stock hint
    plain = table.model().index(1, 1)
    plain_option = QStyleOptionViewItem()
    plain_option.widget = table
    table.itemDelegate().initStyleOption(plain_option, plain)
    assert table.itemDelegate().sizeHint(plain_option, plain) == QStyledItemDelegate(table).sizeHint(plain_option, plain)


def test_the_hint_grows_by_what_the_italic_face_adds(qapp, word_is_whole):
    """Some faces set their italics wider than the upright letters; the cell must hold that extra width too."""
    from PySide6.QtGui import QFontDatabase

    def widening(family: str) -> int:
        upright = QFont(family)
        upright.setPixelSize(16)
        slanted = QFont(upright)
        slanted.setItalic(True)
        return QFontMetrics(slanted).horizontalAdvance("missing") - QFontMetrics(upright).horizontalAdvance("missing")

    family = next((name for name in QFontDatabase.families() if widening(name) >= 2), None)
    if family is None:
        pytest.skip("no installed font sets its italics wider than its upright letters")
    table = QTableWidget(1, 1)
    table.setItemDelegate(MissingCellDelegate(table))
    face = QFont(family)
    face.setPixelSize(16)
    cell = QTableWidgetItem("missing")
    cell.setFont(face)
    mark_missing(cell)
    table.setItem(0, 0, cell)
    table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
    table.show()
    QApplication.processEvents()
    index = table.model().index(0, 0)
    option = QStyleOptionViewItem()
    option.widget = table
    table.itemDelegate().initStyleOption(option, index)
    stock = QStyledItemDelegate(table).sizeHint(option, index).width()
    slanted = QFont(option.font)
    slanted.setItalic(True)
    extra = QFontMetrics(slanted).horizontalAdvance("missing") - QFontMetrics(option.font).horizontalAdvance("missing")
    assert extra > 0
    assert table.itemDelegate().sizeHint(option, index).width() == stock + extra + _CELL_PADDING
    assert word_is_whole(table, 0, 0)


def test_a_cell_that_is_not_missing_paints_as_before(qapp):
    marked_off = _table("12", missing=False)
    unmarked = _table("12", missing=False)
    unmarked.setItemDelegate(QStyledItemDelegate(unmarked))
    assert _paint(marked_off) == _paint(unmarked)


def test_the_medication_table_hatches_exactly_its_missing_values(qapp):
    from src.modules.medication_vitals.panel import MedicationVitalsPanel

    panel = MedicationVitalsPanel()
    table = panel.table
    assert isinstance(table.itemDelegate(), MissingCellDelegate)
    flagged = {
        (row, column): table.item(row, column).text()
        for row in range(table.rowCount())
        for column in range(table.columnCount())
        if table.item(row, column).data(MISSING_ROLE)
    }
    assert flagged, "the fictional record has a missing saturation and an unrecorded dose"
    assert set(flagged.values()) <= {"missing", "not recorded"}
    assert {"missing", "not recorded"} <= set(flagged.values())
    for row in range(table.rowCount()):
        value = table.item(row, 2).text()
        assert bool(table.item(row, 2).data(MISSING_ROLE)) == (value in {"missing", "not recorded"})



def test_the_text_of_a_missing_cell_has_a_clear_backing_so_the_hatch_never_crosses_the_letters(qapp, applied_theme):
    plain = _table("", missing=False)
    background = QColor(_paint(plain).pixel(150, 20))
    empty = _paint(_table("", missing=True))
    with_text = _paint(_table("missing", missing=True))
    # the hatch's own full-coverage color: the most common painted color of an empty hatched cell
    counts: dict[int, int] = {}
    for y in range(empty.height()):
        for x in range(empty.width()):
            if QColor(empty.pixel(x, y)) != background:
                counts[empty.pixel(x, y)] = counts.get(empty.pixel(x, y), 0) + 1
    hatch = max(counts, key=lambda color: counts[color])
    changed = [(x, y) for y in range(with_text.height()) for x in range(with_text.width())
               if with_text.pixel(x, y) != empty.pixel(x, y)]
    assert changed
    left, right = min(x for x, _ in changed), max(x for x, _ in changed)
    top, bottom = min(y for _, y in changed), max(y for _, y in changed)
    crossing = [(x, y) for y in range(top, bottom + 1) for x in range(left, right + 1) if with_text.pixel(x, y) == hatch]
    assert crossing == [], "hatch lines must not run through the text"


def test_the_hatch_keeps_a_clip_the_caller_already_set(qapp):
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtWidgets import QStyleOptionViewItem

    table = _table("", missing=True)
    table.resize(320, 120)
    table.show()
    QApplication.processEvents()
    index = table.model().index(0, 0)
    option = QStyleOptionViewItem()
    option.widget = table
    table.itemDelegate().initStyleOption(option, index)
    option.rect = QRect(0, 0, 200, 40)
    image = QImage(320, 80, QImage.Format.Format_ARGB32)
    sentinel = QColor(10, 200, 30).rgba()
    image.fill(sentinel)
    painter = QPainter(image)
    painter.setClipRect(QRect(0, 0, 100, 80))
    table.itemDelegate().paint(painter, option, index)
    painter.end()
    outside = [(x, y) for y in range(image.height()) for x in range(100, image.width()) if image.pixel(x, y) != sentinel]
    inside = [(x, y) for y in range(image.height()) for x in range(100) if image.pixel(x, y) != sentinel]
    assert inside, "the hatch must still be drawn inside the caller's clip"
    assert outside == [], "the hatch must not escape the clip the caller set"
