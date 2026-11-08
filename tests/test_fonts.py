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
    assert fonts.serif_family() == "Bitstream Charter"


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


def test_digits_have_equal_width_so_numbers_line_up(qapp):
    metrics = QFontMetricsF(fonts.body_font())
    assert metrics.horizontalAdvance("1111") == pytest.approx(metrics.horizontalAdvance("8888"))


def test_applying_a_theme_installs_the_app_font(qapp):
    from src.desktop.theme import apply_theme

    qapp.setFont(qapp.font().__class__("DejaVu Sans"))
    apply_theme(qapp, "light")
    assert qapp.font().family() == "Inter"
    assert qapp.font().pixelSize() == fonts.BODY_PIXEL_SIZE
