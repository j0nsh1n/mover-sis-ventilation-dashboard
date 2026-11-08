"""Bundled fonts: Inter for the interface, Bitstream Charter for titles and statements.

The files ship inside the app so the look does not depend on what the machine has
installed. Licenses sit beside them in ``assets/fonts``.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication

FONT_DIR = Path(__file__).parent / "assets" / "fonts"

SANS_FAMILY = "Inter"
SERIF_FAMILY = "Bitstream Charter"
# Used only when the bundled serif could not be loaded.
SERIF_FALLBACK = "Noto Serif"
BODY_PIXEL_SIZE = 14

BUNDLED_FONT_FILES = (
    "Inter-Regular.ttf",
    "Inter-Italic.ttf",
    "Inter-SemiBold.ttf",
    "Inter-SemiBoldItalic.ttf",
    "c0648bt_.pfb",
    "c0649bt_.pfb",
    "c0632bt_.pfb",
    "c0633bt_.pfb",
)

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
    _loaded_families = frozenset(families)
    return _loaded_families


def serif_family() -> str:
    """Family name for titles and statements in later stages."""
    return SERIF_FAMILY if SERIF_FAMILY in load_bundled_fonts() else SERIF_FALLBACK


def body_font() -> QFont:
    """Inter at the design's body size, with tabular figures so numbers line up in columns."""
    font = QFont(SANS_FAMILY)
    font.setPixelSize(BODY_PIXEL_SIZE)
    font.setFeature(QFont.Tag("tnum"), 1)
    return font


def install_app_font(app: QGuiApplication) -> None:
    """Load the bundled fonts and make Inter the application's default font."""
    load_bundled_fonts()
    body = body_font()
    # A theme switch calls this again; re-setting an unchanged font would repolish every widget.
    if app.font() != body:
        app.setFont(body)
