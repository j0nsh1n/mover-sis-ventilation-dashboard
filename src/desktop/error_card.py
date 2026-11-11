"""One shared way to show a failure: what failed, why in plain words, what to do next, and the technical detail
collapsed where it can be selected and copied.

It is a ``QMessageBox`` so the standard button, the details area and the focus return keep working. Nothing here
rewords an error: ``why`` is the message the caller already had, and the detail is only the code and the log place.
"""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QMessageBox, QWidget

from src.desktop.errors import log_path
from src.desktop.marks import severity_label
from src.desktop.theme import token_color

# error_message() puts its code in parentheses: (load:ValueError)
_CODE = re.compile(r"\(([A-Za-z0-9_-]+:[A-Za-z0-9_]+)\)")


def _with_home_as_tilde(path: Path) -> str:
    """The path with the home folder written ``~``, so the account name is not on screen or in a copy."""
    try:
        return f"~/{path.relative_to(Path.home()).as_posix()}"
    except ValueError:
        return str(path)


def failure_detail(message: str) -> str:
    """The technical detail that ``message`` carries: its error code and the local log, when it names them."""
    lines: list[str] = []
    code = _CODE.search(message)
    if code:
        lines.append(f"Code: {code.group(1)}")
    if "app-errors.log" in message:
        lines.append(f"Log: {_with_home_as_tilde(log_path())}")
    return "\n".join(lines)


# How a card's level is written: the rule flags' shape and word (marks.py), never colour alone.
_LEVEL_SEVERITY = {"critical": "critical", "warning": "warn"}


def _glyph(severity: str, px: int = 40) -> QPixmap:
    """The level's shape over its word: a solid diamond for a failure, an outlined triangle for a warning."""
    screen_ratio = 2.0
    word_px = 16
    pixmap = QPixmap(round(px * screen_ratio * 1.6), round((px + word_px) * screen_ratio))
    pixmap.setDevicePixelRatio(screen_ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    ink = token_color("ink_2")
    pen = QPen(ink, 1.8)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    unit = px / 24.0
    left = (px * 1.6 - px) / 2
    painter.translate(left, 0)
    if severity == "critical":
        painter.setBrush(ink)
        diamond = QPainterPath()
        diamond.moveTo(QPointF(12 * unit, 2.5 * unit))
        diamond.lineTo(QPointF(21.5 * unit, 12 * unit))
        diamond.lineTo(QPointF(12 * unit, 21.5 * unit))
        diamond.lineTo(QPointF(2.5 * unit, 12 * unit))
        diamond.closeSubpath()
        painter.drawPath(diamond)
    else:
        path = QPainterPath()
        path.moveTo(QPointF(12 * unit, 3.5 * unit))
        path.lineTo(QPointF(21.5 * unit, 20 * unit))
        path.lineTo(QPointF(2.5 * unit, 20 * unit))
        path.closeSubpath()
        painter.drawPath(path)
        painter.drawLine(QPointF(12 * unit, 9.5 * unit), QPointF(12 * unit, 14.5 * unit))
        painter.drawPoint(QPointF(12 * unit, 17.2 * unit))
    painter.translate(-left, 0)
    font = painter.font()
    font.setPixelSize(12)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(QRectF(0, px, px * 1.6, word_px), Qt.AlignmentFlag.AlignCenter, severity)
    painter.end()
    return pixmap


class ErrorCard(QMessageBox):
    """A failure. The headline says what failed; the lines under it say why and what to do next."""

    def __init__(self, parent: QWidget | None, *, what: str, why: str = "", next_step: str = "",
                 detail: str | None = None, title: str = "Something did not work", level: str = "warning"):
        super().__init__(parent)
        # "critical" when the work the person asked for was lost, "warning" when it could carry on
        self.level = level
        self.setObjectName("errorCard")
        self.setWindowTitle(title)
        self.setIcon(QMessageBox.Icon.Warning)
        severity = _LEVEL_SEVERITY.get(level, "warn")
        # The same shape and word the rule flags use, spoken too since the picture is not read out
        self.mark = severity_label(severity)
        self.setIconPixmap(_glyph(severity))
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setText(what)
        self.why, self.next_step = why, next_step
        self.setInformativeText("\n\n".join(part for part in (why, next_step) if part))
        technical = failure_detail(why) if detail is None else detail
        if technical:
            self.setDetailedText(technical)
        self.setStandardButtons(QMessageBox.StandardButton.Ok)
        self.setDefaultButton(QMessageBox.StandardButton.Ok)
        self.setAccessibleName(what)
        self.setAccessibleDescription(f"{self.mark}. {self.words()}")

    def words(self) -> str:
        """Everything the card says outside its collapsed detail."""
        return "\n\n".join(part for part in (self.text(), self.why, self.next_step) if part)

    def has_detail(self) -> bool:
        return bool(self.detailedText())


def show_failure(parent: QWidget | None, *, what: str, why: str = "", next_step: str = "",
                 detail: str | None = None, title: str = "Something did not work", level: str = "critical") -> int:
    """Show a failure and wait for it to be closed (the way ``QMessageBox.critical`` did)."""
    return ErrorCard(parent, what=what, why=why, next_step=next_step, detail=detail, title=title, level=level).exec()


__all__ = ["ErrorCard", "failure_detail", "show_failure"]
