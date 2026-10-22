"""Announce completed actions without moving keyboard focus."""

from __future__ import annotations

from PySide6.QtGui import QAccessible, QAccessibleAnnouncementEvent
from PySide6.QtWidgets import QWidget


def announce(widget: QWidget, message: str) -> None:
    if not message or widget.property("lastAnnouncement") == message:
        return
    widget.setProperty("lastAnnouncement", message)
    event = QAccessibleAnnouncementEvent(widget, message)
    event.setPoliteness(QAccessible.AnnouncementPoliteness.Polite)
    QAccessible.updateAccessibility(event)
