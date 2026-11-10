"""Application themes: light ("Paper"), dark ("Night ledger"), and follow-system."""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QDir, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

from src.desktop.fonts import install_app_font, serif_family
from src.user_settings import THEME_DARK, THEME_LIGHT, THEME_SYSTEM, get_theme

# Concrete theme last applied ("light" | "dark") — used by matplotlib charts.
_active_concrete: str = THEME_LIGHT

# ---------------------------------------------------------------------------
# Shared stylesheet (structure only; colors injected per theme)
#
# Rules of the design: one canvas, hairlines instead of boxes, radius 3 px, two
# font weights (400 and 600), one accent. Hover and press change color only.
# Every focusable control shows a 2 px accent ring; padding shrinks by 1 px when
# it appears so nothing moves.
# ---------------------------------------------------------------------------

_STRUCTURE = """
/* —— shell —— */
QMainWindow {{
    background: {paper};
    color: {ink};
}}
QWidget {{
    color: {ink};
    font-size: 14px;
    font-family: "Inter", "Noto Sans", "Segoe UI", "Ubuntu", sans-serif;
}}
QToolTip {{
    background: {sheet};
    color: {ink};
    border: 1px solid {rule};
    padding: 6px 8px;
    border-radius: 3px;
    font-size: 12.5px;
}}

/* —— menu (context menus only; the app has no menu bar) —— */
QMenu {{
    background: {sheet};
    color: {ink};
    border: 1px solid {rule};
    border-radius: 3px;
    padding: 4px;
}}
QMenu::item {{
    padding: 7px 28px 7px 12px;
    border-radius: 3px;
}}
QMenu::item:selected {{
    background: {sel};
    color: {ink};
}}
QMenu::item:disabled {{
    color: {rule_strong};
}}
QMenu::separator {{
    height: 1px;
    background: {rule};
    margin: 6px 8px;
}}

/* —— group boxes: a hairline, no card —— */
QGroupBox {{
    background: transparent;
    border: 1px solid {rule};
    border-radius: 3px;
    margin-top: 1.15em;
    padding-top: 14px;
    padding-left: 12px;
    padding-right: 12px;
    padding-bottom: 10px;
    font-weight: 600;
    color: {ink};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    top: 0px;
    padding: 0 6px;
    color: {ink_3};
    background-color: {paper};
    font-size: 12.5px;
    font-weight: 600;
}}

/* —— inputs —— */
QLineEdit, QSpinBox, QComboBox, QTextEdit, QPlainTextEdit {{
    background: {sheet};
    border: 1px solid {rule_strong};
    border-radius: 3px;
    padding: 7px 10px;
    min-height: 20px;
    color: {ink};
    font-size: 13px;
    selection-background-color: {accent};
    selection-color: {on_accent};
}}
QLineEdit:hover, QSpinBox:hover, QComboBox:hover, QTextEdit:hover, QPlainTextEdit:hover {{
    border-color: {ink_3};
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border: 2px solid {accent};
    padding: 6px 9px;
}}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled, QTextEdit:disabled, QPlainTextEdit:disabled {{
    background: {paper};
    color: {rule_strong};
    border-color: {rule};
}}
QComboBox {{
    padding-right: 28px;
}}
QComboBox:focus {{
    padding-right: 27px;
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 28px;
    border: none;
    background: transparent;
}}
QComboBox::drop-down:hover {{
    background: {rule_faint};
}}
QComboBox::down-arrow {{
    image: url(movericons:{combo_arrow});
    width: 12px;
    height: 12px;
}}
QComboBox QAbstractItemView {{
    background: {sheet};
    color: {ink};
    border: 1px solid {rule};
    selection-background-color: {sel};
    selection-color: {ink};
    outline: none;
    padding: 4px;
}}
QSpinBox::up-button, QSpinBox::down-button {{
    background: transparent;
    border: none;
    width: 16px;
}}

/* —— buttons: outlined by default, one filled primary, text buttons for the rest —— */
QPushButton {{
    background: transparent;
    color: {ink};
    border: 1px solid {rule_strong};
    border-radius: 3px;
    padding: 8px 14px;
    font-size: 13px;
    font-weight: 600;
    min-height: 18px;
}}
QPushButton:hover {{
    background: {rule_faint};
}}
QPushButton:pressed {{
    background: {rule};
}}
QPushButton:disabled {{
    background: transparent;
    color: {rule_strong};
    border-color: {rule};
}}
QPushButton:focus {{
    border: 2px solid {accent};
    padding: 7px 13px;
}}
QPushButton#primaryBtn {{
    background: {accent};
    color: {on_accent};
    border: 1px solid {accent};
}}
QPushButton#primaryBtn:hover, QPushButton#primaryBtn:pressed {{
    background: {accent_hover};
    border-color: {accent_hover};
}}
QPushButton#primaryBtn:disabled {{
    background: {rule_faint};
    color: {rule_strong};
    border-color: {rule};
}}
/* an accent ring on an accent fill cannot be seen, so the filled button rings in ink */
QPushButton#primaryBtn:focus {{
    border: 2px solid {ink};
    padding: 7px 13px;
}}
QPushButton#textBtn {{
    background: transparent;
    color: {accent};
    border: 1px solid transparent;
}}
QPushButton#textBtn:hover, QPushButton#textBtn:pressed {{
    background: transparent;
    color: {accent_hover};
}}
QPushButton#textBtn:disabled {{
    background: transparent;
    color: {rule_strong};
    border-color: transparent;
}}
QPushButton#textBtn:focus {{
    border: 2px solid {accent};
    padding: 7px 13px;
}}

/* —— tabs: text, with a line under the current one —— */
QTabWidget::pane {{
    border: none;
    border-top: 1px solid {rule};
    background: transparent;
    top: -1px;
    padding: 4px;
}}
QTabBar::tab {{
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    padding: 8px 10px;
    margin-right: 2px;
    color: {ink_3};
    font-size: 13px;
    font-weight: 600;
}}
QTabBar::tab:hover {{
    color: {ink};
}}
QTabBar::tab:selected {{
    color: {ink};
    border-bottom: 2px solid {ink};
}}
/* the ring replaces the 2 px underline, so padding drops by 2 px at the sides and 1 px above and below
   and the tab keeps its size; the side padding is what leaves clear space between ring and letters */
QTabBar::tab:focus {{
    border: 2px solid {accent};
    padding: 7px 8px;
}}

/* —— lists and tables: one header line, faint row lines —— */
QAbstractItemView {{
    selection-background-color: {sel};
    selection-color: {ink};
    outline: none;
}}
QTableView, QListView, QTreeView {{
    background: {paper};
    gridline-color: transparent;
    border: 2px solid transparent;
    color: {ink};
    font-size: 13px;
}}
QTableView:focus, QListView:focus, QTreeView:focus {{
    border: 2px solid {accent};
}}
QTableView::item {{
    padding: 4px 8px;
    border-bottom: 1px solid {rule_faint};
}}
QTableView::item:selected, QTableView::item:selected:!active {{
    background: {sel};
    color: {ink};
}}
QListView::item:selected, QListView::item:selected:!active {{
    background: {sel};
    color: {ink};
}}
QHeaderView {{
    background: {paper};
}}
QHeaderView::section {{
    background: {paper};
    padding: 6px 10px;
    border: none;
    border-bottom: 1px solid {rule};
    font-weight: 600;
    font-size: 12.5px;
    color: {ink_3};
}}
QHeaderView::section:vertical {{
    border-bottom: 1px solid {rule_faint};
}}
QTableCornerButton::section {{
    background: {paper};
    border: none;
    border-bottom: 1px solid {rule};
}}

/* —— app frame: rail, running head, practice tabs, footer —— */
QFrame#navRail {{
    background: {paper};
    border: none;
    border-right: 1px solid {rule};
}}
QToolButton#railButton {{
    background: transparent;
    color: {ink_3};
    border: none;
    border-left: 3px solid transparent;
    border-right: 3px solid transparent;
    border-radius: 0;
    padding: 8px 0 6px 0;
    font-size: 11px;
    font-weight: 600;
}}
QToolButton#railButton:hover {{
    color: {ink};
}}
QToolButton#railButton:checked {{
    color: {ink};
    border-left: 3px solid {accent};
}}
QToolButton#railButton:disabled {{
    color: {rule_strong};
}}
/* the ring replaces the 3 px marks, so padding changes to keep icon and word where they were */
QToolButton#railButton:focus {{
    border: 2px solid {accent};
    padding: 6px 1px 4px 1px;
}}
/* the current place keeps its mark under the ring: the left side stays the thicker one */
QToolButton#railButton:focus:checked {{
    border: 2px solid {accent};
    border-left: 5px solid {accent};
    padding: 6px 3px 4px 0;
}}
QFrame#runningHead {{
    background: {paper};
    border: none;
}}
QFrame#runningHead[practice="true"] {{
    border-top: 3px solid {practice_bar};
}}
QLabel#stamp {{
    color: {ink_3};
    font-size: 12.5px;
}}
QLabel#fictionLabel {{
    color: {practice};
    font-size: 12.5px;
    font-weight: 600;
}}
QPushButton#findButton {{
    background: transparent;
    color: {ink_3};
    border: 1px solid transparent;
    padding: 4px 8px;
    min-height: 0;
    font-size: 12.5px;
    font-weight: 400;
}}
QPushButton#findButton:hover, QPushButton#findButton:pressed {{
    background: transparent;
    color: {ink};
}}
QPushButton#findButton:focus {{
    border: 2px solid {accent};
    padding: 3px 7px;
}}
QFrame#practiceTabs {{
    background: {paper};
    border: none;
}}
QDialog#commandPalette {{
    background: {sheet};
    border: 1px solid {rule_strong};
}}
QListWidget#paletteList {{
    background: {sheet};
    border: 2px solid transparent;
}}
QListWidget#paletteList:focus {{
    border: 2px solid {accent};
}}
QLabel#paletteHint {{
    color: {ink_3};
    font-size: 12px;
}}

/* —— status: the quiet footer —— */
QStatusBar {{
    background: {paper};
    color: {ink_3};
    border-top: 1px solid {rule};
    padding: 2px 16px;
    font-size: 12px;
}}
QStatusBar::item {{
    border: none;
}}
QLabel#footerField {{
    color: {ink_3};
    font-size: 12px;
    padding-left: 18px;
}}

/* —— research page: question, controls, evidence trail, conversation —— */
QLineEdit#questionField {{
    background: transparent;
    border: none;
    border-bottom: 1px solid {rule_strong};
    border-radius: 0;
    padding: 2px 0 8px 0;
    min-height: 34px;
    font-family: {serif};
    font-size: 28px;
    color: {ink};
}}
QLineEdit#questionField:hover {{
    border-color: {ink_3};
}}
/* the ring is the underline, thickened; padding drops by 1 px so the field keeps its height */
QLineEdit#questionField:focus {{
    border: none;
    border-bottom: 2px solid {accent};
    padding: 2px 0 7px 0;
}}
QLineEdit#questionField:disabled {{
    background: transparent;
    color: {ink_3};
    border-bottom: 1px solid {rule};
}}
QGroupBox#scopeSwitch {{
    border: none;
    margin: 0;
    padding: 0;
    background: transparent;
}}
QRadioButton#scopeOption {{
    background: transparent;
    color: {ink_3};
    border: none;
    border-bottom: 2px solid transparent;
    padding: 8px 10px;
    margin-right: 2px;
    spacing: 0;
    font-size: 13px;
    font-weight: 600;
}}
QRadioButton#scopeOption::indicator {{
    width: 0;
    height: 0;
}}
QRadioButton#scopeOption:hover {{
    color: {ink};
}}
QRadioButton#scopeOption:checked {{
    color: {ink};
    border-bottom: 2px solid {ink};
}}
QRadioButton#scopeOption:focus {{
    border: 2px solid {accent};
    padding: 7px 8px;
}}
QPushButton#modelMenuBtn {{
    background: transparent;
    border: 1px solid transparent;
    padding: 6px 28px 6px 10px;
    font-weight: 400;
}}
QPushButton#modelMenuBtn:hover, QPushButton#modelMenuBtn:pressed {{
    background: {rule_faint};
}}
QPushButton#modelMenuBtn::menu-indicator {{
    image: none;
    width: 0;
}}
QPushButton#modelMenuBtn:focus {{
    border: 2px solid {accent};
    padding: 5px 27px 5px 9px;
}}
QPushButton#linkBtn {{
    background: transparent;
    color: {accent};
    border: 1px solid transparent;
    padding: 2px 4px;
    min-height: 0;
    font-size: 12.5px;
}}
QPushButton#linkBtn:hover, QPushButton#linkBtn:pressed {{
    background: transparent;
    color: {accent_hover};
}}
QPushButton#linkBtn:disabled {{
    background: transparent;
    color: {rule_strong};
    border-color: transparent;
}}
QPushButton#linkBtn:focus {{
    border: 2px solid {accent};
    padding: 1px 3px;
}}
QLabel#boardHeading {{
    font-family: {serif};
    font-size: 20px;
    color: {ink};
}}
QLabel#statement {{
    font-family: {serif};
    font-size: 20px;
    color: {ink};
}}
QLabel#stepHeading {{
    font-size: 14px;
    font-weight: 600;
    color: {ink};
}}
QLabel#stepNumber {{
    font-size: 14px;
    font-weight: 600;
    color: {ink_3};
}}
QLabel#quietText {{
    color: {ink_3};
    font-size: 12.5px;
}}
QLabel#bodyText {{
    color: {ink};
    font-size: 14px;
}}
QSplitter#boardSplitter::handle {{
    background: transparent;
    width: 1px;
    height: 1px;
    margin: 0;
}}
QSplitter#boardSplitter::handle:hover {{
    background: {accent};
}}
QScrollArea#conversationPanel {{
    background: {sheet};
    border: none;
    border-left: 1px solid {rule};
}}
QWidget#conversationPage {{
    background: {sheet};
}}
QLabel#conversationTitle {{
    font-family: {serif};
    font-size: 20px;
    color: {ink};
}}
QLabel#turnQuestion {{
    font-family: {serif};
    font-style: italic;
    font-size: 20px;
    color: {ink};
}}
QFrame#turnRule {{
    background: {rule};
    border: none;
    min-height: 1px;
    max-height: 1px;
}}
QTextBrowser#turnAnswer {{
    background: transparent;
    border: 2px solid transparent;
    padding: 0;
    font-size: 14px;
    selection-background-color: {accent};
    selection-color: {on_accent};
}}
QTextBrowser#turnAnswer:focus {{
    border: 2px solid {accent};
    padding: 0;
}}
QPushButton#citationLink {{
    background: transparent;
    color: {accent};
    border: 1px solid transparent;
    padding: 2px 4px;
    min-height: 0;
    font-size: 13px;
    font-weight: 400;
    text-align: left;
}}
QPushButton#citationLink:hover, QPushButton#citationLink:pressed {{
    background: transparent;
    color: {accent_hover};
}}
QPushButton#citationLink:focus {{
    border: 2px solid {accent};
    padding: 1px 3px;
}}
QProgressBar#turnProgress {{
    background: {rule};
    border: none;
    min-height: 3px;
    max-height: 3px;
}}
QProgressBar#turnProgress::chunk {{
    background: {accent};
}}

/* —— case page: header, chart readouts, minute slider, inspector —— */
QLabel#caseTitle {{
    font-family: {serif};
    font-size: 28px;
    color: {ink};
}}
QLabel#caseFacts {{
    color: {ink_2};
    font-size: 13px;
}}
QLabel#minuteStamp {{
    font-size: 28px;
    font-weight: 600;
    color: {ink};
}}
QLabel#minuteCaption {{
    color: {ink_2};
    font-size: 13px;
}}
QLabel#trackValue {{
    font-size: 20px;
    font-weight: 600;
    color: {ink};
}}
QLabel#trackValue[missing="true"] {{
    font-size: 14px;
    font-weight: 400;
    font-style: italic;
    color: {ink_3};
    padding-top: 4px;
}}
QSlider#minuteSlider::groove:horizontal {{
    height: 2px;
    background: {rule_strong};
}}
QSlider#minuteSlider::handle:horizontal {{
    background: {accent};
    border: 2px solid {paper};
    width: 10px;
    height: 10px;
    margin: -6px 0;
    border-radius: 7px;
}}
QSlider#minuteSlider::handle:horizontal:hover {{
    background: {accent_hover};
}}
/* the ring: the handle turns hollow with a thick accent edge, which reads on the paper without color alone */
QSlider#minuteSlider::handle:horizontal:focus {{
    background: {paper};
    border: 3px solid {accent};
    width: 8px;
    height: 8px;
    margin: -6px 0;
    border-radius: 7px;
}}
QFrame#caseInspector {{
    background: {sheet};
    border: none;
    border-left: 1px solid {rule};
}}
QFrame#caseInspector[stacked="true"] {{
    border-left: none;
    border-top: 1px solid {rule};
}}
QFrame#caseInspector QTableView, QFrame#caseInspector QListView {{
    background: transparent;
}}
QFrame#caseInspector QHeaderView, QFrame#caseInspector QHeaderView::section {{
    background: transparent;
}}

/* —— labels —— */
QLabel#heroTitle {{
    font-size: 20px;
    font-weight: 600;
    color: {ink};
}}
QLabel#heroSub {{
    color: {ink_3};
    font-size: 12.5px;
    padding-top: 2px;
}}
QLabel#pathHint {{
    color: {ink_3};
    font-size: 12.5px;
}}
QLabel#sectionLabel {{
    font-weight: 600;
    font-size: 12.5px;
    color: {ink_3};
    margin-top: 4px;
}}
QLabel#metricTitle {{
    color: {ink_3};
    font-size: 12.5px;
    font-weight: 600;
}}
QLabel#metricValue {{
    font-size: 18px;
    font-weight: 600;
    color: {ink};
    padding-top: 2px;
}}

/* —— metric cards —— */
QFrame#metricCard {{
    background: {sheet};
    border: 1px solid {rule};
    border-radius: 3px;
    min-width: 108px;
}}

/* —— chart host —— */
QWidget#chartView {{
    background: {paper};
    border: 1px solid {rule};
    border-radius: 3px;
}}
QScrollArea {{
    background: transparent;
    border: none;
}}
QScrollArea > QWidget > QWidget {{
    background: transparent;
}}

/* —— sidebar —— */
QWidget#sidePanel {{
    background: transparent;
}}

/* —— checkboxes —— */
QCheckBox {{
    color: {ink};
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 3px;
    border: 1px solid {rule_strong};
    background: {sheet};
}}
QCheckBox::indicator:hover {{
    border-color: {ink_3};
}}
QCheckBox::indicator:checked {{
    background: {accent};
    border-color: {accent};
}}
QCheckBox::indicator:focus {{
    border: 2px solid {accent};
}}
QCheckBox:disabled {{
    color: {rule_strong};
}}

/* —— dialogs / wizard —— */
QDialog, QWizard {{
    background: {paper};
    color: {ink};
}}
QWizard QWidget {{
    color: {ink};
}}
QDialogButtonBox QPushButton {{
    min-width: 88px;
}}

/* —— splitters —— */
QSplitter::handle {{
    background: {rule};
    width: 2px;
    margin: 4px 2px;
    border-radius: 0px;
}}
QSplitter::handle:hover {{
    background: {accent};
}}

/* —— scrollbars —— */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 4px 2px;
}}
QScrollBar::handle:vertical {{
    background: {rule_strong};
    border-radius: 3px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{
    background: {ink_3};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px 4px;
}}
QScrollBar::handle:horizontal {{
    background: {rule_strong};
    border-radius: 3px;
    min-width: 28px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {ink_3};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}
"""

# Design tokens, with the design's hyphenated names written with underscores
# (sheet-2 is sheet_2). Light is "Paper", dark is "Night ledger".
_LIGHT = {
    "combo_arrow": "chevron-dark.svg",
    "paper": "#F3F0E8",
    "sheet": "#FBFAF6",
    "sheet_2": "#ECE8DE",
    "ink": "#1C1B19",
    "ink_2": "#47443D",
    "ink_3": "#66625A",
    "rule": "#DAD4C7",
    "rule_faint": "#E6E1D6",
    "rule_strong": "#8E877A",
    "grid": "#E2DCCF",
    "accent": "#2F43A3",
    "accent_hover": "#24348A",
    "on_accent": "#FFFFFF",
    "sel": "#E2E5F4",
    "practice": "#6E4D00",
    "practice_bar": "#A27C12",
    "hatch": "rgba(71, 68, 61, 0.30)",
    "s_pip": "#B0502A",
    "s_etco2": "#1B776B",
    "s_hr": "#6A4C9C",
    "s_spo2": "#2D69B0",
    "s_map": "#7F6200",
}

_DARK = {
    "combo_arrow": "chevron-light.svg",
    "paper": "#131518",
    "sheet": "#1A1D21",
    "sheet_2": "#0F1113",
    "ink": "#ECE8DF",
    "ink_2": "#C3BDB0",
    "ink_3": "#9C978B",
    "rule": "#2B2F35",
    "rule_faint": "#202328",
    "rule_strong": "#686D75",
    "grid": "#24282E",
    "accent": "#A4B1F5",
    "accent_hover": "#C0C9F8",
    "on_accent": "#10142B",
    "sel": "#252B48",
    "practice": "#F0D27A",
    "practice_bar": "#8A7429",
    "hatch": "rgba(195, 189, 176, 0.24)",
    "s_pip": "#EE8A5E",
    "s_etco2": "#5AC6B6",
    "s_hr": "#B9A1EC",
    "s_spo2": "#80AFEE",
    "s_map": "#D8BC55",
}

_RGBA = re.compile(r"rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+)\s*\)")


def _serif_css() -> str:
    """The title font as a stylesheet family list; asking for it registers the bundled files first."""
    return f'"{serif_family()}", "Noto Serif", Georgia, serif'


def _build_stylesheet(tokens: dict[str, str]) -> str:
    return _STRUCTURE.format(serif=_serif_css(), **tokens)


QDir.addSearchPath("movericons", str(Path(__file__).parent / "assets"))
LIGHT_STYLESHEET = _build_stylesheet(_LIGHT)
DARK_STYLESHEET = _build_stylesheet(_DARK)


def active_theme() -> str:
    """Return concrete theme last applied (``light`` or ``dark``)."""
    return _active_concrete


def tokens(theme: str | None = None) -> dict[str, str]:
    """Design tokens of ``theme`` (``light`` or ``dark``), or of the active theme when omitted."""
    chosen = theme or _active_concrete
    return dict(_DARK if chosen == THEME_DARK else _LIGHT)


def token_color(name: str, theme: str | None = None) -> QColor:
    """A token as a ``QColor``; accepts the plain hex colors and the ``rgba()`` hatch."""
    value = tokens(theme)[name]
    match = _RGBA.fullmatch(value)
    if match:
        red, green, blue, alpha = match.groups()
        return QColor(int(red), int(green), int(blue), round(float(alpha) * 255))
    return QColor(value)


def chart_style_from(t: dict[str, str]) -> dict[str, str]:
    """Colors for matplotlib figures drawn from the token set ``t``.

    ``info``, ``warn`` and ``critical`` are the same ink: a rule flag is told apart by its
    shape and its word, never by a color of its own.
    """
    return {
        "figure_facecolor": t["paper"],
        "axes_facecolor": t["paper"],
        "text": t["ink"],
        "muted": t["ink_3"],
        "grid": t["grid"],
        "spine": t["rule"],
        "bar": t["ink_2"],
        "info": t["ink"],
        "warn": t["ink"],
        "critical": t["ink"],
    }


def chart_style() -> dict[str, str]:
    """Colors for matplotlib figures matching the active UI theme."""
    return chart_style_from(tokens())


def chat_role_color(role: str) -> str:
    """HTML color for chat role labels under the active theme."""
    t = tokens()
    return t["accent"] if role == "user" else t["ink_3"]


def system_prefers_dark() -> bool:
    """Detect OS dark-mode preference (Qt 6 styleHints when available)."""
    try:
        hints = QGuiApplication.styleHints()
        scheme = hints.colorScheme()
        if scheme == Qt.ColorScheme.Dark:
            return True
        if scheme == Qt.ColorScheme.Light:
            return False
    except Exception:
        pass
    try:
        app = QApplication.instance()
        if isinstance(app, QApplication):
            c = app.palette().color(QPalette.ColorRole.Window)
            return c.lightness() < 128
    except Exception:
        pass
    return False


def resolve_theme_mode(mode: str | None = None) -> str:
    """Return concrete 'light' or 'dark' from preference (including system)."""
    m = (mode or get_theme()).strip().lower()
    if m == THEME_SYSTEM:
        return THEME_DARK if system_prefers_dark() else THEME_LIGHT
    if m == THEME_DARK:
        return THEME_DARK
    return THEME_LIGHT


def _palette_from_tokens(t: dict[str, str]) -> QPalette:
    p = QPalette()
    window = QColor(t["paper"])
    base = QColor(t["sheet"])
    text = QColor(t["ink"])
    muted = QColor(t["ink_3"])
    disabled = QColor(t["rule_strong"])
    p.setColor(QPalette.ColorRole.Window, window)
    p.setColor(QPalette.ColorRole.WindowText, text)
    p.setColor(QPalette.ColorRole.Base, base)
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(t["sheet_2"]))
    p.setColor(QPalette.ColorRole.ToolTipBase, base)
    p.setColor(QPalette.ColorRole.ToolTipText, text)
    p.setColor(QPalette.ColorRole.Text, text)
    p.setColor(QPalette.ColorRole.Button, window)
    p.setColor(QPalette.ColorRole.ButtonText, text)
    p.setColor(QPalette.ColorRole.BrightText, QColor("#ffffff"))
    p.setColor(QPalette.ColorRole.Highlight, QColor(t["accent"]))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(t["on_accent"]))
    p.setColor(QPalette.ColorRole.Link, QColor(t["accent"]))
    p.setColor(QPalette.ColorRole.PlaceholderText, muted)
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, disabled)
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, disabled)
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, disabled)
    return p


def apply_theme(app: QCoreApplication | None = None, mode: str | None = None) -> str:
    """
    Apply palette + stylesheet for the given preference.

    Returns the concrete theme used (``light`` or ``dark``).
    """
    global _active_concrete
    if app is None:
        app = QApplication.instance()
    concrete = resolve_theme_mode(mode)
    _active_concrete = concrete
    palette = tokens(concrete)
    sheet = _build_stylesheet(palette)
    if isinstance(app, QApplication):
        install_app_font(app)
        app.setStyle("Fusion")
        app.setPalette(_palette_from_tokens(palette))
        app.setStyleSheet(sheet)
    return concrete
