"""Application themes: light, dark, and follow-system."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

from src.user_settings import THEME_DARK, THEME_LIGHT, THEME_SYSTEM, get_theme

# Concrete theme last applied ("light" | "dark") — used by matplotlib charts.
_active_concrete: str = THEME_LIGHT

# ---------------------------------------------------------------------------
# Shared stylesheet fragments (structure only; colors injected per theme)
# ---------------------------------------------------------------------------

_STRUCTURE = """
/* —— shell —— */
QMainWindow {{
    background: {bg};
    color: {fg};
}}
QWidget {{
    color: {fg};
    font-size: 13px;
    font-family: "Inter", "Segoe UI", "Ubuntu", "Noto Sans", sans-serif;
}}
QToolTip {{
    background: {surface};
    color: {fg};
    border: 1px solid {border};
    padding: 6px 8px;
    border-radius: 2px;
}}

/* —— menu —— */
QMenuBar {{
    background: {surface};
    color: {fg};
    border-bottom: 1px solid {border};
    padding: 2px 4px;
    spacing: 2px;
}}
QMenuBar::item {{
    background: transparent;
    padding: 6px 12px;
    border-radius: 2px;
}}
QMenuBar::item:selected {{
    background: {hover};
}}
QMenu {{
    background: {surface};
    color: {fg};
    border: 1px solid {border};
    border-radius: 2px;
    padding: 4px;
}}
QMenu::item {{
    padding: 7px 28px 7px 12px;
    border-radius: 2px;
}}
QMenu::item:selected {{
    background: {hover};
}}
QMenu::separator {{
    height: 1px;
    background: {border};
    margin: 6px 8px;
}}

/* —— cards / group boxes ——
   Title sits on the top border: need margin-top + title background
   matching the surface so the border does not cut through the text. */
QGroupBox {{
    background: {surface};
    border: 1px solid {border};
    border-radius: 2px;
    margin-top: 1.15em;
    padding-top: 14px;
    padding-left: 12px;
    padding-right: 12px;
    padding-bottom: 10px;
    font-weight: 600;
    color: {fg};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    top: 0px;
    padding: 0 6px;
    color: {muted};
    background-color: {bg};
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.03em;
}}

/* —— inputs —— */
QLineEdit, QSpinBox, QComboBox, QTextEdit, QPlainTextEdit {{
    background: {input_bg};
    border: 1px solid {border};
    border-radius: 2px;
    padding: 6px 10px;
    min-height: 26px;
    color: {fg};
    selection-background-color: {accent_soft};
    selection-color: {fg};
}}
QLineEdit:hover, QSpinBox:hover, QComboBox:hover, QTextEdit:hover {{
    border-color: {border_strong};
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QTextEdit:focus {{
    border: 1px solid {accent};
    background: {input_focus};
}}
QComboBox {{
    padding-right: 28px;
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 28px;
    border: none;
    border-left: 1px solid {border};
    background: {hover};
}}
QComboBox::drop-down:hover {{
    background: {border_strong};
}}
QComboBox::down-arrow {{
    /* CSS triangle — visible without image assets */
    image: none;
    width: 0px;
    height: 0px;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid {muted};
    margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background: {surface};
    color: {fg};
    border: 1px solid {border};
    selection-background-color: {hover};
    outline: none;
    padding: 4px;
}}
QSpinBox::up-button, QSpinBox::down-button {{
    background: transparent;
    border: none;
    width: 16px;
}}

/* —— buttons —— */
QPushButton {{
    background: {btn};
    color: {btn_fg};
    border: 1px solid {btn_border};
    border-radius: 2px;
    padding: 8px 14px;
    font-weight: 600;
    min-height: 18px;
}}
QPushButton:hover {{
    background: {btn_hover};
}}
QPushButton:pressed {{
    background: {btn_pressed};
}}
QPushButton:disabled {{
    background: {disabled_bg};
    color: {disabled_fg};
    border-color: {border};
}}
QPushButton#secondaryBtn {{
    background: {surface};
    color: {fg};
    border: 1px solid {border};
}}
QPushButton#secondaryBtn:hover {{
    background: {hover};
    border-color: {border_strong};
}}
QPushButton#primaryBtn {{
    background: {accent};
    color: {accent_fg};
    border: 1px solid {accent};
}}
QPushButton#primaryBtn:hover {{
    background: {accent_hover};
    border-color: {accent_hover};
}}
QPushButton#primaryBtn:pressed {{
    background: {accent_pressed};
}}

/* —— tabs —— */
QTabWidget::pane {{
    border: 1px solid {border};
    border-radius: 2px;
    background: {surface};
    top: -1px;
    padding: 4px;
}}
QTabBar::tab {{
    background: transparent;
    border: 1px solid transparent;
    border-bottom: none;
    border-top-left-radius: 2px;
    border-top-right-radius: 2px;
    padding: 9px 16px;
    margin-right: 2px;
    color: {muted};
    font-weight: 500;
}}
QTabBar::tab:hover {{
    background: {hover};
    color: {fg};
}}
QTabBar::tab:selected {{
    background: {surface};
    border: 1px solid {border};
    border-bottom: 1px solid {surface};
    color: {fg};
    font-weight: 700;
}}

/* —— tables —— */
QTableWidget {{
    background: {input_bg};
    gridline-color: {border};
    border: 1px solid {border};
    border-radius: 2px;
    alternate-background-color: {alt_row};
    color: {fg};
    selection-background-color: {accent_soft};
    selection-color: {fg};
    outline: none;
}}
QTableWidget::item {{
    padding: 4px 8px;
}}
QTableWidget::item:selected {{
    background: {accent_soft};
}}
QHeaderView::section {{
    background: {surface};
    padding: 8px 10px;
    border: none;
    border-right: 1px solid {border};
    border-bottom: 1px solid {border};
    font-weight: 700;
    font-size: 11px;
    color: {muted};
}}
QTableCornerButton::section {{
    background: {surface};
    border: none;
    border-bottom: 1px solid {border};
    border-right: 1px solid {border};
}}

/* —— status —— */
QStatusBar {{
    background: {surface};
    color: {muted};
    border-top: 1px solid {border};
    padding: 2px 8px;
    font-size: 11px;
}}

/* —— labels —— */
QLabel#heroTitle {{
    font-size: 20px;
    font-weight: 700;
    color: {fg};
    letter-spacing: -0.02em;
}}
QLabel#heroSub {{
    color: {muted};
    font-size: 12px;
    padding-top: 2px;
}}
QLabel#pathHint {{
    color: {muted};
    font-size: 11px;
    line-height: 1.35;
}}
QLabel#sectionLabel {{
    font-weight: 700;
    font-size: 12px;
    color: {muted};
    margin-top: 4px;
    letter-spacing: 0.02em;
}}
QLabel#metricTitle {{
    color: {muted};
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.03em;
}}
QLabel#metricValue {{
    font-size: 18px;
    font-weight: 700;
    color: {fg};
    padding-top: 2px;
}}

/* —— metric cards —— */
QFrame#metricCard {{
    background: {surface};
    border: 1px solid {border};
    border-radius: 2px;
    border-top: 2px solid {accent};
    min-width: 108px;
}}

/* —— chart host —— */
QWidget#chartView {{
    background: {surface};
    border: 1px solid {border};
    border-radius: 2px;
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
    color: {fg};
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 2px;
    border: 1px solid {border_strong};
    background: {input_bg};
}}
QCheckBox::indicator:checked {{
    background: {accent};
    border-color: {accent};
}}

/* —— dialogs / wizard —— */
QDialog, QWizard {{
    background: {bg};
    color: {fg};
}}
QWizard QWidget {{
    color: {fg};
}}
QDialogButtonBox QPushButton {{
    min-width: 88px;
}}

/* —— splitters —— */
QSplitter::handle {{
    background: {border};
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
    background: {scroll};
    border-radius: 2px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{
    background: {scroll_hover};
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
    background: {scroll};
    border-radius: 2px;
    min-width: 28px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}
"""

_LIGHT = {
    "bg": "#f1f5f9",
    "surface": "#ffffff",
    "input_bg": "#ffffff",
    "input_focus": "#ffffff",
    "fg": "#0f172a",
    "muted": "#64748b",
    "border": "#e2e8f0",
    "border_strong": "#cbd5e1",
    "hover": "#f1f5f9",
    "alt_row": "#f8fafc",
    "accent": "#0d9488",
    "accent_hover": "#0f766e",
    "accent_pressed": "#115e59",
    "accent_fg": "#f0fdfa",
    "accent_soft": "#ccfbf1",
    "btn": "#e2e8f0",
    "btn_fg": "#1e293b",
    "btn_border": "#cbd5e1",
    "btn_hover": "#cbd5e1",
    "btn_pressed": "#94a3b8",
    "disabled_bg": "#f1f5f9",
    "disabled_fg": "#94a3b8",
    "scroll": "#cbd5e1",
    "scroll_hover": "#94a3b8",
}

_DARK = {
    "bg": "#0b1220",
    "surface": "#151e2e",
    "input_bg": "#0f172a",
    "input_focus": "#111827",
    "fg": "#e8eef7",
    "muted": "#94a3b8",
    "border": "#243044",
    "border_strong": "#334155",
    "hover": "#1a2436",
    "alt_row": "#121a29",
    "accent": "#14b8a6",
    "accent_hover": "#2dd4bf",
    "accent_pressed": "#0d9488",
    "accent_fg": "#042f2e",
    "accent_soft": "#134e4a",
    "btn": "#1e293b",
    "btn_fg": "#f1f5f9",
    "btn_border": "#334155",
    "btn_hover": "#334155",
    "btn_pressed": "#475569",
    "disabled_bg": "#151e2e",
    "disabled_fg": "#475569",
    "scroll": "#334155",
    "scroll_hover": "#475569",
}


def _build_stylesheet(tokens: dict[str, str]) -> str:
    return _STRUCTURE.format(**tokens)


LIGHT_STYLESHEET = _build_stylesheet(_LIGHT)
DARK_STYLESHEET = _build_stylesheet(_DARK)


def active_theme() -> str:
    """Return concrete theme last applied (``light`` or ``dark``)."""
    return _active_concrete


def chart_style() -> dict[str, str]:
    """Colors for matplotlib figures matching the active UI theme."""
    if _active_concrete == THEME_DARK:
        return {
            "figure_facecolor": _DARK["surface"],
            "axes_facecolor": _DARK["input_bg"],
            "text": _DARK["fg"],
            "muted": _DARK["muted"],
            "grid": _DARK["border"],
            "spine": _DARK["border_strong"],
            "bar": "#f97316",
            "info": "#60a5fa",
            "warn": "#fbbf24",
            "critical": "#f87171",
        }
    return {
        "figure_facecolor": _LIGHT["surface"],
        "axes_facecolor": "#ffffff",
        "text": _LIGHT["fg"],
        "muted": _LIGHT["muted"],
        "grid": _LIGHT["border"],
        "spine": _LIGHT["border_strong"],
        "bar": "#ea580c",
        "info": "#3b82f6",
        "warn": "#d97706",
        "critical": "#dc2626",
    }


def chat_role_color(role: str) -> str:
    """HTML color for chat role labels under the active theme."""
    dark = _active_concrete == THEME_DARK
    if role == "user":
        return "#2dd4bf" if dark else "#0f766e"
    return "#94a3b8" if dark else "#475569"


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
        if app is not None:
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
    window = QColor(t["bg"])
    base = QColor(t["input_bg"])
    alt = QColor(t["surface"])
    text = QColor(t["fg"])
    muted = QColor(t["muted"])
    highlight = QColor(t["accent"])
    button = QColor(t["btn"])
    disabled = QColor(t["disabled_fg"])
    p.setColor(QPalette.ColorRole.Window, window)
    p.setColor(QPalette.ColorRole.WindowText, text)
    p.setColor(QPalette.ColorRole.Base, base)
    p.setColor(QPalette.ColorRole.AlternateBase, alt)
    p.setColor(QPalette.ColorRole.ToolTipBase, alt)
    p.setColor(QPalette.ColorRole.ToolTipText, text)
    p.setColor(QPalette.ColorRole.Text, text)
    p.setColor(QPalette.ColorRole.Button, button)
    p.setColor(QPalette.ColorRole.ButtonText, text)
    p.setColor(QPalette.ColorRole.BrightText, QColor("#ffffff"))
    p.setColor(QPalette.ColorRole.Highlight, highlight)
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(t["accent_fg"]))
    p.setColor(QPalette.ColorRole.Link, QColor(t["accent"]))
    p.setColor(QPalette.ColorRole.PlaceholderText, muted)
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, disabled)
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, disabled)
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, disabled)
    return p


def apply_theme(app: QApplication | None = None, mode: str | None = None) -> str:
    """
    Apply palette + stylesheet for the given preference.

    Returns the concrete theme used (``light`` or ``dark``).
    """
    global _active_concrete
    app = app or QApplication.instance()
    concrete = resolve_theme_mode(mode)
    _active_concrete = concrete
    tokens = _DARK if concrete == THEME_DARK else _LIGHT
    sheet = _build_stylesheet(tokens)
    if app is not None:
        app.setStyle("Fusion")
        app.setPalette(_palette_from_tokens(tokens))
        app.setStyleSheet(sheet)
    return concrete
