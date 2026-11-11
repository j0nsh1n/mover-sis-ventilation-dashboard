"""About MOVER Suite: what it is, what it is not, and where its parts come from. Every line is a fact the app knows."""

from __future__ import annotations

import platform

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.desktop.accessibility import announce
from src.desktop.fonts import FONT_DIR
from src.runtime_paths import demo_data_in_use

PRODUCT = "MOVER Suite"
SUMMARY = (
    "A desktop workspace for studying the de-identified MOVER SIS anesthesia dataset, with fictional "
    "practice pages for teaching handoffs, medication timelines and checklists."
)
LICENSE_LINE = "GNU General Public License v3.0 or later"
FONT_CREDITS = (
    "Inter, © 2016 The Inter Project Authors, SIL Open Font License 1.1",
    "Charter, © 1989–1992 Bitstream Inc., free license kept with the font files",
)
DATA_FACT = "MOVER SIS export, read from your own folders. Nothing is uploaded."
DEMO_DATA_FACT = "Fictional demo data generated on this computer. These are not patient records."


def facts(demo: bool) -> tuple[tuple[str, str], ...]:
    """The first rows of About. The Data row says what the app is reading now, so it follows the data in use."""
    return (
        ("Use", "Research and education only. Not a medical device. Not for patient care or decisions."),
        ("Data", DEMO_DATA_FACT if demo else DATA_FACT),
        ("AI", "Optional local models through Ollama, on this computer. Answers cite the source rows they read."),
    )


LICENSE_FILES = ("Inter-LICENSE.txt", "Charter-NOTICE.txt")


def build_line(rules: str | None) -> str:
    """The Build row: the toolkit and language the app is running on, and the rule preset when one is loaded."""
    from PySide6 import __version__ as pyside_version

    parts = [f"PySide6 {pyside_version}", f"Python {platform.python_version()}"]
    if rules:
        parts.append(f"rules {rules}")
    return " · ".join(parts)


def version_details(version: str, rules: str | None) -> str:
    lines = [f"{PRODUCT} version {version}", f"License: {LICENSE_LINE}", f"Build: {build_line(rules)}",
             "Fonts:", *(f"  {credit}" for credit in FONT_CREDITS)]
    return "\n".join(lines)


def licence_text() -> str:
    """The font license files shipped with the app, whole, under the app's own license line."""
    chunks = [f"{PRODUCT} is licensed under the {LICENSE_LINE}. The full text is the LICENSE file of the source."]
    for name in LICENSE_FILES:
        try:
            chunks.append(f"{name}\n\n{(FONT_DIR / name).read_text(encoding='utf-8').strip()}")
        except OSError:
            chunks.append(f"{name}\n\nThis file could not be read from the app's folder.")
    return "\n\n" + ("\n\n" + "-" * 60 + "\n\n").join(chunks) + "\n"


class AboutDialog(QDialog):
    def __init__(self, version: str, rules: str | None = None, parent: QWidget | None = None,
                 *, demo: bool | None = None):
        super().__init__(parent)
        self._version = version
        self._rules = rules
        self.setObjectName("aboutDialog")
        self.setWindowTitle(f"About {PRODUCT}")
        self.setModal(True)
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 32, 36, 24)
        layout.setSpacing(20)

        head = QHBoxLayout()
        head.setSpacing(18)
        mark = QLabel("M")
        mark.setObjectName("aboutMark")
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setFixedSize(54, 54)
        mark.setAccessibleName(PRODUCT)
        names = QVBoxLayout()
        names.setSpacing(0)
        title = QLabel(PRODUCT)
        title.setObjectName("pageTitle")
        title.setAccessibleName(PRODUCT)
        number = QLabel(f"version {version}")
        number.setObjectName("quietText")
        names.addWidget(title)
        names.addWidget(number)
        head.addWidget(mark)
        head.addLayout(names, 1)
        layout.addLayout(head)

        summary = QLabel(SUMMARY)
        summary.setObjectName("bodyText")
        summary.setWordWrap(True)
        layout.addWidget(summary)

        rule = QFrame()
        rule.setObjectName("turnRule")
        rule.setFixedHeight(1)
        layout.addWidget(rule)

        grid = QGridLayout()
        grid.setHorizontalSpacing(28)
        grid.setVerticalSpacing(12)
        rows = [*facts(demo_data_in_use() if demo is None else demo), ("Build", build_line(rules)),
                ("License", LICENSE_LINE), ("Fonts", "\n".join(FONT_CREDITS))]
        for row, (key, value) in enumerate(rows):
            label = QLabel(key)
            label.setObjectName("quietText")
            label.setAlignment(Qt.AlignmentFlag.AlignTop)
            text = QLabel(value)
            text.setObjectName("bodyText")
            text.setWordWrap(True)
            text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            text.setAccessibleName(f"{key}: {value}")
            grid.addWidget(label, row, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(text, row, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)

        buttons = QHBoxLayout()
        buttons.setSpacing(12)
        self.copy_button = QPushButton("Copy version details")
        self.copy_button.setObjectName("textBtn")
        self.copy_button.clicked.connect(self.copy_details)
        self.licenses_button = QPushButton("Licenses")
        self.licenses_button.clicked.connect(self.show_licenses)
        self.close_button = QPushButton("Close")
        self.close_button.setObjectName("primaryBtn")
        self.close_button.setDefault(True)
        self.close_button.clicked.connect(self.accept)
        buttons.addWidget(self.copy_button)
        buttons.addStretch(1)
        buttons.addWidget(self.licenses_button)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)
        self.close_button.setFocus()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # Wrapped lines need the height for the width the dialog really has, or the last row is cut off
        layout = self.layout()
        if layout is not None:
            layout.activate()
            self.resize(self.width(), max(self.height(), layout.totalHeightForWidth(self.width())))

    def copy_details(self) -> None:
        QGuiApplication.clipboard().setText(version_details(self._version, self._rules))
        announce(self.copy_button, "Version details copied")

    def show_licenses(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Licenses")
        dialog.resize(640, 480)
        box = QVBoxLayout(dialog)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setAccessibleName("Licenses")
        text.setPlainText(licence_text())
        box.addWidget(text)
        close = QPushButton("Close")
        close.setObjectName("primaryBtn")
        close.clicked.connect(dialog.accept)
        box.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
        dialog.exec()
