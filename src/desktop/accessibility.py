"""Announce completed actions without moving keyboard focus."""

from __future__ import annotations

from PySide6.QtGui import QAccessible, QAccessibleAnnouncementEvent, QAccessibleValueChangeEvent
from PySide6.QtWidgets import QAccessibleWidget, QComboBox, QWidget


def announce(widget: QWidget, message: str) -> None:
    if not message or widget.property("lastAnnouncement") == message:
        return
    widget.setProperty("lastAnnouncement", message)
    event = QAccessibleAnnouncementEvent(widget, message)
    event.setPoliteness(QAccessible.AnnouncementPoliteness.Polite)
    QAccessible.updateAccessibility(event)


_COMBO_INTERFACES: dict[int, QAccessibleWidget] = {}
_COMBO_FACTORY_INSTALLED = False


def notify_purpose_combo_value(combo: QComboBox) -> None:
    """Publish the current choice. The accessible name stays the purpose."""
    if not combo.property("purposeName"):
        return
    QAccessible.updateAccessibility(QAccessibleValueChangeEvent(combo, combo.currentText()))


class PurposeComboAccessible(QAccessibleWidget):
    """Purpose is the name. The current choice is the value. Qt otherwise publishes only the item."""

    def text(self, kind: QAccessible.Text) -> str:
        widget = self.widget()
        if isinstance(widget, QComboBox):
            if kind == QAccessible.Text.Name:
                purpose = widget.property("purposeName")
                if isinstance(purpose, str) and purpose:
                    return purpose
                return widget.accessibleName()
            if kind == QAccessible.Text.Value:
                return widget.currentText()
        return super().text(kind)

    def state(self) -> QAccessible.State:
        state = super().state()
        state.expandable = True
        widget = self.widget()
        view = widget.view() if isinstance(widget, QComboBox) else None
        state.expanded = bool(view is not None and view.isVisible())
        return state

    def actionNames(self) -> list[str]:
        return [self.showMenuAction(), self.pressAction()]

    def doAction(self, action: str) -> None:
        widget = self.widget()
        if isinstance(widget, QComboBox) and action in (self.showMenuAction(), self.pressAction()):
            widget.showPopup()
            return
        super().doAction(action)

    def childCount(self) -> int:
        widget = self.widget()
        return 1 if isinstance(widget, QComboBox) and widget.view() is not None else 0

    def child(self, index: int):
        widget = self.widget()
        if index != 0 or not isinstance(widget, QComboBox) or widget.view() is None:
            return None
        return QAccessible.queryAccessibleInterface(widget.view())

    def indexOfChild(self, child) -> int:
        widget = self.widget()
        if child is None or not isinstance(widget, QComboBox) or widget.view() is None:
            return -1
        return 0 if child.object() is widget.view() else -1


def _combo_factory(classname: str, obj: QWidget):
    if classname != "QComboBox" or not isinstance(obj, QComboBox) or not obj.property("purposeName"):
        return None
    iface = PurposeComboAccessible(obj, QAccessible.Role.ComboBox)
    _COMBO_INTERFACES[id(obj)] = iface
    return iface


def _install_combo_factory() -> None:
    global _COMBO_FACTORY_INSTALLED
    if _COMBO_FACTORY_INSTALLED:
        return
    QAccessible.installFactory(_combo_factory)
    _COMBO_FACTORY_INSTALLED = True


def name_combo(combo: QComboBox, purpose: str) -> None:
    """Publish a purpose-first name. Qt's combo interface ignores accessibleName once an item exists."""
    _install_combo_factory()
    combo.setProperty("purposeName", purpose)
    combo.setAccessibleName(purpose)
    combo.currentTextChanged.connect(lambda _text, box=combo: notify_purpose_combo_value(box))
    current = QAccessible.queryAccessibleInterface(combo)
    if not isinstance(current, PurposeComboAccessible):
        if current is not None:
            QAccessible.deleteAccessibleInterface(QAccessible.uniqueId(current))
        current = QAccessible.queryAccessibleInterface(combo)
    assert isinstance(current, PurposeComboAccessible)
    combo_key = id(combo)
    combo.destroyed.connect(lambda _obj=None, key=combo_key: _COMBO_INTERFACES.pop(key, None))
    _COMBO_INTERFACES[combo_key] = current
