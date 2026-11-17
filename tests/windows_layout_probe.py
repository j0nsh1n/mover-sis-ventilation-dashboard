"""Temporary: print the measurements behind four Windows-only layout failures. Asserts nothing.

Run by the temporary ``windows-layout-probe`` CI job only (an explicit path; pytest does not collect this name
on its own). Remove it with that job once the four tests pass on Windows.
"""

from __future__ import annotations

import math

import pytest
from PySide6.QtGui import QColor, QFontInfo, QFontMetricsF
from PySide6.QtWidgets import QApplication

from src.desktop.theme import apply_theme


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    apply_theme(app, "light")
    return app


def _font(widget) -> str:
    font = widget.font()
    metrics = QFontMetricsF(font)
    return (f"font {font.family()!r} resolved {QFontInfo(font).family()!r} px {font.pixelSize()} pt {font.pointSizeF()}"
            f" ascent {metrics.ascent():.3f} descent {metrics.descent():.3f} leading {metrics.leading():.3f}"
            f" height {metrics.height():.3f} lineSpacing {metrics.lineSpacing():.3f}")


def test_probe_cases_table(qapp):
    from tests.test_cases_page import _sweep_widths, _wide_cases, _workspace

    view, overview = _workspace(1500, cases=_wide_cases())
    table, header = overview.table, overview.table.horizontalHeader()
    print("\nCASES", _font(table))
    for width in [*_sweep_widths(overview), overview.least_width() - 150]:
        view.resize(width, 800)
        QApplication.processEvents()
        sizes = [header.sectionSize(c) for c in range(header.count())]
        bar = table.horizontalScrollBar().maximum()
        if bar or width in (overview.least_width(), overview.least_width() - 150):
            print(f"CASES width {width} page {overview.width()} table {table.width()} viewport {table.viewport().width()}"
                  f" frame {table.frameWidth()} vbar {table.verticalScrollBar().isVisible()}"
                  f"/{table.verticalScrollBar().width()} header len {header.length()} sum {sum(sizes)}"
                  f" offset {header.offset()} hbar max {bar} sizes {sizes}"
                  f" tiers {[sum(t) for t in overview._tiers]} min {table.minimumWidth()}")
    view.close()


def test_probe_handoff_box_and_table(qapp, monkeypatch, tmp_path):
    from src.modules.handoff_trainer.panel import HandoffPanel

    monkeypatch.setenv("MOVER_CONFIG_DIR", str(tmp_path / "config"))
    panel = HandoffPanel()
    panel.resize(1300, 820)
    panel.show()
    QApplication.processEvents()
    box = panel.sections["action_list"]
    for pixels in (13, 14, 16, 18, 22):
        box.setStyleSheet(f"font-size: {pixels}px;")
        for count in (2, 3, 8):
            box.setPlainText("\n".join(f"line {n}" for n in range(count)))
            QApplication.processEvents()
            QApplication.processEvents()
            doc = box.document()
            block = doc.firstBlock()
            layout = block.layout()
            line = layout.lineAt(0) if layout is not None and layout.lineCount() else None
            rects = []
            b = block
            while b.isValid():
                rects.append(round(box.blockBoundingRect(b).height(), 3))
                b = b.next()
            print(f"BOX px {pixels} lines {count} height {box.height()} viewport {box.viewport().height()}"
                  f" frame {box.frameWidth()} margins {box.contentsMargins().top()}/{box.contentsMargins().bottom()}"
                  f" docMargin {doc.documentMargin()} docH {doc.size().height()}"
                  f" lineH {line.height() if line else None} leadingIncl {line.leadingIncluded() if line else None}"
                  f" blockH {rects} vbar {box.verticalScrollBar().maximum()}"
                  f" fm.lineSpacing {QFontMetricsF(doc.defaultFont()).lineSpacing():.3f}"
                  f" ceil {math.ceil(QFontMetricsF(doc.defaultFont()).lineSpacing())} {_font(box)}")
    for width in (700, 1300):
        panel.resize(width, 800)
        if not panel.timeline_table.isVisible():
            panel.table_toggle.click()
        QApplication.processEvents()
        QApplication.processEvents()
        table = panel.timeline_table
        header = table.horizontalHeader()
        sizes = [header.sectionSize(c) for c in range(header.count())]
        hints = [table.sizeHintForColumn(c) for c in range(header.count())]
        print(f"HTABLE width {width} panel {panel.width()} table {table.width()} viewport {table.viewport().width()}"
              f" frame {table.frameWidth()} vbar {table.verticalScrollBar().isVisible()}"
              f"/{table.verticalScrollBar().width()} header len {header.length()} sum {sum(sizes)}"
              f" hbar max {table.horizontalScrollBar().maximum()} sizes {sizes} hints {hints}"
              f" headerHints {[header.sectionSizeHint(c) for c in range(header.count())]} {_font(table)}")
    panel.close()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_probe_missing_cell_backing(qapp, theme):
    from tests.test_marks import _paint, _table

    apply_theme(qapp, theme)
    plain = _table("", missing=False)
    background = QColor(_paint(plain).pixel(150, 20))
    empty = _paint(_table("", missing=True))
    table = _table("missing", missing=True)
    with_text = _paint(table)
    backing_color = None
    changed = [(x, y) for y in range(with_text.height()) for x in range(with_text.width())
               if with_text.pixel(x, y) != empty.pixel(x, y)]
    counts: dict[int, int] = {}
    for x, y in changed:
        counts[with_text.pixel(x, y)] = counts.get(with_text.pixel(x, y), 0) + 1
    if counts:
        backing_color = max(counts, key=lambda c: counts[c])
    backing = [(x, y) for x, y in changed if with_text.pixel(x, y) == backing_color]
    ink = [(x, y) for x, y in changed if with_text.pixel(x, y) != backing_color]

    def box(points):
        if not points:
            return None
        return (min(x for x, _ in points), min(y for _, y in points), max(x for x, _ in points),
                max(y for _, y in points))

    print(f"\nHATCH {theme} background {background.name()} backing box {box(backing)} ink box {box(ink)}"
          f" changed box {box(changed)} rowHeight {table.rowHeight(0)} {_font(table)}")
    apply_theme(qapp, "light")


def test_probe_handoff_table_pictures(qapp, monkeypatch, tmp_path):
    """Pictures for the owner, who has no Windows machine: the table at 700 px with one-line headings (as before)
    and with two-line headings (now), and at full width. Saved to probe-shots/ for the CI artifact."""
    from pathlib import Path

    from src.modules.handoff_trainer.panel import HandoffPanel

    monkeypatch.setenv("MOVER_CONFIG_DIR", str(tmp_path / "config"))
    out = Path("probe-shots")
    out.mkdir(exist_ok=True)
    panel = HandoffPanel()
    panel.resize(1300, 820)
    panel.show()
    QApplication.processEvents()
    panel.table_toggle.click()
    table = panel.timeline_table
    for width, name in ((700, "now-700"), (1300, "now-1300")):
        panel.resize(width, 820)
        QApplication.processEvents()
        QApplication.processEvents()
        table.grab().save(str(out / f"handoff-table-{name}.png"))
        print(f"SHOT {name} two_lines {table._two_lines} hbar {table.horizontalScrollBar().maximum()}")
    panel.resize(700, 820)
    QApplication.processEvents()
    table._set_two_lines(False)  # the old look, for comparison
    QApplication.processEvents()
    table.grab().save(str(out / "handoff-table-before-700.png"))
    print(f"SHOT before-700 hbar {table.horizontalScrollBar().maximum()}")
    panel.close()
