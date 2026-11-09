"""Flag marks (one ink, shape plus word) and the hatched missing-data cell."""

from __future__ import annotations

import os

import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import to_rgba  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication, QStyledItemDelegate, QTableWidget, QTableWidgetItem  # noqa: E402

from src.desktop.charts import flag_rules_figure, focused_signals_figure  # noqa: E402
from src.desktop.marks import (  # noqa: E402
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
    assert severity_label("critical") == "◇ critical"
    assert len(set(SEVERITY_GLYPHS.values())) == 3
    assert severity_glyph("unheard-of") == ""
    assert severity_label("unheard-of") == "unheard-of"


def _flags() -> pd.DataFrame:
    return pd.DataFrame(
        {"t_min": [1.0, 2.0, 3.0], "severity": ["info", "warn", "critical"], "rule_id": ["a", "b", "c"]}
    )


def test_the_flag_lane_draws_three_shapes_in_one_ink(qapp, applied_theme):
    ts = pd.DataFrame({"t_min": range(5), "PIP": [10, 11, 12, 13, 14], "ETCO2": [30] * 5, "HR": [70] * 5})
    lane = focused_signals_figure(ts, 2, flags=_flags()).axes[-1]
    marks = {c.get_label(): c for c in lane.collections}
    assert set(marks) == {"info", "warn", "critical"}
    # The lane's figure is always dark, so its ink is the dark theme's ink under either app theme.
    ink = to_rgba(tokens("dark")["ink"])
    for severity, collection in marks.items():
        assert tuple(collection.get_facecolor()[0]) == pytest.approx(ink), severity
    shapes = {len(c.get_paths()[0].vertices) for c in marks.values()}
    assert len(shapes) == 3, "circle, triangle and diamond must stay three different shapes"


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

