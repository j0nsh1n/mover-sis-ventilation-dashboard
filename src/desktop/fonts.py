"""Bundled fonts: Inter for the interface, Bitstream Charter for titles and statements.

The files ship inside the app so the look does not depend on what the machine has
installed. Licenses sit beside them in ``assets/fonts``. Charter ships as the TrueType
release of Bitstream's font, because Qt does not load Type 1 files on every platform.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication
from PySide6.QtWidgets import QHeaderView, QTableView, QWidget

FONT_DIR = Path(__file__).parent / "assets" / "fonts"

SANS_FAMILY = "Inter"
SERIF_FAMILY = "Charter"
# Used only when the bundled serif could not be loaded.
SERIF_FALLBACK = "Noto Serif"
BODY_PIXEL_SIZE = 14

BUNDLED_FONT_FILES = (
    "Inter-Regular.ttf",
    "Inter-Italic.ttf",
    "Inter-SemiBold.ttf",
    "Inter-SemiBoldItalic.ttf",
    "Charter-Regular.ttf",
    "Charter-Italic.ttf",
    "Charter-Bold.ttf",
    "Charter-BoldItalic.ttf",
)

_log = logging.getLogger(__name__)

_loaded_families: frozenset[str] | None = None


def load_bundled_fonts() -> frozenset[str]:
    """Register the bundled files with Qt once and return the families they provide.

    Qt needs a running application to register fonts, so a call made before one
    exists loads nothing and does not count as the one-time load.
    """
    global _loaded_families
    if _loaded_families is not None:
        return _loaded_families
    if QGuiApplication.instance() is None:
        return frozenset()
    families: set[str] = set()
    for name in BUNDLED_FONT_FILES:
        font_id = QFontDatabase.addApplicationFont(str(FONT_DIR / name))
        if font_id >= 0:
            families.update(QFontDatabase.applicationFontFamilies(font_id))
        else:
            _log.warning("Bundled font %s could not be loaded; Qt will use a substitute.", name)
    _loaded_families = frozenset(families)
    return _loaded_families


def serif_family() -> str:
    """Family name for titles and statements in later stages."""
    return SERIF_FAMILY if SERIF_FAMILY in load_bundled_fonts() else SERIF_FALLBACK


def body_font() -> QFont:
    """Inter at the design's body size, with its default proportional figures.

    Tabular figures change the width of the hyphen and other punctuation, which reads as
    uneven spacing in sentences and IDs, so only tables and number readouts take them.
    """
    font = QFont(SANS_FAMILY)
    font.setPixelSize(BODY_PIXEL_SIZE)
    return font


def tabular(font: QFont) -> QFont:
    """A copy of ``font`` with tabular figures, so digits have equal width and columns line up."""
    tabular_font = QFont(font)
    tabular_font.setFeature(QFont.Tag("tnum"), 1)
    return tabular_font


def make_tabular(widget: QWidget) -> None:
    """Give a number readout (a value label, a count) tabular figures."""
    widget.setFont(tabular(widget.font()))


class _TabularFigures(QObject):
    """Gives every table view and table header tabular figures when it is first polished.

    ``QApplication.setFont(font, className)`` does not carry the feature through the
    application stylesheet's font rules, and a font set on the widget itself does.
    """

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Polish and isinstance(watched, (QTableView, QHeaderView)):
            watched.setFont(tabular(watched.font()))
        return False


_tabular_filter: _TabularFigures | None = None


def install_app_font(app: QGuiApplication) -> None:
    """Load the bundled fonts, make Inter the default font, and give table text tabular figures."""
    global _tabular_filter
    load_bundled_fonts()
    body = body_font()
    # A theme switch calls this again; re-setting an unchanged font would repolish every widget.
    if app.font() != body:
        app.setFont(body)
    if _tabular_filter is None or _tabular_filter.parent() is not app:
        _tabular_filter = _TabularFigures(app)
        app.installEventFilter(_tabular_filter)
