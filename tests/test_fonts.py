"""Bundled fonts: the files ship with their licenses, load from the bundle, and set the app font."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtGui import QFontDatabase, QFontMetricsF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from src.desktop import fonts  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_every_bundled_file_ships_with_both_license_texts():
    for name in fonts.BUNDLED_FONT_FILES:
        assert (fonts.FONT_DIR / name).stat().st_size > 10_000, name
    charter = (fonts.FONT_DIR / "Charter-NOTICE.txt").read_text(encoding="utf-8")
    # Bitstream's terms: redistribute only with this notice intact and the trademark acknowledged.
    assert "Copyright 1989-1992, Bitstream Inc." in charter
    assert "BITSTREAM CHARTER is a registered trademark of Bitstream Inc." in charter
    inter = (fonts.FONT_DIR / "Inter-LICENSE.txt").read_text(encoding="utf-8")
    assert "SIL Open Font License, Version 1.1" in inter
    assert "The Inter Project Authors" in inter


def test_the_families_come_from_the_bundled_files(qapp):
    # applicationFontFamilies lists only fonts registered through addApplicationFont,
    # so a machine that has Inter installed cannot make this pass without the bundle loading.
    families = fonts.load_bundled_fonts()
    assert {fonts.SANS_FAMILY, fonts.SERIF_FAMILY} <= families
    for name in fonts.BUNDLED_FONT_FILES:
        font_id = QFontDatabase.addApplicationFont(str(fonts.FONT_DIR / name))
        assert font_id >= 0, name
        assert QFontDatabase.applicationFontFamilies(font_id), name
    assert fonts.serif_family() == "Charter"


def test_loading_happens_once(qapp, monkeypatch):
    fonts.load_bundled_fonts()
    calls = []
    monkeypatch.setattr(fonts.QFontDatabase, "addApplicationFont", lambda path: calls.append(path) or 0)
    fonts.load_bundled_fonts()
    assert calls == []


def test_the_application_default_font_is_inter_at_body_size(qapp):
    fonts.install_app_font(qapp)
    assert qapp.font().family() == "Inter"
    assert qapp.font().pixelSize() == fonts.BODY_PIXEL_SIZE == 14


def _widths(font) -> tuple[float, float]:
    metrics = QFontMetricsF(font)
    return metrics.horizontalAdvance("1111"), metrics.horizontalAdvance("8888")


def test_the_body_font_has_proportional_figures_and_normal_hyphens(qapp):
    body = fonts.body_font()
    assert body.featureTags() == []
    ones, eights = _widths(body)
    assert ones < eights
    # tabular figures widen the hyphen too; ordinary text and IDs ("end-tidal", "FAKE-ART-LOW") keep Inter's own
    assert QFontMetricsF(body).horizontalAdvance("-") < QFontMetricsF(fonts.tabular(body)).horizontalAdvance("-")


def test_tabular_figures_make_digits_equal_width_so_numbers_line_up(qapp):
    ones, eights = _widths(fonts.tabular(fonts.body_font()))
    assert ones == pytest.approx(eights)


def test_table_views_and_headers_use_tabular_figures_and_other_widgets_do_not(qapp, applied_theme):
    from PySide6.QtWidgets import QLabel, QTableWidget

    table = QTableWidget(2, 2)
    label = QLabel("12-34")
    for widget in (table, table.horizontalHeader(), table.viewport()):
        widget.show()
    label.show()
    qapp.processEvents()
    try:
        for widget in (table, table.horizontalHeader()):
            ones, eights = _widths(widget.font())
            assert ones == pytest.approx(eights), (applied_theme, type(widget).__name__)
        ones, eights = _widths(label.font())
        assert ones < eights, "plain text keeps proportional figures"
    finally:
        table.close()
        label.close()


def test_number_readouts_use_tabular_figures(qapp, applied_theme):
    from src.desktop.research_workspace import ResearchWorkspace
    from src.desktop.rules_drawer import RulesDrawer

    workspace = ResearchWorkspace()
    drawer = RulesDrawer()
    readouts = [workspace.minute_stamp, *workspace.metric_values.values(), drawer.count_label]
    for readout in readouts:
        ones, eights = _widths(readout.font())
        assert ones == pytest.approx(eights), readout.objectName() or readout.text()


def test_a_font_that_fails_to_load_is_logged(qapp, monkeypatch, caplog):
    monkeypatch.setattr(fonts, "_loaded_families", None)
    monkeypatch.setattr(fonts.QFontDatabase, "addApplicationFont", lambda path: -1)
    with caplog.at_level("WARNING", logger=fonts.__name__):
        fonts.load_bundled_fonts()
    assert [name for name in fonts.BUNDLED_FONT_FILES if any(name in r.getMessage() for r in caplog.records)] == list(
        fonts.BUNDLED_FONT_FILES)


def test_applying_a_theme_installs_the_app_font(qapp):
    from src.desktop.theme import apply_theme

    qapp.setFont(qapp.font().__class__("DejaVu Sans"))
    apply_theme(qapp, "light")
    assert qapp.font().family() == "Inter"
    assert qapp.font().pixelSize() == fonts.BODY_PIXEL_SIZE
